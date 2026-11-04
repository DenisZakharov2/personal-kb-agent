#!/usr/bin/env bash
# Legacy-сборщик: Telegram + YouTube в _queue.jsonl (приложением kb-agent не используется).
# Использование: bash scripts/legacy/run.sh [--limit N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python3 collect_telegram.py "$@"
python3 collect_youtube.py collect
python3 collect_youtube.py transcripts
