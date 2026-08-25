from brew import ensure_tap
from config import normalize
from detect import detect_brew_type, info_token, parse_info_token
from messages import fmt

SKIP_CASK_TAPS = frozenset({"homebrew/cask", "homebrew/cask-fonts"})
LIST_KEY = {"cask": "casks", "formula": "formulas", "tap": "taps"}


def _is_tap_formula_spec(name: str) -> bool:
    return len(name.split("/")) == 3


def _tap_from_formula_spec(name: str) -> str:
    return name[: name.rfind("/")]


def _formula_name_from_spec(name: str) -> str:
    return name[name.rfind("/") + 1 :]


def _copy_doc(document: dict) -> dict:
    return {
        "version": document["version"],
        "formulas": list(document["formulas"]),
        "casks": list(document["casks"]),
        "taps": list(document["taps"]),
    }


def _assign_doc(document: dict, updated: dict) -> None:
    document["version"] = updated["version"]
    document["formulas"] = updated["formulas"]
    document["casks"] = updated["casks"]
    document["taps"] = updated["taps"]


def _cask_tap_from_search(stdout: str, pkg_name: str) -> str | None:
    for line in stdout.splitlines():
        if pkg_name in line and "/" in line:
            parts = line.strip().split("/")
            if len(parts) < 2:
                return None
            tap_name = f"{parts[0]}/{parts[1]}"
            if tap_name in SKIP_CASK_TAPS:
                return None
            return tap_name
    return None


def _listed_taps(runner, ui) -> list[str]:
    result = runner.run(["tap"], ui)
    return [line for line in (result.stdout or "").splitlines() if line]


def _ensure_cask_tap(pkg_name: str, runner, ui) -> bool:
    search = runner.run(["search", "--cask", pkg_name], ui)
    tap_name = _cask_tap_from_search(search.stdout or "", pkg_name)
    if not tap_name:
        return True
    if tap_name in _listed_taps(runner, ui):
        return True
    ui.step(f"Adding required tap: {tap_name}")
    if not ensure_tap(tap_name, runner):
        ui.error(fmt("tap.failed", name=tap_name))
        return False
    return True


def _add_one(pkg_name: str, force_type: str | None, doc: dict, runner, ui) -> bool:
    ui.step(f"Adding {pkg_name}")
    pkg_type = force_type
    list_name = pkg_name
    tap_for_formula = ""
    info_stdout = ""

    if not pkg_type:
        if _is_tap_formula_spec(pkg_name):
            pkg_type = "formula"
            list_name = _formula_name_from_spec(pkg_name)
            tap_for_formula = _tap_from_formula_spec(pkg_name)
        elif "/" in pkg_name:
            pkg_type = "tap"
        else:
            pkg_type = detect_brew_type(pkg_name, runner, ui)
            if pkg_type is None:
                return False
    elif pkg_type == "tap":
        if "/" not in pkg_name or _is_tap_formula_spec(pkg_name):
            ui.warn(fmt("tap.name.invalid"))
            return False
    elif pkg_type == "formula" and _is_tap_formula_spec(pkg_name):
        list_name = _formula_name_from_spec(pkg_name)
        tap_for_formula = _tap_from_formula_spec(pkg_name)
    elif pkg_type in ("cask", "formula"):
        result = runner.run(["info", f"--{pkg_type}", pkg_name], ui)
        if result.returncode != 0:
            ui.warn(fmt("not.found.type", name=pkg_name, type=pkg_type))
            return False
        info_stdout = result.stdout or ""

    if pkg_type == "formula" and not tap_for_formula:
        token = (
            parse_info_token(info_stdout)
            if info_stdout
            else info_token("formula", pkg_name, runner, ui)
        )
        if token and "/" in token:
            tap_for_formula = token[: token.rfind("/")]

    list_key = LIST_KEY[pkg_type]
    if list_name in doc[list_key]:
        ui.warn(fmt("already.in.list", name=list_name, type=pkg_type))
        return False

    if tap_for_formula and tap_for_formula not in doc["taps"]:
        doc["taps"].append(tap_for_formula)
        ui.active(fmt("added.tap", name=tap_for_formula, n=len(doc["taps"])))

    if pkg_type == "cask" and not _ensure_cask_tap(pkg_name, runner, ui):
        return False

    doc[list_key].append(list_name)
    ui.active(
        fmt("added.item", name=list_name, type=pkg_type, n=len(doc[list_key]))
    )
    return True


def run_add(names, force_type, document, runner, ui, save_fn) -> int:
    doc = _copy_doc(document)
    succeeded = 0
    failed = 0
    for name in names:
        if _add_one(name, force_type, doc, runner, ui):
            succeeded += 1
        else:
            failed += 1
    if succeeded:
        _assign_doc(document, normalize(doc))
        save_fn(document)
    return 1 if failed else 0


def run_remove(names, document, ui, save_fn) -> int:
    doc = _copy_doc(document)
    succeeded = 0
    failed = 0
    for name in names:
        ui.step(f"Removing {name}")
        if name in doc["casks"]:
            doc["casks"] = [item for item in doc["casks"] if item != name]
            ui.active(fmt("removed.item", name=name, type="cask", n=len(doc["casks"])))
            succeeded += 1
        elif name in doc["formulas"]:
            doc["formulas"] = [item for item in doc["formulas"] if item != name]
            ui.active(
                fmt("removed.item", name=name, type="formula", n=len(doc["formulas"]))
            )
            succeeded += 1
        elif name in doc["taps"]:
            doc["taps"] = [item for item in doc["taps"] if item != name]
            ui.active(fmt("removed.item", name=name, type="tap", n=len(doc["taps"])))
            succeeded += 1
        else:
            ui.warn(fmt("not.in.list", name=name))
            failed += 1
    if succeeded:
        _assign_doc(document, normalize(doc))
        save_fn(document)
    return 1 if failed else 0
