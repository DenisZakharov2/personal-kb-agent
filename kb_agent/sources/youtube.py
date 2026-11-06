"""Сборщик YouTube: метаданные и автоматические субтитры (Этап 2).

Логика перенесена из scripts/legacy/collect_youtube.py, но запись теперь ведётся
в SQLite-таблицу raw_queue вместо файла _queue.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.youtube")

DEFAULT_SUB_LANGS = ["ru", "en"]


def _ensure_yt_dlp() -> None:
    """Проверяет наличие внешнего бинаря yt-dlp в PATH.

    Вызывается перед сбором YouTube. Если бинарный файл absent,
    выбрасывается понятная ошибка с указанием, как установить зависимость.
    """
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp не найден в PATH. Установите необходимую зависимость: "
            "pip install '.[youtube]' (или загрузите бинарный файл yt-dlp "
            "и добавьте его в PATH). Без yt-dlp сбор YouTube невозможен."
        )


def _fetch_channel_videos(channel_url: str, max_videos: int | None = None) -> list[dict]:
    """Скачивает метаданные видео канала через yt-dlp (без Shorts, без загрузки видео)."""
    # Ограничиваем количество видео через --playlist-end для производительности
    playlist_end = max_videos * 3 if max_videos and max_videos > 0 else 30
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--print-json",
        "--match-filter",
        "!is_short & duration > 60",
        "--playlist-end",
        str(playlist_end),
        channel_url.rstrip("/") + "/videos",
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp timeout для {channel_url}")
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp error: {result.stderr[:500]}")
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


def _download_subs(
    tmp: Path,
    video_id: str,
    video_url: str,
    lang: str,
    *,
    write_auto: bool,
) -> str | None:
    """Скачивает субтитры одного языка (ручные или автоматические).

    ``write_auto=True`` запрашивает автоматические субтитры YouTube
    (``--write-auto-subs``), когда у автора нет собственных дорожек.
    Возвращает отформатированную транскрипцию или None.
    """
    out_tmpl = str(tmp / "%(id)s")
    kind = "авто" if write_auto else "ручные"
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-subs" if write_auto else "--write-subs",
        "--sub-lang",
        lang,
        "--sub-format",
        "json3",
        "--output",
        out_tmpl,
        video_url,
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        logger.warning("  %s | таймаут при скачивании %s субтитров (%s)", video_id, kind, lang)
        return None
    if result.returncode != 0:
        logger.warning(
            "  %s | yt-dlp ошибка (%s субтитры, %s): %s",
            video_id,
            kind,
            lang,
            result.stderr[:200],
        )
        return None

    lang_file = tmp.glob(f"{video_id}.{lang}.json3")
    lang_candidates = list(lang_file)
    if not lang_candidates:
        # Пробуем любой json3 файл для этого видео
        any_candidates = list(tmp.glob(f"{video_id}.*.json3"))
        if not any_candidates:
            logger.warning("  %s | %s субтитры не найдены (%s)", video_id, kind, lang)
            return None
        raw_path = any_candidates[0]
    else:
        raw_path = lang_candidates[0]

    try:
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("  %s | ошибка чтения субтитров: %s", video_id, exc)
        return None

    segments = _parse_json3_subtitles(raw)
    if segments:
        logger.info(
            "  %s | %s субтитры найдены (%s, %d сегментов)",
            video_id,
            kind,
            lang,
            len(segments),
        )
        return _format_transcript(segments)
    logger.warning("  %s | %s субтитры пустые (%s)", video_id, kind, lang)
    return None


def _fetch_transcript(video_id: str, video_url: str, langs: list[str]) -> str | None:
    """Скачивает субтитры видео: сначала ручные, потом автоматические.

    Временная директория удаляется автоматически после завершения.
    """
    with tempfile.TemporaryDirectory(prefix="yt_subs_") as tmp_dir:
        tmp = Path(tmp_dir)
        for lang in langs:
            transcript = _download_subs(tmp, video_id, video_url, lang, write_auto=False)
            if transcript is None:
                transcript = _download_subs(tmp, video_id, video_url, lang, write_auto=True)
            if transcript:
                return transcript
    return None


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
    langs = [l.strip() for l in sub_langs.split(",") if l.strip()] or DEFAULT_SUB_LANGS
    _ensure_yt_dlp()
    return _collect_one_channel(state, channel_url, langs, months, limit, source_id=source_id)


def _collect_one_channel(
    state: StateManager,
    channel_url: str,
    langs: list[str],
    months: int,
    limit: int,
    source_id: str | None = None,
) -> dict:
    """Внутренняя функция сбора одного канала."""
    if source_id is None:
        channel_name = channel_url.rstrip("/").split("/")[-1]
        source_id = f"yt_{channel_name.replace('@', '')}"

    logger.info("Канал YouTube: %s", channel_url)
    videos = _fetch_channel_videos(channel_url, max_videos=limit)
    logger.info("  Найдено видео (без Shorts): %s", len(videos))
    videos = _filter_by_date(videos, months)
    logger.info("  После фильтра даты: %s", len(videos))

    new_for_channel = 0
    skipped_for_channel = 0
    subs_for_channel = 0

    # Применяем limit на ранней стадии — не скачиваем лишнее
    videos_to_process = videos[:limit] if limit and limit > 0 else videos

    for v in videos_to_process:
        vid = v.get("id", "")
        if not vid:
            continue

        external_id = f"yt_{vid}"
        canonical_url = v.get("webpage_url", f"https://youtube.com/watch?v={vid}")
        title = v.get("title", "Без названия") or "Без названия"
        raw_text = v.get("description", "") or ""

        # Проверка на déjà vu (по external_id) ДО скачивания субтитров
        if state.is_processed(external_id):
            skipped_for_channel += 1
            continue

        # Скачиваем субтитры
        transcript_text = _fetch_transcript(video_id=vid, video_url=canonical_url, langs=langs)
        if transcript_text:
            raw_text += "\n\n--- ТРАНСКРИПЦИЯ ---\n" + transcript_text
            subs_for_channel += 1
        else:
            logger.info("  %s | нет субтитров", vid)

        # raw_hash считается ПОСЛЕ добавления транскрипции
        raw_hash = hashlib.sha256((title + raw_text).encode("utf-8")).hexdigest()

        queue_id = state.enqueue(
            source_id=source_id,
            external_id=external_id,
            canonical_url=canonical_url,
            title=title,
            raw_text=raw_text,
            raw_hash=raw_hash,
            published_at=v.get("upload_date"),
        )
        if queue_id is None:
            skipped_for_channel += 1
            continue

        # mark_processed_key будет вызван в pipeline ПОСЛЕ успешного создания заметки
        new_for_channel += 1

        # Пауза между запросами, чтобы не заблокировали
        if new_for_channel % 5 == 0:
            time.sleep(1.5)

    logger.info(
        "  Новых: %s, с субтитрами: %s, уже было: %s",
        new_for_channel,
        subs_for_channel,
        skipped_for_channel,
    )

    return {
        "source": "youtube",
        "new": new_for_channel,
        "skipped": skipped_for_channel,
        "with_subtitles": subs_for_channel,
    }
