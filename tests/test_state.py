"""P2-02: атомарность и конкурентность StateManager (WAL, busy_timeout)."""

from __future__ import annotations


class TestStateConnectPragmas:
    def test_connect_sets_wal_and_busy_timeout(self, config, state):
        """После connect() режим журналирования WAL и busy_timeout установлены."""
        cur = state.connect().execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"

        cur = state.connect().execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] > 0


class TestStateIndexNoteDedup:
    def test_reindex_same_note_no_fts_duplicates(self, config, state):
        """Повторная индексация одного note_id не создаёт дублей в notes_fts."""
        state.index_note(
            note_id="note-1",
            title="Заголовок",
            tags="тэг1,тэг2",
            topic="Тема",
            content="Содержимое заметки для индексации",
        )
        state.index_note(
            note_id="note-1",
            title="Заголовок обновлён",
            tags="тэг1,тэг2",
            topic="Тема",
            content="Обновлённое содержимое заметки",
        )

        conn = state.connect()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notes_fts WHERE note_id = ?", ("note-1",)
        ).fetchone()
        assert rows["cnt"] == 1, f"Ожидалась 1 строка FTS, получено {rows['cnt']}"

        notes_rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notes WHERE note_id = ?", ("note-1",)
        ).fetchone()
        assert notes_rows["cnt"] == 1

    def test_rebuild_fts_restores_index_from_vault(self, config, state):
        """rebuild_fts перестраивает notes_fts из файлов vault."""
        from kb_agent.storage.vault import VaultManager

        vault = VaultManager(config)
        # Создаём реальные файлы заметок в vault
        vault.write_note(
            note_id="note-rebuild-1",
            source_id="tg_test",
            source_type="telegram",
            external_id="ext1",
            canonical_url="https://t.me/s/test/1",
            published_at="2026-01-01",
            received_at="2026-01-01T10:00:00",
            title="Первая",
            tags="тэг1",
            topic="Тема",
            content="содержимое первой заметки",
        )
        vault.write_note(
            note_id="note-rebuild-2",
            source_id="yt_test",
            source_type="youtube",
            external_id="ext2",
            canonical_url="https://youtube.com/watch?v=test",
            published_at="2026-01-02",
            received_at="2026-01-02T10:00:00",
            title="Вторая",
            tags="тэг2",
            topic="Тема",
            content="содержимое второй заметки",
        )

        # Также индексируем в SQLite (notes таблица) для метаданных
        state.index_note(
            note_id="note-rebuild-1",
            title="Первая",
            tags="тэг1",
            topic="Тема",
            content="содержимое первой заметки",
            source_id="tg_test",
            external_id="ext1",
            canonical_url="https://t.me/s/test/1",
            file_path=str(config.vault_path / "Тема" / "2026-01-01_Первая.md"),
            content_hash="hash1",
        )
        state.index_note(
            note_id="note-rebuild-2",
            title="Вторая",
            tags="тэг2",
            topic="Тема",
            content="содержимое второй заметки",
            source_id="yt_test",
            external_id="ext2",
            canonical_url="https://youtube.com/watch?v=test",
            file_path=str(config.vault_path / "Тема" / "2026-01-02_Вторая.md"),
            content_hash="hash2",
        )

        # Очищаем FTS вручную, как после частичного сбоя
        conn = state.connect()
        conn.execute("DELETE FROM notes_fts")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0] == 0

        count = state.rebuild_fts(vault_path=config.vault_path)
        assert count == 2

        rows = conn.execute("SELECT COUNT(*) AS cnt FROM notes_fts").fetchone()
        assert rows["cnt"] == 2

        # Проверяем, что контент восстановлен правильно
        fts_rows = conn.execute(
            "SELECT note_id, content FROM notes_fts ORDER BY note_id"
        ).fetchall()
        assert fts_rows[0]["note_id"] == "note-rebuild-1"
        assert "содержимое первой заметки" in fts_rows[0]["content"]
        assert fts_rows[1]["note_id"] == "note-rebuild-2"
        assert "содержимое второй заметки" in fts_rows[1]["content"]

    def test_revision_increments_on_reindex(self, config, state):
        """revision увеличивается при повторной индексации того же note_id."""
        state.index_note(
            note_id="note-2",
            title="T1",
            tags="t1",
            topic="Тема",
            content="C1",
        )
        state.index_note(
            note_id="note-2",
            title="T2",
            tags="t1",
            topic="Тема",
            content="C2",
        )

        conn = state.connect()
        row = conn.execute("SELECT revision FROM notes WHERE note_id = ?", ("note-2",)).fetchone()
        assert row is not None
        assert row["revision"] == 2


class TestStateSearchProvenance:
    """P2-05: search() возвращает поля provenance (URL, источник, путь, ревизия)."""

    def test_search_returns_provenance_fields(self, config, state):
        """Результат поиска содержит canonical_url, source_id, file_path, content_hash, revision."""
        state.index_note(
            note_id="note-prov-1",
            title="Заголовок про ИИ",
            tags="ии",
            topic="Технологии",
            content="Текст про нейросети и машинное обучение",
            source_id="tg_test",
            external_id="yt_vid1",
            canonical_url="https://t.me/s/test/42",
            file_path="/vault/note.md",
            content_hash="abc123",
            revision=1,
        )

        results = state.search("нейросети", limit=5)
        assert len(results) == 1
        row = results[0]
        assert row["note_id"] == "note-prov-1"
        assert row["canonical_url"] == "https://t.me/s/test/42"
        assert row["source_id"] == "tg_test"
        assert row["external_id"] == "yt_vid1"
        assert row["file_path"] == "/vault/note.md"
        assert row["content_hash"] == "abc123"
        assert row["revision"] == 1
        assert row["title"] == "Заголовок про ИИ"

    def test_search_missing_notes_row_returns_nulls(self, config, state):
        """Если записи в notes нет (только FTS), поля provenance — NULL."""
        conn = state.connect()
        conn.execute(
            "INSERT INTO notes_fts (note_id, title, tags, topic, content) VALUES (?, ?, ?, ?, ?)",
            ("note-fts-only", "Заголовок", "тэг", "Тема", "текст про поиск"),
        )
        conn.commit()

        results = state.search("поиск", limit=5)
        assert len(results) == 1
        row = results[0]
        assert row["note_id"] == "note-fts-only"
        assert row["canonical_url"] is None
        assert row["source_id"] is None
        assert row["file_path"] is None
        assert row["content_hash"] is None
        assert row["revision"] is None


class TestStatePromptSeeding:
    """P1-09: Промпты сеяятся из упакованных ресурсов."""

    def test_seed_prompt_from_text_creates_version_1(self, config, state):
        """seed_prompt_from_text создаёт версию 1 промпта из строки."""
        result = state.seed_prompt_from_text(
            prompt_name="test_prompt",
            content="Это тестовый промпт для проверки.",
            prompt_type="summarize",
        )
        assert result is not None
        assert result["prompt_name"] == "test_prompt"
        assert result["version"] == 1
        assert result["prompt_type"] == "summarize"
        assert result["is_active"] == 1
        assert result["approved_by"] == "system"
        assert result["content"] == "Это тестовый промпт для проверки."

    def test_seed_prompt_from_text_idempotent(self, config, state):
        """Повторный вызов для того же промпта не создаёт дубля."""
        result1 = state.seed_prompt_from_text(
            prompt_name="test_prompt_idem",
            content="Первая версия.",
            prompt_type="search",
        )
        assert result1 is not None
        assert result1["version"] == 1

        result2 = state.seed_prompt_from_text(
            prompt_name="test_prompt_idem",
            content="Вторая версия (игнорируется).",
            prompt_type="search",
        )
        assert result2 is None  # активная версия уже есть

    def test_seed_prompt_from_text_empty_content(self, config, state):
        """Пустое содержимое — не создаёт промпт."""
        result = state.seed_prompt_from_text(
            prompt_name="test_prompt_empty",
            content="",
            prompt_type="search",
        )
        assert result is None

    def test_seed_prompt_from_text_whitespace_only(self, config, state):
        """Только пробелы — не создаёт промпт."""
        result = state.seed_prompt_from_text(
            prompt_name="test_prompt_ws",
            content="   \n\n   ",
            prompt_type="search",
        )
        assert result is None


class TestPromptSeedingFromResources:
    """P1-09: ensure_prompt_seeded читает промпты из упакованных ресурсов."""

    def test_ensure_prompt_seeded_uses_packaged_resources(self, config, state, monkeypatch):
        """ensure_prompt_seeded читает содержимое промпта из ресурсов, а не от пути к файлу."""
        import kb_agent.core.prompts as prompts_mod

        # Мокаем get_prompt_text в модуле config (откуда импортируется в prompts)
        monkeypatch.setattr(
            "kb_agent.config.get_prompt_text",
            lambda name, config=None: "# Тестовый промпт из ресурсов\nСодержимое для проверки.",
        )
        # Мокаем save_prompt, чтобы отслеживать вызовы
        calls = []
        original_save = state.save_prompt

        def tracking_save(*args, **kwargs):
            calls.append((args, kwargs))
            return original_save(*args, **kwargs)

        monkeypatch.setattr(state, "save_prompt", tracking_save)

        prompts_mod.ensure_prompt_seeded(config, state)

        # Должны быть вызваны save_prompt для всех трёх промптов
        assert len(calls) == 3, f"Ожидалось 3 вызова save_prompt, получено {len(calls)}"
        prompt_names = {call[1].get("prompt_name") or call[0][0] for call in calls}
        assert prompt_names == {"agent_prompt", "summarize_prompt", "answer_prompt"}
        # Все промпты получат одинаковое содержимое из agent_prompt.md
        for call in calls:
            content = call[1].get("content") or call[0][2]
            assert content == "# Тестовый промпт из ресурсов\nСодержимое для проверки."
