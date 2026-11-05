"""P2-02: атомарная запись sources.yaml в SourceRegistry."""

from __future__ import annotations

import pytest
import yaml

from kb_agent.sources.base import SourceConfig
from kb_agent.sources.registry import SourceRegistry, normalize_source


def _write_yaml(path, sources):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"version": 1, "sources": sources}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class TestRegistryAtomicYaml:
    def test_save_writes_valid_yaml_no_tmp_left(self, tmp_path):
        """После save() sources.yaml валиден, временный файл не остаётся."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        registry._sources = {
            "tg_test": SourceConfig(id="tg_test", type="telegram", url="https://t.me/s/test", enabled=True),
        }
        registry._loaded = True

        registry.save()

        assert (tmp_path / "sources.yaml").exists()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert not tmp_files, f"Временные файлы не удалены: {tmp_files}"

        data = yaml.safe_load((tmp_path / "sources.yaml").read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["sources"]) == 1
        assert data["sources"][0]["id"] == "tg_test"

    def test_atomic_write_yaml_uses_temp_and_replace(self, tmp_path):
        """_atomic_write_yaml пишет через .tmp и заменяет целевой файл."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        target = tmp_path / "sources.yaml"

        registry._atomic_write_yaml(target, {"version": 1, "sources": []})

        assert target.exists()
        assert not target.with_name(target.name + ".tmp").exists()

    def test_overwrite_yaml_atomic(self, tmp_path):
        """Повторная запись заменяет содержимое полностью, без остатков."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        target = tmp_path / "sources.yaml"

        registry._atomic_write_yaml(target, {"version": 1, "sources": [{"id": "a"}]})
        registry._atomic_write_yaml(target, {"version": 1, "sources": [{"id": "b"}]})

        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data["sources"] == [{"id": "b"}]

    def test_export_to_yaml_atomic(self, config, state):
        """_export_to_yaml записывает атомарно из SQLite."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        state.add_source("tg_exp", "telegram", "https://t.me/s/exp")
        registry._sources = {
            "tg_exp": SourceConfig(id="tg_exp", type="telegram", url="https://t.me/s/exp", enabled=True),
        }
        registry._loaded = True
        registry._use_sqlite = True

        registry._export_to_yaml()

        assert config.sources_path.exists()
        assert not config.sources_path.with_name(config.sources_path.name + ".tmp").exists()
        data = yaml.safe_load(config.sources_path.read_text(encoding="utf-8"))
        assert len(data["sources"]) == 1


class TestSourceNormalization:
    """P2-13: нормализация и валидация источников."""

    def test_normalize_telegram_url(self):
        sid, url, st = normalize_source("telegram", "https://t.me/s/rubiconzone")
        assert sid == "tg_rubiconzone"
        assert url == "https://t.me/s/rubiconzone"
        assert st == "telegram"

    def test_normalize_youtube_url(self):
        sid, url, st = normalize_source("youtube", "https://youtube.com/@amorev")
        assert sid == "yt_amorev"
        assert url == "https://youtube.com/@amorev"
        assert st == "youtube"

    def test_normalize_generates_source_id_when_missing(self):
        sid, _url, _st = normalize_source("youtube", "https://youtube.com/@kanal", source_id=None)
        assert sid == "yt_kanal"

    def test_normalize_keeps_explicit_source_id(self):
        sid, _url, st = normalize_source("telegram", "https://t.me/s/chan", source_id="my_custom_id")
        assert sid == "my_custom_id"
        assert st == "telegram"

    def test_normalize_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Неподдерживаемый тип"):
            normalize_source("rss", "https://example.com/feed")

    def test_normalize_invalid_telegram_url_raises(self):
        with pytest.raises(ValueError, match="Неправильный URL Telegram"):
            normalize_source("telegram", "https://example.com/not-telegram")

    def test_normalize_invalid_youtube_url_raises(self):
        with pytest.raises(ValueError, match="Неправильный URL YouTube"):
            normalize_source("youtube", "https://example.com/video")

    def test_registry_add_rejects_invalid_url(self, config, state):
        """add() возвращает False для некорректного URL типа."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry._loaded = True
        registry._use_sqlite = True

        cfg = SourceConfig(
            id="tg_bad", type="telegram", url="https://example.com/not-telegram", enabled=True,
        )
        assert registry.add(cfg) is False

    def test_registry_add_accepts_valid(self, config, state):
        """add() принимает корректный URL типа."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry._loaded = True
        registry._use_sqlite = True

        cfg = SourceConfig(
            id="tg_ok", type="telegram", url="https://t.me/s/okchan", enabled=True,
        )
        assert registry.add(cfg) is True
        assert registry.get("tg_ok") is not None
