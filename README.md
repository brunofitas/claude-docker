# claude-docker

Run [Claude Code](https://docs.anthropic.com/en/docs/claude-code) inside a Docker container, sandboxed from your host system. Automatically reuses your existing Claude login — no API key required.

## Why?

Claude Code with `bypassPermissions` mode can read, write, and execute anything on your machine. Running it inside Docker gives you the power of full autonomy with the safety of containerization — Claude can only touch the files you explicitly mount.

## Requirements

- [Docker](https://docs.docker.com/get-docker/)
- Python 3.10+
- An existing Claude Code login on your host machine

## Install

```bash
pip install claude-docker
```

Or with [pipx](https://pipx.pypa.io/) (recommended):

```bash
pipx install claude-docker
```

Or from source:

```bash
git clone https://github.com/brunofitas/claude-docker.git
cd claude-docker
pip install .
```

## Usage

```bash
# Run Claude Code in the current directory (default: bypassPermissions)
claude-docker

# Choose a permission mode
claude-docker --permission-mode default
claude-docker --permission-mode acceptEdits
claude-docker --permission-mode plan
claude-docker --permission-mode auto
claude-docker --permission-mode dontAsk
claude-docker --permission-mode bypassPermissions

# Pass arguments to Claude
claude-docker -p "explain this codebase"

# Force rebuild the Docker image (after updates)
claude-docker --build

# Combine options
claude-docker --permission-mode plan --build -p "review this code"
```

### Permission Modes

| Mode | Behavior |
|------|----------|
| `default` | Claude reads files without asking |
| `acceptEdits` | Claude reads and edits files without asking |
| `plan` | Claude reads and explores but does not make edits |
| `auto` | All actions execute without prompts (requires Team plan) |
| `dontAsk` | Only pre-approved tools execute; everything else is blocked |
| `bypassPermissions` | All permission checks are skipped (default in Docker) |

## How it works

1. Builds a Docker image with Claude Code installed via npm
2. Extracts your OAuth token from the platform's credential store
3. Mounts your current directory and Claude config into the container
4. Launches Claude Code with the selected permission mode inside the sandboxed container

### Authentication

`claude-docker` automatically retrieves your OAuth token from:

| Platform | Credential Store |
|----------|-----------------|
| macOS | Keychain (`security`) |
| Linux | libsecret (`secret-tool`) |
| Windows | Credential Manager (PowerShell) |

You can also set the token manually:

```bash
export CLAUDE_CODE_OAUTH_TOKEN="your-token-here"
claude-docker
```

Or run `/login` inside the container if automatic detection fails.

## Development

```bash
git clone https://github.com/brunofitas/claude-docker.git
cd claude-docker
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format --check src/ tests/
```

## License

MIT
