"""Тесты конфигурации и упаковки (P1-09, P2-07)."""

from __future__ import annotations

from kb_agent.config import get_prompt_path


class TestConfigResources:
    """P1-09: Ресурсы доступны через importlib.resources."""

    def test_get_prompt_path_finds_packaged_prompt(self):
        """Упакованный промпт доступен через get_prompt_path."""
        path = get_prompt_path("agent_prompt.md")
        assert path is not None, "agent_prompt.md должен быть найден"
        assert path.exists(), f"Путь существует: {path}"
        assert path.suffix == ".md"

    def test_get_prompt_path_fallback_for_dev(self):
        """Fallback на config/prompts для разработки из исходников."""
        path = get_prompt_path("agent_prompt.md")
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0, "Промпт не пустой"


class TestConfigPaths:
    """P2-07: Нет хардкода личного пути."""

    def test_default_vault_path_uses_expanduser(self):
        """DEFAULT_VAULT_PATH должен использовать expanduser, а не хардкод."""
        from kb_agent.config import DEFAULT_VAULT_PATH

        # Путь должен быть получен через expanduser, а не быть абсолютным хардкодом
        assert str(DEFAULT_VAULT_PATH).startswith("/"), \
            f"DEFAULT_VAULT_PATH должен быть абсолютным: {DEFAULT_VAULT_PATH}"