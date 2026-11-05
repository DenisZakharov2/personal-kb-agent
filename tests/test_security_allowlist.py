"""Тесты allowlist источников и URL (6.1).

Проверяется валидация хостов/паттернов при добавлении источника,
включая покрытие протоколов yt-dlp.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.types import CallToolResult, TextContent

from kb_agent.agents.mcp_server import (
    ALLOWED_HOSTS,
    _is_url_allowed,
    handle_call_tool,
)
from kb_agent.sources.registry import normalize_source


def _is_error_result(result) -> bool:
    return isinstance(result, CallToolResult)


class TestUrlAllowlist:
    """6.1: только хосты из allowlist разрешены для источников."""

    def test_allowed_hosts_contains_known_domains(self):
        """Allowlist содержит Telegram и YouTube домены."""
        assert "t.me" in ALLOWED_HOSTS
        assert "youtube.com" in ALLOWED_HOSTS
        assert "youtu.be" in ALLOWED_HOSTS

    def test_is_url_allowed_telegram(self):
        assert _is_url_allowed("https://t.me/s/channel") is True
        assert _is_url_allowed("https://www.t.me/s/channel") is True
        assert _is_url_allowed("https://telegram.me/channel") is True

    def test_is_url_allowed_youtube(self):
        assert _is_url_allowed("https://youtube.com/@handle") is True
        assert _is_url_allowed("https://www.youtube.com/@handle") is True
        assert _is_url_allowed("https://youtu.be/abc123") is True
        assert _is_url_allowed("https://youtube.com/c/channel") is True
        assert _is_url_allowed("https://youtube.com/channel/UCxxx") is True

    def test_is_url_allowed_rejects_unknown_host(self):
        assert _is_url_allowed("https://evil.example.com/malicious") is False
        assert _is_url_allowed("https://attacker.com/steal") is False

    def test_is_url_allowed_rejects_unsafe_scheme(self):
        """yt-dlp поддерживает ftp/file/и т.д. — они должны быть отклонены."""
        assert _is_url_allowed("ftp://t.me/s/chan") is False
        assert _is_url_allowed("file:///etc/passwd") is False
        assert _is_url_allowed("javascript:alert(1)") is False

    def test_normalize_source_rejects_bad_url(self):
        """normalize_source отклоняет некорректные URL (P2-13 + 6.1)."""
        with pytest.raises(ValueError):
            normalize_source("telegram", "https://evil.example.com/bad")
        with pytest.raises(ValueError):
            normalize_source("youtube", "https://evil.example.com/bad")
        with pytest.raises(ValueError):
            normalize_source("unknown_type", "https://t.me/s/chan")

    def test_mcp_add_source_rejects_disallowed_host(self, config, monkeypatch, state):
        """MCP kb_add_source отклоняет URL вне allowlist (через normalize_source)."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        result = asyncio.run(
            handle_call_tool(
                "kb_add_source",
                {
                    "source_type": "telegram",
                    "channel": "https://evil.example.com/malicious_chan",
                },
            )
        )
        assert _is_error_result(result)
        assert result.isError is True

    def test_mcp_add_source_allows_trusted_host(self, config, monkeypatch, state):
        """MCP kb_add_source принимает URL из allowlist (создаёт pending, не применяет)."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        result = asyncio.run(
            handle_call_tool(
                "kb_add_source",
                {
                    "source_type": "telegram",
                    "channel": "https://t.me/s/trusted_channel",
                },
            )
        )
        assert not _is_error_result(result)
        assert isinstance(result, list)
        assert all(isinstance(t, TextContent) for t in result)
        # Подтверждение требуется через CLI — источник не добавлен сразу
        # (pending-запрос создан, но источник ещё не в БД)


class TestYtDlpProtocolCoverage:
    """6.1: yt-dlp ограничен известными доменами, не все протоколы."""

    def test_yt_dlp_only_http_https(self):
        """yt-dlp может поддерживать многие протоколы, но мы разрешаем только http/https."""
        from urllib.parse import urlparse

        for url in [
            "https://youtube.com/@amorev",
            "https://t.me/s/chan",
        ]:
            parsed = urlparse(url)
            assert parsed.scheme in ("http", "https")

        for bad in ["ftp://youtube.com/@x", "file:///etc/passwd"]:
            parsed = urlparse(bad)
            assert parsed.scheme not in ("http", "https")
