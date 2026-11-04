#!/usr/bin/env bash
# Скрипт подготовки к новой сессии: реализация write-операций в MCP-сервере.
# Запускать в начале новой сессии перед началом кодирования.
#
# Использование:
#   cd /путь/к/кб-тгк
#   bash scripts/prepare_stage4_write.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Подготовка к новой сессии: write-операции MCP (Этап 4) ==="

# 1. Проверка окружения
if [ ! -d ".venv" ]; then
    echo "ОШИБКА: виртуальное окружение .venv не найдено. Запустите scripts/init_stage1.sh сначала." >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# 2. Проверка, что Этап 4 read-only уже работает
echo "Проверка MCP-сервера (read-only)..."
python3 -c "
from kb_agent.agents.mcp_server import app, handle_list_tools
import asyncio
tools = asyncio.run(handle_list_tools())
tool_names = [t.name for t in tools]
required = ['kb_search', 'kb_get_note', 'kb_get_insights']
for r in required:
    assert r in tool_names, f'Отсутствует инструмент: {r}'
print('Read-only MCP инструменты OK:', tool_names)
"

# 3. Проверка existing файлов Этапа 4
echo "Проверка файлов Этапа 4..."
for f in SKILL.md .cursor/mcp.json .cursorrules config/prompts/agent_prompt.md; do
    if [ -f "$f" ]; then
        echo "  OK: $f"
    else
        echo "  ОШИБКА: $f не найден" >&2
        exit 1
    fi
done

# 4. Показать план
echo ""
echo "=== План для новой сессии ==="
echo "1. Добавить pending_changes таблицу в kb_agent/storage/state.py"
echo "2. Создать kb_agent/storage/pending.py — менеджер pending-запросов"
echo "3. Добавить write-инструменты в kb_agent/agents/mcp_server.py:"
echo "   - kb_add_source, kb_remove_source, kb_enable_source"
echo "   - kb_update_profile, kb_revert_prompt"
echo "   - kb_confirm_change (подтверждение человеком)"
echo "4. Добавить CLI команду kb-agent confirm <id>"
echo "5. Протестировать поток: add_source — pending — confirm — применение"
echo ""
echo "Готово к началу кодирования write-операций MCP."
