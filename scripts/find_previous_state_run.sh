#!/usr/bin/env bash
# Ищет последний завершённый запуск workflow, в котором есть артефакт состояния.
#
# Зачем: `actions/download-artifact` без параметра `run-id` скачивает артефакты
# ТЕКУЩЕГО запуска, а не предыдущего. Без явного `run-id` состояние базы
# знаний никогда не восстанавливалось бы между ежедневными прогонами.
#
# Печатает ID запуска (или пустую строку, если подходящих запусков нет).
# Требует GH_TOKEN с правом чтения Actions.
#
# Переменные окружения:
#   GITHUB_REPOSITORY — "owner/repo" (задаётся GitHub Actions автоматически)
#   WORKFLOW_FILE    — имя workflow-файла (по умолчанию collect.yml)

set -euo pipefail

REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY не задан}"
WORKFLOW="${WORKFLOW_FILE:-collect.yml}"
CURRENT_RUN="${GITHUB_RUN_ID:-}"

# Список последних запусков workflow (новые первыми).
runs_json="$(gh api "repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=30")"

# Перебираем завершённые запуски (кроме текущего) и ищем первый с артефактом.
run_ids="$(printf '%s' "${runs_json}" \
    | jq -r --arg current "${CURRENT_RUN}" \
        '.workflow_runs[] | select(.status == "completed" and (.id | tostring) != $current) | .id')"

for run_id in ${run_ids}; do
    has_artifact="$(gh api "repos/${REPO}/actions/runs/${run_id}/artifacts" \
        --jq 'any(.artifacts[]?; .name == "kb-state")' 2>/dev/null || echo false)"
    if [ "${has_artifact}" = "true" ]; then
        echo "${run_id}"
        exit 0
    fi
done

# Подходящих запусков нет — пустой вывод (вызывающий скрипт начнёт с чистой базы).
exit 0
