mkdir -p scripts
cd "$(dirname "$0")/.."
python3 scripts/collect_telegram.py "$@"
python3 scripts/collect_youtube.py collect "$@"
python3 scripts/collect_youtube.py transcripts "$@"
