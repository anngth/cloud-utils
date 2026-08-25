import re

from messages import fmt

_TOKEN = re.compile(r"^==>\s+([^: (]+)")


def parse_info_token(stdout: str) -> str | None:
    if not stdout:
        return None
    first = stdout.splitlines()[0]
    match = _TOKEN.match(first)
    return match.group(1) if match else None


def info_token(kind: str, name: str, runner, ui) -> str | None:
    result = runner.run(["info", f"--{kind}", name], ui)
    if result.returncode != 0:
        return None
    return parse_info_token(result.stdout or "")


def token_matches_name(token: str | None, name: str) -> bool:
    if not token:
        return False
    slash = token.rfind("/")
    short = token[slash + 1 :] if slash != -1 else token
    return token == name or short == name


def detect_brew_type(name: str, runner, ui) -> str | None:
    formula_token = info_token("formula", name, runner, ui)
    cask_token = info_token("cask", name, runner, ui)
    is_formula = token_matches_name(formula_token, name)
    is_cask = token_matches_name(cask_token, name)

    if is_cask and is_formula:
        ui.warn(fmt("dual.type", name=name))
        ui.info(fmt("dual.hint", name=name))
        return None
    if is_cask:
        return "cask"
    if is_formula:
        return "formula"
    ui.warn(fmt("not.found.brew", name=name))
    ui.info(fmt("not.found.brew.hint", name=name))
    return None
