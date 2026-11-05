"""Тесты конфигурации и упаковки (P1-09, P2-07)."""

from __future__ import annotations

from pathlib import Path

from kb_agent.config import get_prompt_path, get_prompt_text


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

    def test_get_prompt_text_reads_packaged_prompt(self):
        """get_prompt_text читает содержимое упакованного промпта."""
        text = get_prompt_text("agent_prompt.md")
        assert text is not None, "agent_prompt.md должен быть прочитан"
        assert len(text) > 0, "Промпт не пустой"
        assert "# " in text or "## " in text, "Промпт содержит markdown-заголовки"

    def test_get_prompt_text_none_for_missing(self):
        """get_prompt_text возвращает None для несуществующего ресурса."""
        text = get_prompt_text("nonexistent_prompt.md")
        assert text is None


class TestConfigPaths:
    """P2-07: Нет хардкода личного пути; пути вычисляются от data_dir."""

    def test_default_vault_path_is_under_data_dir_not_personal(self):
        """По умолчанию vault_path = data_dir/vault, нет личного абсолютного пути."""
        from kb_agent.config import AppConfig, DEFAULT_DATA_DIR

        config = AppConfig()
        assert config.vault_path == DEFAULT_DATA_DIR / "vault"
        assert str(config.vault_path).startswith(str(DEFAULT_DATA_DIR))
        # Нет персонального пути вроде ~/База знаний (убрали DEFAULT_VAULT_PATH)
        assert str(config.vault_path) != str(Path.home() / "База знаний")


    def test_dependent_paths_under_data_dir(self):
        """db, sources, profile, log по умолчанию лежат под data_dir."""
        from kb_agent.config import AppConfig

        config = AppConfig()
        assert config.db_path == config.data_dir / "state.db"
        assert config.sources_path == config.data_dir / "sources.yaml"
        assert config.profile_path == config.data_dir / "user_profile.yaml"
        assert config.log_path == config.data_dir / "kb_agent.log"

    def test_kb_data_dir_moves_unset_paths(self, monkeypatch, tmp_path):
        """KB_DATA_DIR перемещает пути, которые НЕ заданы явно (в окружении или .env).

        KB_DB_PATH, KB_SOURCES_PATH, KB_PROFILE_PATH не заданы → вычисляются
        от data_dir. KB_VAULT_PATH и KB_LOG_PATH заданы в .env явно — остаются
        как есть (явный путь имеет приоритет).
        """
        monkeypatch.setenv("KB_DATA_DIR", str(tmp_path / "data"))
        from kb_agent.config import load_config

        config = load_config()
        assert config.data_dir == tmp_path / "data"
        assert config.db_path == tmp_path / "data" / "state.db"
        assert config.sources_path == tmp_path / "data" / "sources.yaml"
        assert config.profile_path == tmp_path / "data" / "user_profile.yaml"
        # Явные пути из .env не перезаписываются KB_DATA_DIR
        assert config.vault_path != tmp_path / "data" / "vault"
        assert config.log_path != tmp_path / "data" / "kb_agent.log"

    def test_explicit_env_paths_win(self, monkeypatch, tmp_path):
        """Явные KB_*-пути из окружения используются как есть (приоритет над .env)."""
        monkeypatch.setenv("KB_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("KB_VAULT_PATH", str(tmp_path / "my_vault"))
        monkeypatch.setenv("KB_DB_PATH", str(tmp_path / "my.db"))
        monkeypatch.setenv("KB_LOG_PATH", str(tmp_path / "my.log"))
        from kb_agent.config import load_config

        config = load_config()
        assert config.data_dir == tmp_path / "data"
        assert config.vault_path == tmp_path / "my_vault"
        assert config.db_path == tmp_path / "my.db"
        assert config.log_path == tmp_path / "my.log"
        # Источник и профиль не заданы явно → под data_dir
        assert config.sources_path == tmp_path / "data" / "sources.yaml"

