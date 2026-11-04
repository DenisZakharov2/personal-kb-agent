"""Тесты YouTube-сборщика (P1-03, P1-04 из аудита)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kb_agent.sources.youtube import _fetch_transcript, _collect_one_channel


class TestYouTubeNameError:
    """P1-03: NameError vid не должен возникать при ошибке yt-dlp."""

    @patch("kb_agent.sources.youtube.subprocess.run")
    def test_fetch_transcript_nonzero_exit_no_nameerror(self, mock_run):
        """При ненулевом коде возврата yt-dlp не должно быть NameError."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        mock_run.return_value = mock_result

        # Не должно быть NameError: name 'vid' is not defined
        result = _fetch_transcript(
            video_id="test123",
            video_url="https://youtube.com/watch?v=test123",
            langs=["ru"],
        )
        assert result is None

    @patch("kb_agent.sources.youtube.subprocess.run")
    def test_fetch_transcript_timeout_no_nameerror(self, mock_run):
        """При таймауте yt-dlp не должно быть NameError."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120)

        result = _fetch_transcript(
            video_id="test123",
            video_url="https://youtube.com/watch?v=test123",
            langs=["ru"],
        )
        assert result is None


class TestYouTubeLimit:
    """P1-04: limit должен применяться на ранней стадии."""

    @patch("kb_agent.sources.youtube._fetch_channel_videos")
    @patch("kb_agent.sources.youtube._filter_by_date")
    @patch("kb_agent.sources.youtube._fetch_transcript")
    def test_limit_restricts_videos(
        self, mock_fetch_transcript, mock_filter, mock_fetch_videos, state
    ):
        """При limit=1 обрабатывается только одно видео из N найденных."""
        # 3 видео найдено, но limit=1
        mock_fetch_videos.return_value = [
            {"id": "vid1", "title": "Video 1", "description": "desc1", "webpage_url": "https://youtube.com/watch?v=vid1", "upload_date": "20260101"},
            {"id": "vid2", "title": "Video 2", "description": "desc2", "webpage_url": "https://youtube.com/watch?v=vid2", "upload_date": "20260102"},
            {"id": "vid3", "title": "Video 3", "description": "desc3", "webpage_url": "https://youtube.com/watch?v=vid3", "upload_date": "20260103"},
        ]
        mock_filter.side_effect = lambda v, m: v  # не фильтруем
        mock_fetch_transcript.return_value = None

        stats = _collect_one_channel(
            state=state,
            channel_url="https://youtube.com/@test",
            langs="ru",
            months=1,
            limit=1,
            source_id="yt_test",
        )

        # Должно быть обработано только 1 видео (не 3)
        assert stats["new"] == 1, f"Ожидалось 1 новое, получено {stats['new']}"
        # _fetch_transcript должен быть вызван ровно 1 раз
        assert mock_fetch_transcript.call_count == 1


class TestYouTubeDedup:
    """P1-04: Повторный сбор не скачивает субтитры для уже существующего видео."""

    @patch("kb_agent.sources.youtube._fetch_channel_videos")
    @patch("kb_agent.sources.youtube._filter_by_date")
    @patch("kb_agent.sources.youtube._fetch_transcript")
    def test_skips_already_processed_before_subtitles(
        self, mock_fetch_transcript, mock_filter, mock_fetch_videos, state
    ):
        """is_processed проверяется ДО скачивания субтитров."""
        # Первый запуск: видео обрабатывается
        mock_fetch_videos.return_value = [
            {"id": "vid1", "title": "Video 1", "description": "desc1", "webpage_url": "https://youtube.com/watch?v=vid1", "upload_date": "20260101"},
        ]
        mock_filter.side_effect = lambda v, m: v
        mock_fetch_transcript.return_value = None

        state.enqueue(
            source_id="yt_test",
            external_id="yt_vid1",
            canonical_url="https://youtube.com/watch?v=vid1",
            title="Video 1",
            raw_text="desc1",
            raw_hash="hash1",
        )
        # Помечаем как обработанный
        state.mark_processed_key("yt_vid1")

        stats = _collect_one_channel(
            state=state,
            channel_url="https://youtube.com/@test",
            langs="ru",
            months=1,
            limit=10,
            source_id="yt_test",
        )

        # Видео должно быть пропущено (уже в processed)
        assert stats["skipped"] == 1
        assert stats["new"] == 0
        # _fetch_transcript НЕ должен вызываться для уже обработанного видео
        mock_fetch_transcript.assert_not_called()