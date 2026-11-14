#!/usr/bin/env python3
"""Блокирующая проверка типов (mypy) с зафиксированным baseline.

Раньше mypy запускался с ``|| true`` и был чисто декоративным: любые новые
ошибки типов проходили незамеченными. Теперь проверка блокирующая, но
исторический долг не мешает работать:

* baseline (текущее число ошибок) лежит в ``ci/mypy-baseline.txt``;
* CI падает, если число ошибок **выросло** (регрессия);
* baseline можно обновить осознанно: ``python scripts/check_mypy_baseline.py --update``.

Использование:
    python scripts/check_mypy_baseline.py            # проверка (exit 1 при росте)
    python scripts/check_mypy_baseline.py --update   # обновить baseline
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "ci" / "mypy-baseline.txt"
MYPY_TARGETS = ["kb_agent"]

# «Found 119 errors in 25 files (checked 47 source files)»
_ERRORS_RE = re.compile(r"Found (?P<count>\d+) errors? in (?P<files>\d+) files?")


def read_baseline() -> int:
    """Читает зафиксированное число ошибок из файла baseline."""
    if not BASELINE_FILE.exists():
        return 0
    text = BASELINE_FILE.read_text(encoding="utf-8").strip()
    match = re.search(r"\d+", text)
    return int(match.group()) if match else 0


def write_baseline(count: int) -> None:
    """Фиксирует новое значение baseline."""
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        f"# Baseline mypy: максимально допустимое число ошибок типов.\n"
        f"# Обновляется командой: python scripts/check_mypy_baseline.py --update\n"
        f"{count}\n",
        encoding="utf-8",
    )


def run_mypy() -> tuple[int, str]:
    """Запускает mypy и возвращает (число ошибок, полный вывод)."""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", *MYPY_TARGETS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    match = _ERRORS_RE.search(output)
    if match:
        return int(match.group("count")), output
    # mypy не нашёл ошибок — «Success, no issues found».
    if result.returncode == 0:
        return 0, output
    # Не удалось разобрать вывод: считаем это ошибкой проверки.
    print(output, file=sys.stderr)
    return -1, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка mypy по baseline")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Обновить baseline текущим числом ошибок",
    )
    args = parser.parse_args()

    count, output = run_mypy()
    if count < 0:
        print("✗ Не удалось получить результат mypy", file=sys.stderr)
        return 1

    baseline = read_baseline()

    if args.update:
        write_baseline(count)
        print(f"✓ Baseline mypy обновлён: {count}")
        return 0

    if count > baseline:
        print(output, file=sys.stderr)
        print(
            f"✗ mypy: ошибок {count}, baseline {baseline} "
            f"(регрессия: +{count - baseline}).\n"
            f"  Исправьте новые ошибки или осознанно обновите baseline:\n"
            f"  python scripts/check_mypy_baseline.py --update",
            file=sys.stderr,
        )
        return 1

    print(f"✓ mypy: ошибок {count} (baseline {baseline}) — регрессий нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
