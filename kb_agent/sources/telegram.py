"""Сборщик постов из Telegram-каналов (Этап 2).

Логика перенесена из scripts/legacy/collect_telegram.py, но запись теперь ведётся
в SQLite-таблицу raw_queue вместо файла _queue.jsonl.
"""

from __future__ import annotations

import hashlib
import re

import httpx
from bs4 import BeautifulSoup

from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.telegram")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

SPAM_KEYWORDS = [
    "розыгрыш",
    "giveaway",
    "подпишись на канал",
    "подписаться на",
    "реклама",
    "вложения",
    "заработай",
    "инвестируй",
    "купи",
    "промокод",
    "скидка",
    "розыгрыш приза",
    "бесплатный розыгрыш",
]


def _extract_message_id(link: str) -> str:
    if not link:
        return ""
    m = re.search(r"/(\d+)$", link)
    return m.group(1) if m else ""


def _clean_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    return BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)


def _is_spam(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SPAM_KEYWORDS)


def fetch_channel_posts(channel: str, limit: int) -> list[dict]:
    """Скачивает посты из публичного канала t.me/s/<channel>."""
    url = f"https://t.me/s/{channel}"
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(url, headers=HEADERS)
            if r.status_code != 200:
                raise RuntimeError(f"Не удалось открыть {channel} (статус {r.status_code})")
    except httpx.RequestError as e:
        raise RuntimeError(f"Ошибка при запросе {channel}: {e}")

    soup = BeautifulSoup(r.text, "html.parser")
    # Сообщения на странице идут от старых к новым — берём последние limit
    messages = soup.select(".tgme_widget_message")
    if limit > 0:
        messages = messages[-limit:]
    # Если сообщений меньше limit, берём все
    return messages

    posts: list[dict] = []
    for msg in messages:
        text_el = msg.select_one(".tgme_widget_message_text")
        text = _clean_html(str(text_el)) if text_el else ""
        # Пропускаем посты без текста (только медиа)
        if not text.strip():
            continue

        time_el = msg.select_one("time")
        date = time_el.get("datetime") if time_el else ""

        link_el = msg.select_one(".tgme_widget_message_date")
        link = link_el.get("href") if link_el else url
        if link and link.startswith("/"):
            link = f"https://t.me{link}"

        message_id = _extract_message_id(link)
        if not message_id:
            data_post = msg.get("data-post", "")
            parts = data_post.split()
            if len(parts) == 2:
                message_id = parts[1]
        if not message_id:
            continue

        posts.append(
            {
                "channel": channel,
                "message_id": message_id,
                "date": date,
                "link": link,
                "text": text,
                "is_spam": _is_spam(text),
            }
        )
    return posts


def collect_telegram_source(
    state: StateManager,
    source_id: str,
    channel: str,
    limit: int,
) -> dict:
    """Собирает посты из одного Telegram-канала и пишет в raw_queue.

    channel может быть как сокращённым именем (rubiconzone), так и полным URL
    (https://t.me/s/rubiconzone).
    """
    return _collect_one_channel(state, channel, limit, source_id=source_id)


def _extract_channel_name(channel: str) -> str:
    """Извлекает имя канала из URL или возвращает как есть."""
    channel = channel.strip()
    if channel.startswith("http"):
        # https://t.me/s/ragnarokzone -> ragnarokzone
        parts = channel.rstrip("/").split("/")
        return parts[-1]
    return channel


def _collect_one_channel(
    state: StateManager,
    channel: str,
    limit: int,
    source_id: str | None = None,
) -> dict:
    """Внутренняя функция сбора одного канала."""
    channel_name = _extract_channel_name(channel)
    if source_id is None:
        source_id = f"tg_{channel_name}"

    logger.info("Канал Telegram: %s", channel_name)
    posts = fetch_channel_posts(channel_name, limit)
    if not posts:
        logger.info("  Посты не найдены")
        return {"source": "telegram", "new": 0, "spam": 0, "existing": 0}

    new_for_channel = 0
    spam_for_channel = 0
    existing_for_channel = 0

    for p in posts:
        external_id = f"{channel_name}_{p['message_id']}"
        raw_hash = hashlib.sha256((p["text"] + p["link"]).encode("utf-8")).hexdigest()

        # Спам — не пишем в очередь, только считаем
        if p["is_spam"]:
            spam_for_channel += 1
            continue

        # Проверка на déjà vu в raw_queue (по external_id)
        if state.is_processed(external_id):
            existing_for_channel += 1
            continue

        queue_id = state.enqueue(
            source_id=source_id,
            external_id=external_id,
            canonical_url=p["link"],
            title=p["text"][:100],
            raw_text=p["text"],
            raw_hash=raw_hash,
            published_at=p.get("date"),
        )
        if queue_id is None:
            # Запись уже была в очереди (конфликт external_id или raw_hash)
            existing_for_channel += 1
            continue
        new_for_channel += 1

    logger.info(
        "  Новых: %s, спама: %s, уже было: %s",
        new_for_channel,
        spam_for_channel,
        existing_for_channel,
    )

    return {
        "source": "telegram",
        "new": new_for_channel,
        "spam": spam_for_channel,
        "existing": existing_for_channel,
    }
