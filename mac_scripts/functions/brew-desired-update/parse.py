from dataclasses import dataclass

HELP = frozenset({"help", "-h", "--help"})
LIST = frozenset({"list", "ls"})
SUBCOMMANDS = HELP | LIST | frozenset({"add", "remove"})


@dataclass
class ParseResult:
    exclude: list[str]
    action: str | None
    rest: list[str]
    force_type: str | None
    names: list[str]


def parse_argv(argv: list[str]) -> ParseResult:
    exclude: list[str] = []
    force_type: str | None = None
    names: list[str] = []
    action: str | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-e", "--exclude"):
            i += 1
            while i < len(argv) and not argv[i].startswith("-") and argv[i] not in SUBCOMMANDS:
                exclude.append(argv[i])
                i += 1
            continue
        if tok in HELP:
            return ParseResult(exclude, "help", [], force_type, names)
        if tok in LIST:
            return ParseResult(exclude, "list", argv[i + 1 :], force_type, names)
        if tok == "add":
            action = "add"
            i += 1
            break
        if tok == "remove":
            action = "remove"
            i += 1
            break
        action = tok
        i += 1
        break
    if action == "add":
        while i < len(argv):
            t = argv[i]
            if t == "--cask":
                force_type = "cask"
            elif t == "--formula":
                force_type = "formula"
            elif t == "--tap":
                force_type = "tap"
            else:
                names.append(t)
            i += 1
    elif action == "remove":
        names = argv[i:]
    return ParseResult(exclude, action, [], force_type, names)
