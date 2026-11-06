# Second Brain (personal-kb-agent)

> Collect, structure, and use knowledge from Telegram and YouTube — together with your own AI agent.

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-green.svg)](https://python.org)
[![Obsidian](https://img.shields.io/badge/Obsidian-compatible-blueviolet)](https://obsidian.md)
[![MCP Ready](https://img.shields.io/badge/MCP-ready-orange)](https://modelcontextprotocol.io)

---

## 🎯 What is this and why do you need it?

### The Problem kb-agent Solves

You daily receive a stream of useful information from Telegram channels, YouTube, websites, and your own files. Some materials get lost, some forgotten, and connections between different sources rarely remain. To find a specific insight, you have to manually re-read dozens of posts.

**kb-agent** solves this: it automatically collects materials from your chosen sources, summarizes them via LLM, and saves structured notes to your Obsidian vault. Notes get tags, topics, and a link to the source; connections between notes are stored in the `note_links` table. All notes are indexed and searchable — both for you and for any AI agent (Cursor, Claude Code, Hermes, OpenCode, etc.).

### JTBD (Jobs To Be Done)

| Before | With kb-agent |
|--------|---------------|
| Manually re-read channels/videos to not miss useful content | Automatic collection of new posts and videos (via cron or manually) |
| Write important thoughts in different files, losing connections | Structured notes with tags, topics, and source links |
| Spend long time searching where you read about a topic | Full-text search across entire knowledge base in seconds (FTS5) |
| Forget which insights you've already discovered | Automatic insights — recurring themes, contradictions, hidden dependencies |
| Don't know where to start building a knowledge base | Introductory interview — agent helps define goals, topics, and sources |

---

## 💡 What You Get

### For Humans (Obsidian / KB Users)

| Benefit | How It Works |
|---------|--------------|
| **Nothing gets lost** | Post/video → queue → relevance filter → summarization → note in vault (unsuitable skipped) |
| **See connections between sources** | Notes get tags, topics, and source links; connections stored in `note_links` |
| **Quickly find what you need** | Full-text search (FTS5) + `kb-agent ask` command |
| **Get insights effortlessly** | Agent finds recurring themes and connections between notes (common tags, top-3) |
| **Control information flow** | Add/remove sources with one command, no code changes |
| **Quality control** | Rate each note (1–5), agent adapts to your preferences |

### For AI Agents (Cursor, Claude Code, Hermes, OpenCode, Kimi Claw, etc.)

| Benefit | How It Works |
|---------|--------------|
| **Read your knowledge base** | MCP server `kb-agent mcp-serve` provides `kb_search`, `kb_get_note`, `kb_get_insights` |
| **Search your notes** | FTS5 search via `kb_search` |
| **Get insights** | `kb_get_insights` returns connections between notes |
| **Configure without code** | Write-tools (`kb_add_source`, `kb_remove_source`, `kb_enable_source`, `kb_update_profile`, `kb_revert_prompt`) create pending-request, applied only after explicit human confirmation via `kb-agent confirm <id>` |

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/DenisZakharov2/personal-kb-agent.git
cd personal-kb-agent

# Install dependencies (including yt-dlp for YouTube)
pip install -e .

# Verify installation
kb-agent --version
```

### 2. Configuration (.env)

```bash
cp .env.example .env
# Edit .env — specify vault path, channels, LLM key
```

| Variable | What to specify | Example |
|----------|----------------|---------|
| `KB_VAULT_PATH` | Path to your Obsidian vault | `/home/user/Documents/KB` |
| `KB_TELEGRAM_CHANNELS` | Telegram channels (comma-separated) | `ragnarokzone,paragonzone,rubiconzone` |
| `KB_YOUTUBE_CHANNELS` | YouTube channels (full URLs) | `https://youtube.com/@amorev,https://youtube.com/@rubiconzone` |
| `KB_LLM_PROVIDER` | LLM provider | `openai`, `kimi`, `ollama`, `openrouter` |
| `KB_OPENAI_API_KEY` | Your API key | `sk-...` |
| `KB_OPENAI_BASE_URL` | Provider API URL | `https://api.openai.com/v1` |

### 3. First Interview (Required Before Start)

```bash
kb-agent interview init
```

The agent will ask several questions about goals, topics, sources, and collection frequency — and create your `user_profile.yaml`.

### 4. Run Pipeline

```bash
# Collect materials from all sources and summarize to vault
kb-agent process run

# Or get run digest
kb-agent process digest
```

#### Exit Codes for `kb-agent process run`

| Code | Meaning | When Returned |
|------|---------|---------------|
| `0` | Success | All sources collected without errors, queue processed without failures |
| `1` | Collection/processing error | At least one source collection error or queue processing failure (partial success = error) |
| `2` | Critical config error | Failed to load configuration (no profile, invalid paths, etc.) |

On any failure, CLI prints **Source Collection Errors:** block with `source_id` and error text for each failed source, and if `processing_failed > 0` — separate **Queue Processing Errors** block. JSON output (`--json`) includes `health` (per-source status) and `summary` (totals).

### 5. Working with Knowledge Base

```bash
# Ask a question and get answer with note references
kb-agent ask "What have I accumulated on AI agents?"

# Search notes (list)
kb-agent search "n8n automation"

# Insights — connections between notes (note_links table)
kb-agent insights

# Check for duplicates
kb-agent update check
```

### 6. Source Management

```bash
# List all sources
kb-agent sources list

# Add Telegram channel
kb-agent sources add telegram https://t.me/s/novyikanal

# Add YouTube channel
kb-agent sources add youtube https://youtube.com/@novyykanal

# Enable/disable source
kb-agent sources enable tg_ragnarokzone
kb-agent sources disable yt_staryykanal

# Remove source
kb-agent sources remove tg_staryykanal
```

> ⚠️ Commands `kb-agent sources ...` write to canonical source storage (SQLite). Changes from MCP tools (agent) create pending-request — must confirm via `kb-agent confirm <id>`.

### 7. MCP Server for AI Agents

```bash
# Start server (once, in terminal or as service)
kb-agent mcp-serve
```

Connect in your AI agent (e.g., in `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "kb-agent": {
      "command": "kb-agent",
      "args": ["mcp-serve"]
    }
  }
}
```

Now your agent can:
- Search your notes: `kb_search("AI agents")`
- Read note content: `kb_get_note("note_id")`
- Get insights: `kb_get_insights()`
- Propose source/profile changes: `kb_add_source(...)` → confirm via `kb-agent confirm <change_id>`

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       HUMAN                              │
│  Goals • Questions • Feedback • Source Management        │
└──────────────────────┬───────────────────────────────────┘
                       │ CLI / MCP
┌──────────────────────▼───────────────────────────────────┐
│                    kb-agent CLI / MCP                      │
│  ask • search • insights • sources • process • feedback   │
│  self-improve • interview • mcp-serve                      │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              Pipeline (Pipeline)                           │
│  Collection → Relevance Gate → LLM Summarization → Vault  │
└──────────────────────┬───────────────────────────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     ▼                 ▼                 ▼
  raw_queue        notes_fts (FTS5)    note_links
  (SQLite)         (full-text          (connections
                   search)             between notes)
```

**Stack:** Python 3.10+, SQLite + FTS5, Pydantic v2, Typer, Rich, httpx, MCP, PyYAML.

---

## 📋 Features by Stage

| Stage | What's Done | Status |
|-------|-------------|--------|
| **Stage 1** | Foundation, interview, core import, SQLite schema | ✅ |
| **Stage 2** | Reliable pipeline (collection + summarization + vault) | ✅ |
| **Stage 3** | Search (FTS5), LLM answers, duplicate detection, insights | ✅ |
| **Stage 4** | MCP server (read + write via pending), `--json` for CLI, source management | ✅ |
| **Stage 5** | Feedback, prompt versions & rollback, monthly interview, self-improvement | ✅ |

---

## 🔄 How Pipeline Works Under the Hood

1. **Collection** — new posts from Telegram and videos from YouTube go to `raw_queue`
2. **Relevance Gate** — material checked against your goals and topics from profile
3. **Summarization** — LLM restructures raw text into structured note (essence, ideas, tools, conclusions, tags)
4. **Writing** — note saved to thematic vault folder with YAML frontmatter
5. **Indexing** — note added to FTS5 index for search
6. **Catalog** — `Каталог.md` rebuilt automatically
7. **Digest** — `Дайджест.md` in vault with run statistics

---

## 🔐 Security & Privacy

### Data Transmission to External LLM

Notes, state DB, and profile are stored locally on your computer. However, **raw texts, found notes, and metadata are sent to the configured external OpenAI-compatible endpoint** (OpenAI, Kimi, OpenRouter, Ollama, etc.) for summarization and search answers:

| What is Sent | To Whom | When |
|--------------|---------|------|
| Raw post/video text (`raw_text`) | Configured LLM provider (`KB_OPENAI_BASE_URL`) | During summarization in pipeline (`kb-agent process run`) |
| Note snippets + metadata (ID, title, tags, source URL) | Configured LLM provider | During search answers (`kb-agent ask`, `kb_search` via MCP) |
| Source metadata (source_id, channel, canonical_url) | Configured LLM provider | During summarization and search |
| API key (`KB_OPENAI_API_KEY`) | External provider (only if not Ollama/local) | Every LLM request |

For full locality, use local provider (Ollama) without API key and enable local processing mode (see below).

### Local Processing Mode

Variable `KB_LOCAL_PROCESSING_MODE=true` enables mode where data is not sent to external LLM provider. Works only with local Ollama/local model. External providers (OpenAI, Kimi, OpenRouter) are blocked.

```bash
export KB_LOCAL_PROCESSING_MODE=true
kb-agent process run
```

### Redaction — Excluding Sensitive Data

Variables `KB_REDACT_FIELDS` and `KB_REDACT_SOURCES` allow excluding sensitive fields and sources from being sent to external LLM:

```bash
# Exclude email, phone, source URLs from LLM transmission
export KB_REDACT_FIELDS="email,phone,canonical_url"

# Exclude entire content of specific sources
export KB_REDACT_SOURCES="tg_private_chan,yt_internal_channel"
```

### Sensitivity Labels in MCP

Notes and sources can be marked as private (`is_private = 1`). Private notes **are not returned** to external agent via `kb_get_note` and are filtered from `kb_search` results for external MCP client. Sensitivity is marked by `is_private` and `sensitivity` fields (public/internal/private/secret) in structured MCP response.

### Untrusted Content Chain & Agent Actions

- **No confirm-tool in MCP.** Model cannot confirm change independently. Human confirmation — only via separate CLI channel: `kb-agent confirm <id>` (or `kb-agent confirm <id> --revision N`).
- **MCP write-tools** (`kb_add_source`, `kb_remove_source`, `kb_enable_source`, `kb_update_profile`, `kb_revert_prompt`) create pending-proposal but don't apply it. Application requires human confirmation via CLI.
- **Each pending-proposal contains:** diff, payload_hash, base_revision, author (created_by), expiration time (TTL/expires_at), unique identifier (change_id).
- **On confirmation, atomically verified:** status='pending', payload_hash (parameters not tampered), current object revision.
- **Provenance metadata** for each fragment (URL, source, note_id, `untrusted_content` label) included in MCP responses (`kb_search`, `kb_get_note`).
- **Source & URL Allowlist:** when adding source via MCP, host validated against allowlist of trusted domains (t.me, youtube.com, youtu.be, etc.). yt-dlp limited to known YouTube domains.
- **Prompt Injection Protection:** model has no direct write tools. Malicious text in note cannot lead to source deletion or profile change — only to creating pending-proposal requiring separate human confirmation.

---

## 📊 Feedback & Self-Improvement

```bash
# Rate a note (1–5)
kb-agent feedback rate <note_id> 5 --comment "Great summarization"

# Leave arbitrary message
kb-agent feedback tell "Need more code examples"

# View quality metrics
kb-agent feedback metrics

# View prompt improvement proposals
kb-agent self-improve
```

## Self-Improvement (self-improve)

All subcommands support `--json` (`-j`) flag for machine output.

```bash
# Show feedback metrics and proposals list
kb-agent self-improve

# List proposals (default: proposed status)
kb-agent self-improve list
kb-agent self-improve list --status proposed
kb-agent self-improve list --status rejected --json

# Propose prompt change
kb-agent self-improve propose-prompt agent_prompt "need clearer summarization" "new prompt text"
# or from file:
kb-agent self-improve propose-prompt agent_prompt "reason" --content-file prompts/new_agent_prompt.md

# Apply prompt proposal (requires test suite check and confirmation)
kb-agent self-improve apply-prompt <proposal_id> "final prompt text"
kb-agent self-improve apply-prompt <proposal_id> --content-file prompts/final.md --confirmed-by user

# Reject proposal
kb-agent self-improve reject <proposal_id>

# Propose config change
kb-agent self-improve propose-config data/sources.yaml "old" "new" "reason"

# Apply config proposal
kb-agent self-improve apply-config <proposal_id> "final value"

# Rollback prompt to version
kb-agent self-improve rollback agent_prompt 1
```

Proposals stored in SQLite (`prompt_proposals`) and follow path: `proposed → confirmed → applied/rejected/failed`.

Prompt and config application requires verification on test set (`tests/eval_set.json`) and explicit user confirmation — changes not applied automatically. Prompt rollback (`rollback`) also requires confirmation.

Agent collects your ratings (`kb-agent feedback rate/tell`) and calculates average score. Automatic improvement proposals generated based on accumulated feedback.

---

## 🤝 Contributing

Project is developed openly. You can:

- Report bugs and propose features via GitHub Issues
- Submit Pull Requests with improvements
- Add new sources (inherit `BaseSource` from `kb_agent/sources/base.py`)

All code changes under MIT license.

---

## 📄 License

MIT — see [`LICENSE`](LICENSE).

---

## 🔗 Related Projects

- [Obsidian](https://obsidian.md) — notes storage (vault)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io) — standard for AI agents
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube metadata and subtitles collection

---

*Made with ❤️ for conscious knowledge management. If this project helped you — give it ⭐️ and share with colleagues.*