# Participation in kb-agent development

We welcome contributions! This project is a local AI agent for personal knowledge bases. Here's how you can help.

## 🛠️ How to contribute

### 1. Fork and clone

```bash
git clone https://github.com/DenisZakharov2/personal-kb-agent.git
cd personal-kb-agent
```

### 2. Set up development environment

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package in development mode with dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks (optional)
pip install ruff
ruff check .
```

### 3. Create a branch

```bash
git checkout -b feature/your-feature-name
```

### 4. Make changes and test

```bash
# Run linting
ruff check .

# Run tests (when tests are added)
pytest

# Verify CLI works
kb-agent --help
kb-agent sources list
```

### 5. Commit and push

```bash
git add .
git commit -m "feat: describe your change"
git push origin feature/your-feature-name
```

### 6. Open a Pull Request

Describe what you changed and why. Screenshots or CLI output are welcome.

---

## 📁 Project structure

```
personal-kb-agent/
├── README.md              # Project documentation
├── LICENSE                # MIT license
├── pyproject.toml         # Project metadata and dependencies
├── .env.example           # Environment variables template
├── .gitignore             # Files excluded from git
├── kb_agent/              # Main package
│   ├── cli/               # CLI commands (Typer)
│   │   ├── commands/
│   │   │   ├── ask.py           # ask, search, insights, update check
│   │   │   ├── feedback.py      # feedback rate/tell/metrics/list
│   │   │   ├── interview.py     # interview init/status/periodic
│   │   │   ├── pending.py       # pending confirm/list
│   │   │   ├── process.py       # process run/digest/status
│   │   │   ├── self_improve.py  # self-improve rollback
│   │   │   └── sources.py       # sources add/remove/enable/disable/list
│   │   ├── output.py            # Shared JSON output helper
│   │   └── main.py              # CLI entry point
│   ├── core/                # Core logic
│   │   ├── interview.py         # InterviewManager
│   │   ├── insights.py          # InsightsEngine (themes, gaps, links)
│   │   ├── prompts.py           # Versioned system prompts (prompt_history)
│   │   ├── relevance.py         # Relevance Gate
│   │   ├── search.py            # FTS5 search + LLM answer
│   │   ├── self_improvement.py  # SelfImprovementEngine
│   │   └── update.py            # KnowledgeUpdater
│   ├── processing/            # Processing pipeline
│   │   ├── llm_client.py        # LLM client (OpenAI-compatible, retries)
│   │   └── pipeline.py          # Pipeline (collect → relevance → LLM → vault)
│   ├── sources/               # Data sources
│   │   ├── base.py              # BaseSource abstract class
│   │   ├── registry.py          # SourceRegistry (sources.yaml)
│   │   ├── manager.py           # SourceManager (collect all/one)
│   │   ├── telegram.py          # Telegram collector
│   │   └── youtube.py           # YouTube collector
│   ├── storage/               # Storage layer
│   │   ├── state.py             # StateManager (SQLite)
│   │   ├── vault.py             # VaultManager (Obsidian vault)
│   │   ├── catalog.py           # CatalogGenerator (Каталог.md)
│   │   └── pending.py           # PendingManager (pending changes)
│   ├── agents/                # External agent integration
│   │   └── mcp_server.py        # MCP server (kb-agent mcp-serve)
│   ├── feedback/              # Feedback collection
│   │   ├── collector.py         # FeedbackCollector
│   │   └── metrics.py           # FeedbackMetricsCollector
│   ├── config.py              # Application configuration
│   ├── utils/
│   │   └── logger.py            # Logging setup
│   └── __init__.py
├── config/                    # Configuration templates
│   ├── sources.example.yaml   # Sources template
│   ├── user_profile.example.yaml  # Profile template
│   └── prompts/
│       ├── agent_prompt.md    # System prompt (search/answer)
│       ├── answer_prompt.md   # Answer prompt (search/answer)
│       └── summarize_prompt.md  # Summarize prompt (pipeline)
├── scripts/                   # Utility scripts
│   ├── prepare_stage4_write.sh # Environment check (Stage 4)
│   └── legacy/                # Legacy collectors (_queue.jsonl), not used by app
├── tests/                     # Tests (38 test files, 297 tests)
├── docs/                      # Documentation
└── SKILL.md                   # MCP skill for AI agents
```

---

## 🎯 Areas where help is needed

- **New sources**: Implement `BaseSource` for websites, RSS, arXiv, personal files
- **Tests**: Add pytest coverage for pipeline, relevance gate, storage
- **Documentation**: Translate README to English, add more examples
- **CI/CD**: Add GitHub Actions for linting and testing
- **Packaging**: Publish to PyPI

---

## 📝 Commit conventions

Use conventional commits:

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation changes
- `refactor:` — code refactoring
- `test:` — adding tests
- `chore:` — maintenance tasks

Example: `feat: add YouTube source with subtitles`

---

## 📄 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

## ❓ Questions?

Open an [issue](https://github.com/DenisZakharov2/personal-kb-agent/issues) or start a discussion.
