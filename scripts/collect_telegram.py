#!/usr/bin/env python3
"""Обёртка совместимости: старый путь запуска (scripts/collect_telegram.py).

Реальная реализация — scripts/legacy/collect_telegram.py.
Обёртка нужна, чтобы GitHub Actions workflow (.github/workflows/collect.yml),
в котором прописаны эти пути, продолжал работать без правки самого workflow.
Файлы очереди (_queue.jsonl, _done.txt, _transcripts/) по-прежнему пишутся
в каталог scripts/ — через KB_QUEUE_DIR.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
LEGACY_SCRIPT = SCRIPTS_DIR / "legacy" / "collect_telegram.py"

os.environ.setdefault("KB_QUEUE_DIR", str(SCRIPTS_DIR))
runpy.run_path(str(LEGACY_SCRIPT), run_name="__main__")
