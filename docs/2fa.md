# 2fa

Interactive TOTP helper.

```bash
2fa
```

Prompts for a Base32 secret (hidden), copies a 6-digit TOTP to the clipboard, and prints `Code copied: <code>`.

Requires `date`, `xxd`, `openssl`, `awk`, `tr`, and `pbcopy`.
