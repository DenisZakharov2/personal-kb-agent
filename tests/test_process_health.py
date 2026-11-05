"""P2-14: health summary и явные ошибки в процесс-команде."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kb_agent.cli.commands import process as process_cmd
from kb_agent.processing.pipeline import Pipeline


class TestProcessHealth:
    """P2-14: health summary и явные ошибки в процесс-команде."""

    def _patch_pipeline(self, monkeypatch, pipeline):
        """Заменяет тяжелые части Pipeline на заглушки для теста run()."""
        import kb_agent.processing.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ensure_prompt_seeded", lambda config, state: None)
        pipeline.state.init_db = lambda: None
        pipeline._load_profile = dict

    def test_health_summary_includes_each_source(self, config, monkeypatch):
        """Результат run() содержит health со статусом каждого источника."""
        from kb_agent.sources.manager import SourceCollectionResult

        def fake_run_collection(config, state):
            result = SourceCollectionResult()
            result.by_source = {
                "tg_rubiconzone": {"new": 5, "spam": 0, "skipped": 0},
                "yt_amorev": {"error": "yt-dlp timeout"},
            }
            result.errors = ["yt_amorev: yt-dlp timeout"]
            result.skipped = {"yt_test": "источник выключен"}
            return result

        import kb_agent.processing.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "run_collection", fake_run_collection)

        pipeline = Pipeline(config)
        self._patch_pipeline(monkeypatch, pipeline)
        pipeline._process_queue = lambda: {
            "processed": 0,
            "created": 0,
            "skipped": 0,
            "relevance_skipped": 0,
            "failed": 0,
        }
        pipeline.catalog.generate = lambda: Path("/tmp/catalog.md")
        pipeline._write_vault_digest = lambda *a, **k: Path("/tmp/digest.md")

        try:
            result = pipeline.run()
        finally:
            pipeline.close()

        health = result["health"]
        assert "tg_rubiconzone" in health
        assert health["tg_rubiconzone"] == {
            "status": "ok",
            "new": 5,
            "skipped": 0,
            "failed": 0,
            "error": None,
        }

        assert health["yt_amorev"] == {
            "status": "failed",
            "new": 0,
            "skipped": 0,
            "failed": 1,
            "error": "yt-dlp timeout",
        }

        assert health["yt_test"] == {
            "status": "skipped",
            "new": 0,
            "skipped": 1,
            "failed": 0,
            "error": "источник выключен",
        }

        summary = result["summary"]
        assert summary == {
            "total_sources": 3,
            "healthy": 1,
            "failed": 1,
            "skipped": 1,
        }
        assert result["ok"] is False

    def test_process_run_exits_nonzero_on_source_error(self, config, monkeypatch):
        """При ошибке сбора любого источника exit code = 1."""

        class FakePipeline:
            def run(self):
                return {
                    "ok": False,
                    "collection": {
                        "errors": ["yt_amorev: yt-dlp timeout"],
                        "telegram": {"new": 0, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "health": {
                        "yt_amorev": {
                            "status": "failed",
                            "new": 0,
                            "skipped": 0,
                            "failed": 1,
                            "error": "yt-dlp timeout",
                        }
                    },
                    "summary": {"total_sources": 1, "healthy": 0, "failed": 1, "skipped": 0},
                    "processing": {
                        "processed": 0,
                        "created": 0,
                        "skipped": 0,
                        "relevance_skipped": 0,
                        "failed": 0,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 1
        assert "Ошибки сбора источников:" in result.output
        assert "yt_amorev: yt-dlp timeout" in result.output

    def test_process_run_exits_zero_on_success(self, config, monkeypatch):
        """При успешном проходе всех источников exit code = 0 и сводка 'все в порядке'."""

        class FakePipeline:
            def run(self):
                return {
                    "ok": True,
                    "collection": {
                        "errors": [],
                        "telegram": {"new": 3, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "health": {
                        "tg_rubiconzone": {
                            "status": "ok",
                            "new": 3,
                            "skipped": 0,
                            "failed": 0,
                            "error": None,
                        }
                    },
                    "summary": {"total_sources": 1, "healthy": 1, "failed": 0, "skipped": 0},
                    "processing": {
                        "processed": 3,
                        "created": 3,
                        "skipped": 0,
                        "relevance_skipped": 0,
                        "failed": 0,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 0
        assert "Все источники в порядке" in result.output

    def test_cli_prints_source_errors(self, config, monkeypatch):
        """CLI печатает ошибки сбора с source_id и текстом ошибки."""

        class FakePipeline:
            def run(self):
                return {
                    "ok": False,
                    "collection": {
                        "errors": [
                            "yt_amorev: yt-dlp timeout",
                            "tg_rubiconzone: канал недоступен",
                        ],
                        "telegram": {"new": 0, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "health": {
                        "yt_amorev": {
                            "status": "failed",
                            "new": 0,
                            "skipped": 0,
                            "failed": 1,
                            "error": "yt-dlp timeout",
                        },
                        "tg_rubiconzone": {
                            "status": "failed",
                            "new": 0,
                            "skipped": 0,
                            "failed": 1,
                            "error": "канал недоступен",
                        },
                    },
                    "summary": {"total_sources": 2, "healthy": 0, "failed": 2, "skipped": 0},
                    "processing": {
                        "processed": 0,
                        "created": 0,
                        "skipped": 0,
                        "relevance_skipped": 0,
                        "failed": 0,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 1
        assert "Ошибки сбора источников:" in result.output
        assert "yt_amorev: yt-dlp timeout" in result.output
        assert "tg_rubiconzone: канал недоступен" in result.output

    def test_json_output_contains_health_and_summary(self, config, monkeypatch):
        """JSON-вывод --json содержит секции health и summary."""

        class FakePipeline:
            def run(self):
                return {
                    "ok": False,
                    "collection": {
                        "errors": ["yt_amorev: yt-dlp timeout"],
                        "telegram": {"new": 0, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "health": {
                        "yt_amorev": {
                            "status": "failed",
                            "new": 0,
                            "skipped": 0,
                            "failed": 1,
                            "error": "yt-dlp timeout",
                        }
                    },
                    "summary": {"total_sources": 1, "healthy": 0, "failed": 1, "skipped": 0},
                    "processing": {
                        "processed": 0,
                        "created": 0,
                        "skipped": 0,
                        "relevance_skipped": 0,
                        "failed": 0,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run", "--json"])
        assert result.exit_code == 1
        import json

        data = json.loads(result.output)
        assert "health" in data
        assert "summary" in data
        assert data["health"]["yt_amorev"]["status"] == "failed"
        assert data["summary"]["failed"] == 1

    def test_processing_failed_block_and_exit_one(self, config, monkeypatch):
        """Ошибки обработки очереди (processing_failed > 0) — отдельный блок и exit 1."""

        class FakePipeline:
            def run(self):
                return {
                    "ok": False,
                    "collection": {
                        "errors": [],
                        "telegram": {"new": 1, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "health": {
                        "tg_rubiconzone": {
                            "status": "ok",
                            "new": 1,
                            "skipped": 0,
                            "failed": 0,
                            "error": None,
                        }
                    },
                    "summary": {"total_sources": 1, "healthy": 1, "failed": 0, "skipped": 0},
                    "processing": {
                        "processed": 5,
                        "created": 3,
                        "skipped": 1,
                        "relevance_skipped": 0,
                        "failed": 2,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 1
        assert "Ошибок обработки очереди: 2" in result.output

    def test_exit_code_two_on_config_error(self, config, monkeypatch):
        """Критическая ошибка конфигурации — exit code 2."""
        import kb_agent.cli.commands.process as process_cmd_mod

        monkeypatch.setattr(
            process_cmd_mod,
            "load_config",
            lambda: (_ for _ in ()).throw(RuntimeError("не загружен профиль")),
        )
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 2
        assert "Критическая ошибка конфигурации" in result.output
