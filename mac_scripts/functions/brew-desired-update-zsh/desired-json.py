#!/usr/bin/env python3
"""Read/write bud desired.json for the zsh bud CLI."""

import json
import os
import sys
import tempfile

FIELDS = ("formulas", "casks", "taps")


def load(path):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("version") != 1:
        raise SystemExit("invalid desired.json")
    for field in FIELDS:
        values = document.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            raise SystemExit("invalid desired.json")
    return document


def cmd_get(path, field):
    if field not in FIELDS:
        raise SystemExit(f"unknown field: {field}")
    items = load(path)[field]
    sys.stdout.write("\n".join(items))
    if items:
        sys.stdout.write("\n")


def cmd_write(path):
    data = {"version": 1, "formulas": [], "casks": [], "taps": []}
    section = None
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if line in ("--formulas--", "--casks--", "--taps--"):
            section = line.strip("-")
            continue
        if section and line:
            data[section].append(line)
    for field in FIELDS:
        data[field] = sorted(set(data[field]))
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".desired.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: desired-json.py get <file> <field> | write <file>")
    command = sys.argv[1]
    if command == "get":
        cmd_get(sys.argv[2], sys.argv[3])
    elif command == "write":
        cmd_write(sys.argv[2])
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
