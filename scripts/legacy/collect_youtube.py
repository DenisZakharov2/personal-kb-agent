#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube -> KB collector (двухэтапный сбор с транскрипциями).

Этап 1 (скрипт): collect / transcripts
  collect  — скачивает метаданные видео канала через yt-dlp (без Shorts, без загрузки видео),
             сохраняет в _queue.jsonl.
  transcripts — скачивает автоматические субтитры (ru → en, формат json3) для видео в очереди,
             сохраняет в _transcripts/<VIDEO_ID>.txt.

Этап 2 (Hermes Agent вручную): анализирует очередь и пишет структурированные заметки KB
с YAML frontmatter и секциями: Суть, Ключевые идеи, Инструменты и ресурсы, Практические выводы.

Переменные окружения:
    KB_YOUTUBE_CHANNELS — список URL каналов через запятую (берётся первый)
    KB_YT_CHANNEL_URL   — (устарело) URL одного канала, вместо KB_YOUTUBE_CHANNELS
    KB_YT_MONTHS        — глубина бэклога в месяцах (по умолчанию 16)
    KB_YT_SUB_LANGS     — языки субтитров через запятую (по умолчанию ru,en)
    KB_YT_LIMIT         — макс. видео за запуск transcripts (0 = все)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_CHANNEL_URL = "https://youtube.com/@example_channel"
DEFAULT_MONTHS = 16
DEFAULT_SUB_LANGS = "ru,en"


def default_channel_url() -> str:
    """URL канала: KB_YOUTUBE_CHANNELS (первый) → KB_YT_CHANNEL_URL (устарело) → значение по умолчанию."""
    multiple = os.getenv("KB_YOUTUBE_CHANNELS", "").strip()
    if multiple:
        return multiple.split(",")[0].strip()
    single = os.getenv("KB_YT_CHANNEL_URL", "").strip()
    if single:
        import logging
        logging.getLogger("collect_youtube").warning(
            "KB_YT_CHANNEL_URL устарел, используйте KB_YOUTUBE_CHANNELS"
        )
        return single
    return DEFAULT_CHANNEL_URL


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YouTube -> KB collector (stage 1)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="собрать метаданные в очередь")
    c.add_argument("--channel-url", default=default_channel_url())
    c.add_argument("--months", type=int, default=int(os.getenv("KB_YT_MONTHS", str(DEFAULT_MONTHS))))

    t = sub.add_parser("transcripts", help="скачать субтитры для видео в очереди")
    t.add_argument("--limit", type=int, default=int(os.getenv("KB_YT_LIMIT", "0")))

    sub.add_parser("list-queue", help="показать необработанные записи")
    d = sub.add_parser("done", help="пометить видео обработанным")
    d.add_argument("video_id")

    return p.parse_args()


def _work_dir() -> Path:
    """Каталог для _queue.jsonl и служебных файлов.

    KB_QUEUE_DIR позволяет запускать скрипт из другого места (используется
    обёртками scripts/collect_*.py для совместимости со старым workflow).
    """
    override = os.getenv("KB_QUEUE_DIR", "").strip()
    return Path(override) if override else Path(__file__).resolve().parent


def script_dir() -> Path:
    return _work_dir()


def queue_path() -> Path:
    return script_dir() / "_queue.jsonl"


def done_path() -> Path:
    return script_dir() / "_done.txt"


def trans_done_path() -> Path:
    return script_dir() / "_trans_done.txt"


def transcripts_dir() -> Path:
    d = script_dir() / "_transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcript_txt_path(video_id: str) -> Path:
    return transcripts_dir() / f"{video_id}.txt"


def load_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_set(path: Path, s: set[str]) -> None:
    path.write_text("\n".join(sorted(s)) + "\n", encoding="utf-8")


def append_queue(entries: list[dict]) -> None:
    q = queue_path()
    with q.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def read_queue() -> list[dict]:
    q = queue_path()
    if not q.exists():
        return []
    out: list[dict] = []
    for line in q.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def fetch_channel_videos(channel_url: str) -> list[dict]:
    cmd = [
        "yt-dlp", "--skip-download", "--print-json",
        "--match-filter", "!is_short & duration > 60",
        channel_url + "/videos",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        sys.stderr.write(f"yt-dlp error: {result.stderr}\n")
        return []
    videos: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                videos.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return videos


def filter_by_date(videos: list[dict], months: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    kept: list[dict] = []
    for v in videos:
        upd = v.get("upload_date")
        if not upd:
            continue
        try:
            up_dt = datetime.strptime(upd, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if up_dt >= cutoff:
            kept.append(v)
    return kept


def parse_json3_subtitles(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    events = data.get("events") or []
    segments: list[dict] = []
    for ev in events:
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        segments.append({"seconds": ev.get("tStartMs", 0) / 1000.0, "text": text})
    return segments


def format_transcript(segments: list[dict]) -> str:
    if not segments:
        return ""
    lines: list[str] = []
    for i, seg in enumerate(segments):
        m, s = divmod(int(seg["seconds"]), 60)
        h, m = divmod(m, 60)
        ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{ts}] {seg['text']}")
        if (i + 1) % 6 == 0:
            lines.append("")
    return "\n".join(lines).strip()


def fetch_transcript(video_id: str, video_url: str, langs: list[str]) -> str | None:
    tmp = Path(tempfile.mkdtemp(prefix="yt_subs_"))
    out_tmpl = str(tmp / "%(id)s")
    for lang in langs:
        cmd = [
            "yt-dlp", "--skip-download", "--write-subs",
            "--sub-lang", lang, "--sub-format", "json3",
            "--output", out_tmpl, video_url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode != 0:
            continue
        candidates = list(tmp.glob(f"{video_id}.{lang}.json3")) + list(tmp.glob(f"{video_id}.*.json3"))
        if not candidates:
            continue
        raw = candidates[0].read_text(encoding="utf-8", errors="replace")
        segments = parse_json3_subtitles(raw)
        if segments:
            return format_transcript(segments)
    return None


def cmd_collect(args: argparse.Namespace) -> int:
    done = load_set(done_path())
    print(f"Сбор видео с канала: {args.channel_url}")
    videos = fetch_channel_videos(args.channel_url)
    print(f"  Найдено видео (без Shorts): {len(videos)}")
    videos = filter_by_date(videos, args.months)
    print(f"  После фильтра даты: {len(videos)}")

    new_entries: list[dict] = []
    for v in videos:
        vid = v.get("id", "")
        if not vid or vid in done:
            continue
        new_entries.append({
            "id": vid,
            "title": v.get("title", "Без названия"),
            "description": v.get("description", "") or "",
            "upload_date": v.get("upload_date", ""),
            "duration": v.get("duration", 0),
            "webpage_url": v.get("webpage_url", f"https://youtube.com/watch?v={vid}"),
        })

    if new_entries:
        append_queue(new_entries)
        print(f"  Добавлено в очередь: {len(new_entries)}")
    else:
        print("  Новых видео нет.")
    print(f"Очередь: {queue_path()}  |  Готово: {done_path()}")
    return 0


def cmd_transcripts(args: argparse.Namespace) -> int:
    done = load_set(done_path())
    trans_done = load_set(trans_done_path())
    queue = read_queue()
    langs = [l.strip() for l in os.getenv("KB_YT_SUB_LANGS", DEFAULT_SUB_LANGS).split(",") if l.strip()]

    pending = [e for e in queue if e["id"] not in trans_done]
    if args.limit and args.limit > 0:
        pending = pending[: args.limit]

    print(f"Всего в очереди: {len(queue)}, с субтитрами: {len(trans_done)}, нужно скачать: {len(pending)}")

    for i, e in enumerate(pending, 1):
        vid = e["id"]
        url = e["webpage_url"]
        title = e.get("title", "")
        print(f"[{i}/{len(pending)}] {vid} | {title[:70]}", end=" ")
        sys.stdout.flush()
        try:
            text = fetch_transcript(vid, url, langs)
            if text:
                transcript_txt_path(vid).write_text(text, encoding="utf-8")
                trans_done.add(vid)
                print(f"OK ({len(text)} знаков)")
            else:
                print("ПРОПУЩЕН (нет субтитров)")
        except Exception as ex:
            print(f"ОШИБКА: {ex}")
        if i < len(pending):
            time.sleep(1.5)

    save_set(trans_done_path(), trans_done)
    print(f"Готово. Папка субтитров: {transcripts_dir()}")
    return 0


def cmd_list_queue(args: argparse.Namespace) -> int:
    done = load_set(done_path())
    trans_done = load_set(trans_done_path())
    queue = read_queue()
    pending = [e for e in queue if e["id"] not in done]
    print(f"Всего: {len(queue)}, необработано: {len(pending)}, с субтитрами: {len(trans_done)}")
    for e in pending:
        dt = e.get("upload_date", "")
        dur = e.get("duration", 0)
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
        has_sub = "суб+ " if e["id"] in trans_done else "суб- "
        print(f"  {e['id']} | {dt} | {dur_str} | {has_sub}| {e.get('title','')} | {e.get('webpage_url','')}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    done = load_set(done_path())
    done.add(args.video_id)
    save_set(done_path(), done)
    print(f"Помечено обработанным: {args.video_id}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "collect":
        return cmd_collect(args)
    if args.command == "transcripts":
        return cmd_transcripts(args)
    if args.command == "list-queue":
        return cmd_list_queue(args)
    if args.command == "done":
        return cmd_done(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
