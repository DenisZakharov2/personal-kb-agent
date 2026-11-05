"""Тесты генератора Каталог.md (P2-09): атомарная запись, бэкап, frontmatter."""

from __future__ import annotations

from pathlib import Path

from kb_agent.config import AppConfig
from kb_agent.storage.catalog import CatalogGenerator
from kb_agent.storage.vault import VaultManager


def _make_vault(temp_dir: Path) -> VaultManager:
    config = AppConfig(
        vault_path=temp_dir / "vault",
        data_dir=temp_dir / "data",
        db_path=temp_dir / "data" / "state.db",
        sources_path=temp_dir / "data" / "sources.yaml",
        profile_path=temp_dir / "data" / "user_profile.yaml",
        log_path=temp_dir / "data" / "kb_agent.log",
    )
    return VaultManager(config)


def _write_note(vault: VaultManager, topic: str, title: str, content: str, bad_fm: bool = False) -> Path:
    folder = vault._topic_folder(topic)
    file_name = f"{_slugify(title)}.md"
    file_path = folder / file_name
    if bad_fm:
        frontmatter = "---\nthis: is: broken: [unclosed\n---\n"
    else:
        frontmatter = (
            f"---\ntitle: {title}\ntopic: {topic}\ntags: [тэг1]\n---\n"
        )
    file_path.write_text(frontmatter + f"# {title}\n\n{content}\n", encoding="utf-8")
    return file_path


def _slugify(text: str) -> str:
    import re

    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "Общее"


class TestCatalogGenerate:
    def test_generates_catalog_grouped_by_topic(self, temp_dir):
        """Каталог сгруппирован по темам, служебные файлы не попадают."""
        vault = _make_vault(temp_dir)
        _write_note(vault, "ИИ", "Первая статья", "содержание про нейросети")
        _write_note(vault, "Программирование", "Вторая статья", "содержание про код")
        _write_note(vault, "ИИ", "Третья статья", "ещё про нейросети")

        gen = CatalogGenerator(vault)
        out = gen.generate()

        assert out == vault.vault_path / "Каталог.md"
        text = out.read_text(encoding="utf-8")
        assert "# Каталог базы знаний" in text
        assert "Всего заметок: 3" in text
        assert "### ИИ" in text
        assert "### Программирование" in text

    def test_atomic_write_no_tmp_left(self, temp_dir):
        """После generate() временный .tmp-файл не остаётся."""
        vault = _make_vault(temp_dir)
        _write_note(vault, "ИИ", "Статья", "содержание")

        gen = CatalogGenerator(vault)
        gen.generate()

        tmp_files = list(vault.vault_path.rglob("*.tmp"))
        assert not tmp_files, f"Временные файлы остались: {tmp_files}"

    def test_backup_created_on_overwrite(self, temp_dir):
        """При перезаписи существующего Каталог.md создаётся .bak копия."""
        vault = _make_vault(temp_dir)
        catalog_path = vault.vault_path / "Каталог.md"
        catalog_path.write_text("старый пользовательский контент", encoding="utf-8")

        _write_note(vault, "ИИ", "Статья", "содержание")
        gen = CatalogGenerator(vault)
        gen.generate()

        bak_path = vault.vault_path / "Каталог.md.bak"
        assert bak_path.exists(), "Бэкап .bak должен создаваться при перезаписи"
        assert bak_path.read_text(encoding="utf-8") == "старый пользовательский контент"
        # Новый каталог не равен старому
        assert "старый пользовательский контент" not in catalog_path.read_text(encoding="utf-8")

    def test_bad_frontmatter_does_not_crash(self, temp_dir):
        """Заметка с битым frontmatter не ломает генерацию каталога."""
        vault = _make_vault(temp_dir)
        _write_note(vault, "ИИ", "Хорошая", "нормальный контент")
        _write_note(vault, "ИИ", "Плохая", "битый фронтматер", bad_fm=True)

        gen = CatalogGenerator(vault)
        # Не должно падать с исключением
        gen.generate()

        text = (vault.vault_path / "Каталог.md").read_text(encoding="utf-8")
        assert "Хорошая" in text
        assert "плохая" in text  # заметка попадает в каталог (тема по умолчанию, заголовок от имени файла)

    def test_frontmatter_at_sign_value_is_parsed(self, temp_dir):
        """Значение frontmatter, начинающееся с @, экранируется и парсится (например источник: @ragnarokzone)."""
        vault = _make_vault(temp_dir)
        folder = vault._topic_folder("Телеграм")
        file_path = folder / "2026-10-26_тест.md"
        file_path.write_text(
            "---\ntitle: Тест\nsource: telegram\nисточник: @ragnarokzone\ntopic: Телеграм\n---\n\n# Тест\n\nконтент\n",
            encoding="utf-8",
        )

        gen = CatalogGenerator(vault)
        # Не должно падать с исключением
        gen.generate()

        # Заметка попадает в каталог с правильным заголовком из frontmatter
        text = (vault.vault_path / "Каталог.md").read_text(encoding="utf-8")
        assert "Тест" in text

    def test_frontmatter_colon_in_title_is_parsed(self, temp_dir):
        """Значение frontmatter с ': ' внутри (например заголовок с двоеточием) парсится корректно."""
        vault = _make_vault(temp_dir)
        folder = vault._topic_folder("ИИ")
        file_path = folder / "2026-10-26_тест.md"
        file_path.write_text(
            "---\ntitle: Что такое ИИ: простое объяснение\ntopic: ИИ\n---\n\n# Что такое ИИ: простое объяснение\n\nконтент\n",
            encoding="utf-8",
        )

        gen = CatalogGenerator(vault)
        gen.generate()

        text = (vault.vault_path / "Каталог.md").read_text(encoding="utf-8")
        assert "Что такое ИИ: простое объяснение" in text


class TestCatalogDigestAtomic:
    def test_digest_atomic_write_no_tmp_left(self, temp_dir, config, state):
        """_write_vault_digest не оставляет .tmp-файла."""
        from kb_agent.processing.pipeline import Pipeline

        config.vault_path = temp_dir / "vault"
        config.data_dir = temp_dir / "data"
        pipeline = Pipeline(config)
        try:
            digest_path = pipeline._write_vault_digest(
                {"processed": 0, "created": 0, "skipped": 0, "relevance_skipped": 0, "failed": 0},
                {"new": 0, "spam": 0, "existing": 0},
                {"new": 0, "skipped": 0, "with_subtitles": 0},
            )
        finally:
            pipeline.close()

        tmp_files = list((temp_dir / "vault").rglob("*.tmp"))
        assert not tmp_files, f"Временные файлы остались: {tmp_files}"
        assert digest_path.exists()
