# NovaCode

A terminal AI coding agent built from scratch in Python, similar to Claude Code.

NovaCode turns your terminal into an AI pair programmer: it reads and writes files, runs commands, searches code, and coordinates multiple sub-agents working in parallel — all driven by an autonomous ReAct-style agent loop with streaming output and a full permission system.

<!-- TODO: add a screenshot / demo gif -->

## ✨ Features

- **Multi-protocol LLM** — unified abstraction over Anthropic and OpenAI APIs (including DeepSeek-compatible endpoints), with streaming responses and multi-turn conversation.
- **ReAct agent loop** — autonomous "think → call tool → observe" execution, with batched tool execution (concurrent read-only + serial side-effects) and cancellation / timeout handling.
- **Tool system** — built-in read / write / edit file, bash, glob, and grep tools, extensible via MCP (Model Context Protocol) and Skill packs.
- **Context management** — two-layer compression (offload oversized tool results + LLM summarization) with token estimation, retry and circuit breaker, plus cross-session long-term memory.
- **Permission system** — five-layer defense: dangerous-command blacklist, path sandbox (symlink-aware), rule engine, mode-based fallback, and human-in-the-loop approval.
- **Multi-agent collaboration** — create a Team, spawn sub-agents in isolated Git Worktrees, and coordinate them via a file-based mailbox with cross-process file locking.
- **Slash commands & Hooks** — built-in command framework with autocompletion, and lifecycle hooks (shell / prompt / HTTP) for automation.

## 🧱 Architecture

```
src/novacode/
├── agent/        # ReAct loop, sub-agent execution, fork
├── tool/         # tool registry + 6 core tools + filtering
├── llm/          # provider abstraction (Anthropic / OpenAI)
├── prompt/       # modular system prompt assembly
├── permission/   # 5-layer permission pipeline
├── compact/      # context compression + token estimation
├── memory/       # long-term memory
├── session/      # JSONL session persistence
├── mcp/          # MCP client
├── skills/       # Skill packs
├── hook/         # lifecycle hooks
├── subagent/     # sub-agent role definitions
├── task/         # background task manager
├── worktree/     # Git worktree isolation
├── team/         # multi-agent team collaboration
├── command/      # slash command framework
└── tui/          # terminal UI (prompt_toolkit + Rich)
```

## 🛠 Tech Stack

Python 3.12 · asyncio · prompt_toolkit · Rich · Anthropic SDK · OpenAI SDK · MCP · PyYAML

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install

```bash
git clone https://github.com/<your-username>/novacode.git
cd novacode
uv sync            # or: pip install -e .
```

### Configure

```bash
cp .novacode/config.yaml.example .novacode/config.yaml
```

Edit `.novacode/config.yaml` and fill in your LLM provider credentials:

```yaml
providers:
  - name: "DeepSeek"
    protocol: openai
    api_key: "sk-xxxxx"           # your API key
    model: "deepseek-chat"
    base_url: "https://api.deepseek.com"
    context_window: 1000000
```

### Run

```bash
uv run novacode      # or: python -m novacode
```

Type a request and NovaCode will plan, call tools, and complete the task. Use `/help` to see available slash commands.

## 🧪 Testing

```bash
pytest -q              # 750+ tests
ruff check src tests   # lint
mypy src/novacode      # type check
```

## 📄 License

MIT
