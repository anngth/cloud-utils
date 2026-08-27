# 2fa

Interactive TOTP helper.

```bash
2fa
2fa -h
```

Prompts for a Base32 secret (hidden), copies a 6-digit TOTP to the clipboard, and prints a framed `Code copied: <code>` message.

Requires Python 3.14 in the repository-local `.venv` and macOS `pbcopy`. Create
the environment from the repository root with:

```bash
uv sync --locked
```

Use `uv sync --locked --no-dev` for a runtime-only environment. The launcher
runs `.venv/bin/python -m twofa.cli` using the repository path relative to the
wrapper, so it works from any current directory. It never invokes `uv`,
activates an environment, or falls back to a system `python3`; if the local
interpreter is missing, it reports the setup command and exits.
