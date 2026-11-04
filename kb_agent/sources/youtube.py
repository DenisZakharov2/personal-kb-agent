"""Сборщик YouTube: метаданные и автоматические субтитры (Этап 2).

Логика перенесена из scripts/collect_youtube.py, но запись теперь ведётся
в SQLite-таблицу raw_queue вместо файла _queue.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.youtube")

DEFAULT_SUB_LANGS = "ru,en"


def _fetch_channel_videos(channel_url: str) -> list[dict]:
    """Скачивает метаданные видео канала через yt-dlp (без Shorts, без загрузки видео)."""
    cmd = [
        "yt-dlp", "--skip-download", "--print-json",
        "--match-filter", "!is_short & duration > 60",
        channel_url + "/videos",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timeout для %s", channel_url)
        return []
    if result.returncode != 0:
        logger.error("yt-dlp error: %s", result.stderr)
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


def _filter_by_date(videos: list[dict], months: int) -> list[dict]:
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


def _parse_json3_subtitles(raw: str) -> list[dict]:
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


def _format_transcript(segments: list[dict]) -> str:
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


def _fetch_transcript(video_id: str, video_url: str, langs: list[str]) -> Optional[str]:
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
            logger.warning("  %s | таймаут при скачивании субтитров (%s)", vid, lang)
            continue
        if result.returncode != 0:
            logger.warning("  %s | yt-dlp ошибка (%s): %s", vid, lang, result.stderr[:200])
            continue

        # Ищем файл субтитров нужного языка
        lang_file = tmp.glob(f"{video_id}.{lang}.json3")
        lang_candidates = list(lang_file)
        if not lang_candidates:
            # Пробуем любой json3 файл для этого видео
            any_candidates = list(tmp.glob(f"{video_id}.*.json3"))
            if not any_candidates:
                logger.warning("  %s | субтитры не найдены (%s)", vid, lang)
                continue
            raw_path = any_candidates[0]
        else:
            raw_path = lang_candidates[0]

        try:
            raw = raw_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("  %s | ошибка чтения субтитров: %s", vid, exc)
            continue

        segments = _parse_json3_subtitles(raw)
        if segments:
            logger.info("  %s | субтитры найдены (%s, %d сегментов)", vid, lang, len(segments))
            return _format_transcript(segments)
        else:
            logger.warning("  %s | субтитры пустые (%s)", vid, lang)
    return None


def collect_youtube(config: AppConfig, state: StateManager) -> dict:
    """Собирает видео из всех YouTube-каналов и пишет в raw_queue.

    Для каждого видео также скачивает автоматические субтитры (ru → en).
    Возвращает статистику.
    """
    channels = [c.strip() for c in config.youtube_channels.split(",") if c.strip()]
    langs = [l.strip() for l in config.yt_sub_langs.split(",") if l.strip()] or [DEFAULT_SUB_LANGS]

    total_new = 0
    total_skipped = 0
    total_with_subs = 0

    for channel_url in channels:
        stats = _collect_one_channel(state, channel_url, langs, config.yt_months, config.limit)
        total_new += stats["new"]
        total_skipped += stats["skipped"]
        total_with_subs += stats["with_subtitles"]

    return {
        "source": "youtube",
        "new": total_new,
        "skipped": total_skipped,
        "with_subtitles": total_with_subs,
    }


def collect_youtube_source(
    state: StateManager,
    source_id: str,
    channel_url: str,
    months: int,
    sub_langs: str,
    limit: int,
) -> dict:
    """Собирает видео из одного YouTube-канала и пишет в raw_queue.

    channel_url — полный URL канала (https://youtube.com/@amorev).
    months — глубина бэклога в месяцах.
    sub_langs — языки субтитров через запятую.
    limit — максимум видео за запуск.
    """
    langs = [l.strip() for l in sub_langs.split(",") if l.strip()] or [DEFAULT_SUB_LANGS]
    return _collect_one_channel(state, channel_url, langs, months, limit, source_id=source_id)


def _collect_one_channel(
    state: StateManager,
    channel_url: str,
    langs: list[str],
    months: int,
    limit: int,
    source_id: Optional[str] = None,
) -> dict:
    """Внутренняя функция сбора одного канала."""
    if source_id is None:
        channel_name = channel_url.rstrip("/").split("/")[-1]
        source_id = f"yt_{channel_name.replace('@', '')}"

    logger.info("Канал YouTube: %s", channel_url)
    videos = _fetch_channel_videos(channel_url)
    logger.info("  Найдено видео (без Shorts): %s", len(videos))
    videos = _filter_by_date(videos, months)
    logger.info("  После фильтра даты: %s", len(videos))

    new_for_channel = 0
    skipped_for_channel = 0
    subs_for_channel = 0

    for v in videos:
        vid = v.get("id", "")
        if not vid:
            continue

        external_id = f"yt_{vid}"
        canonical_url = v.get("webpage_url", f"https://youtube.com/watch?v={vid}")
        title = v.get("title", "Без названия") or "Без названия"
        raw_text = v.get("description", "") or ""

        raw_hash = hashlib.sha256(
            (title + raw_text).encode("utf-8")
        ).hexdigest()

        # Проверка на déjà vu (по external_id)
        if state.is_processed(external_id):
            skipped_for_channel += 1
            continue

        # Скачиваем субтитры
        transcript_text = _fetch_transcript(vid, canonical_url, langs)
        if transcript_text:
            raw_text += "\n\n--- ТРАНСКРИПЦИЯ ---\n" + transcript_text
            subs_for_channel += 1
        else:
            logger.info("  %s | нет субтитров", vid)

        queue_id = state.enqueue(
            source_id=source_id,
            external_id=external_id,
            canonical_url=canonical_url,
            title=title,
            raw_text=raw_text,
            raw_hash=raw_hash,
        )
        if queue_id is None:
            skipped_for_channel += 1
            continue
        new_for_channel += 1

        # Пауза между запросами, чтобы не заблокировали
        if new_for_channel % 5 == 0:
            time.sleep(1.5)

    logger.info("  Новых: %s, с субтитрами: %s, уже было: %s", new_for_channel, subs_for_channel, skipped_for_channel)

    return {
        "source": "youtube",
        "new": new_for_channel,
        "skipped": skipped_for_channel,
        "with_subtitles": subs_for_channel,
    }
