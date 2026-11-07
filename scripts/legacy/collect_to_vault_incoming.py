"""
Сборщик постов из Telegram-каналов.
Сохраняет новые посты в папку "Входящие" для последующей обработки агентом.
"""

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# =============== ПУТИ ===============
VAULT_ROOT = Path("/home/anony/Documents/Личное/База знаний/База знаний из ТГК")
INCOMING_DIR = VAULT_ROOT / "Входящие"
CATALOG_DIR = VAULT_ROOT / "Каталог"
SCRIPT_DIR = VAULT_ROOT / "Скрипт"
STATE_FILE = SCRIPT_DIR / "processed.txt"
PENDING_FILE = SCRIPT_DIR / "pending_for_agent.md"

# Каналы для мониторинга
CHANNELS = [
    "ragnarokzone",
    "paragonzone",
    "rubiconzone",
]

LIMIT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}
# ====================================


def load_processed():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    return set()


def save_processed(processed):
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.writelines(key + "\n" for key in sorted(processed))


def clean_html(raw_html):
    if not raw_html:
        return ""
    return BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)


def extract_message_id(link):
    if not link:
        return ""
    match = re.search(r"/(\d+)$", link)
    return match.group(1) if match else ""


def save_raw_post(channel, message_id, date, link, text):
    """Сохраняет сырой пост в папку Входящие."""
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    safe_date = date.replace(":", "-").replace("+", "_")[:16] if date else "unknown"
    safe_channel = re.sub(r"[^a-zA-Z0-9_-]", "_", channel)
    filename = f"{safe_date}_{safe_channel}_{message_id}.md"
    filepath = INCOMING_DIR / filename

    content = f"""# {channel} — пост {message_id}

**Канал:** {channel}
**ID поста:** {message_id}
**Дата:** {date}
**Ссылка:** {link}

## Исходный текст
{text}

---
*Ожидает обработки агентом.*
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath, content


def append_pending(channel, message_id, date, link, text):
    """Добавляет пост в файл pending_for_agent.md для передачи агенту."""
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    with open(PENDING_FILE, "a", encoding="utf-8") as f:
        f.write(f"## {channel} — пост {message_id}\n")
        f.write(f"**Дата:** {date}\n")
        f.write(f"**Ссылка:** {link}\n\n")
        f.write("Текст:\n")
        f.write(text + "\n\n")
        f.write("---\n\n")


def main():
    processed = load_processed()
    new_posts = []

    print(f"Сбор постов из {len(CHANNELS)} каналов...")
    print(f"Папка назначения: {INCOMING_DIR}")
    print()

    for channel in CHANNELS:
        url = f"https://t.me/s/{channel}"
        print(f"Канал: {channel}")

        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"  Ошибка HTTP {r.status_code}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            messages = soup.select(".tgme_widget_message")[:LIMIT]

            if not messages:
                print("  Посты не найдены на странице")
                continue

            channel_new = 0
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

                post_key = f"{channel}_{message_id}"
                if post_key in processed:
                    continue

                filepath, _content = save_raw_post(channel, message_id, date, link, text)
                append_pending(channel, message_id, date, link, text)
                processed.add(post_key)
                new_posts.append(
                    {
                        "channel": channel,
                        "message_id": message_id,
                        "date": date,
                        "link": link,
                        "text": text,
                        "filepath": filepath.name,
                    }
                )
                channel_new += 1
                print(f"  + {message_id} -> {filepath.name}")

            print(f"  Новых в канале: {channel_new}")

        except requests.RequestException as e:
            print(f"  Ошибка: {e}")

    save_processed(processed)

    if new_posts:
        print(f"\nИтого новых постов: {len(new_posts)}")
        print(f"Файл для агента обновлён: {PENDING_FILE}")
    else:
        print("\nНовых постов нет.")
        if PENDING_FILE.exists():
            PENDING_FILE.unlink()

    print(f"Всего собрано: {len(processed)}")


if __name__ == "__main__":
    main()
