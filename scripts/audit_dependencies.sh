#!/usr/bin/env bash
# Аудит зависимостей проекта (блокирующая проверка для CI).
#
# Аудируется ИЗОЛИРОВАННОЕ окружение с зависимостями проекта, а не всё
# предустановленное окружение раннера. Причина: инструменты образа
# (pip/setuptools из состава Python на образе runner'а) не являются
# зависимостями проекта — их уязвимости не влияют на пользователей пакета,
# но ломали бы сборку посторонними срабатываниями.
#
# Использование:
#     scripts/audit_dependencies.sh [путь_к_venv]
#
# Переменные окружения:
#     PYTHON_BIN — интерпретатор для создания venv (по умолчанию python3)

set -euo pipefail

VENV_PATH="${1:-${RUNNER_TEMP:-/tmp}/kb-audit-env}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "→ Аудит зависимостей в изолированном окружении ${VENV_PATH}"

rm -rf "${VENV_PATH}"
"${PYTHON_BIN}" -m venv "${VENV_PATH}"

# Свежие pip/setuptools: иначе аудит ругается на инструменты самого venv.
"${VENV_PATH}/bin/pip" install --quiet --upgrade pip setuptools

# Зависимости проекта (включая dev-зависимости для полного покрытия).
"${VENV_PATH}/bin/pip" install --quiet ".[dev]"

# Сам инструмент аудита ставится последним, чтобы не тянуть лишнее в проект.
"${VENV_PATH}/bin/pip" install --quiet pip-audit

# pip-audit завершается с кодом 1, если найдена хотя бы одна уязвимость.
"${VENV_PATH}/bin/pip-audit" --desc

echo "✓ Уязвимостей в зависимостях проекта не найдено"
