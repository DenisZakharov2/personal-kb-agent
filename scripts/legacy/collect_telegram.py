#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборщик постов из Telegram-каналов (этап 1 двухэтапного сбора).

Читает публичные страницы каналов t.me/s/<channel>, извлекает текст постов,
дату и ссылку, определяет спам по ключевым словам и сохраняет сырые записи
в _queue.jsonl для последующего анализа Hermes Agent (этап 2).

Переменные окружения:
    KB_TELEGRAM_CHANNELS  — каналы через запятую (по умолчанию — встроенный список)
    KB_LIMIT              — постов с каждого канала за запуск (по умолчанию 10)
    KB_VAULT_PATH         — путь к Obsidian vault (для каталога)
    KB_LOG_PATH           — путь к лог-файлу (опционально)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import requests
from bs4 import BeautifulSoup
from pathlib import Path

DEFAULT_CHANNELS = ["ragnarokzone", "paragonzone", "rubiconzone"]
DEFAULT_LIMIT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

SPAM_KEYWORDS = [
    "розыгрыш", "giveaway", "подпишись на канал", "подписаться на",
    "реклама", "вложения", "заработай", "инвестируй", "купи",
    "промокод", "скидка", "розыгрыш приза", "бесплатный розыгрыш",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Telegram -> KB collector (stage 1)")
    p.add_argument("--limit", type=int, default=int(os.getenv("KB_LIMIT", str(DEFAULT_LIMIT))))
    channels_env = os.getenv("KB_TELEGRAM_CHANNELS", "")
    default_channels = [c.strip() for c in channels_env.split(",") if c.strip()] or DEFAULT_CHANNELS
    p.add_argument("--channels", nargs="*", default=default_channels)
    return p.parse_args()


def is_spam(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SPAM_KEYWORDS)


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    return BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)


def extract_message_id(link: str) -> str:
    if not link:
        return ""
    m = re.search(r"/(\d+)$", link)
    return m.group(1) if m else ""


def post_key(channel: str, message_id: str) -> str:
    return f"{channel}_{message_id}"


def fetch_channel_posts(channel: str, limit: int) -> list[dict]:
    url = f"https://t.me/s/{channel}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  Не удалось открыть {channel} (статус {r.status_code})")
            return []
    except Exception as e:
        print(f"  Ошибка при запросе {channel}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    messages = soup.select(".tgme_widget_message")[:limit]

    posts: list[dict] = []
    for msg in messages:
        text_el = msg.select_one(".tgme_widget_message_text")
        text = clean_html(str(text_el)) if text_el else ""
        if not text:
            continue

        time_el = msg.select_one("time")
        date = time_el.get("datetime") if time_el else ""

        link_el = msg.select_one(".tgme_widget_message_date")
        link = link_el.get("href") if link_el else url
        if link and link.startswith("/"):
            link = f"https://t.me{link}"

        message_id = extract_message_id(link)
        if not message_id:
            data_post = msg.get("data-post", "")
            parts = data_post.split()
            if len(parts) == 2:
                message_id = parts[1]
        if not message_id:
            continue

        posts.append({
            "channel": channel,
            "message_id": message_id,
            "date": date,
            "link": link,
            "text": text,
            "is_spam": is_spam(text),
        })
    return posts


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    queue_file = script_dir / "_queue.jsonl"
    done_file = script_dir / "_done.txt"
    state_file = script_dir / "processed.txt"

    def load_set(path: Path) -> set[str]:
        if path.exists():
            return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
        return set()

    def save_set(path: Path, s: set[str]) -> None:
        path.write_text("\n".join(sorted(s)) + "\n", encoding="utf-8")

    done = load_set(done_file)
    processed = load_set(state_file)

    total_new = 0
    for channel in args.channels:
        print(f"\nКанал: {channel}")
        posts = fetch_channel_posts(channel, args.limit)
        if not posts:
            print("  Посты не найдены")
            continue

        new_for_channel = 0
        for p in posts:
            key = post_key(p["channel"], p["message_id"])
            if key in done or key in processed:
                continue
            with queue_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
            processed.add(key)
            new_for_channel += 1
            total_new += 1

        print(f"  Новых в канале: {new_for_channel}")

    save_set(state_file, processed)
    print(f"\nДобавлено в очередь: {total_new}")
    print(f"Очередь: {queue_file}  |  Готово: {done_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
