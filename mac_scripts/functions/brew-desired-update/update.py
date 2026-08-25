from brew import ensure_tap, load_brew_state
from messages import fmt


def collect_casks_to_upgrade(desired_casks, installed) -> list[str]:
    has = set(installed)
    return [cask for cask in desired_casks if cask in has]


def filter_excluded(casks, exclude) -> list[str]:
    excluded = set(exclude)
    return [cask for cask in casks if cask not in excluded]


def _format_name_list(names, limit: int = 8) -> str:
    if not names:
        return ""
    if len(names) <= limit:
        return " ".join(names)
    shown = " ".join(names[:limit])
    return f"{shown} … (+{len(names) - limit} more)"


def run_update(exclude, document, runner, ui) -> int:
    formulas = document.get("formulas") or []
    taps = document.get("taps") or []
    casks = document.get("casks") or []

    ui.active(
        f"Desired lists: {len(formulas)} formulae · {len(taps)} taps · {len(casks)} casks"
    )
    ui.step("Loading Homebrew state")
    state = load_brew_state(runner, ui)
    ui.active("Loaded Homebrew state")

    missing_taps = [tap for tap in taps if tap not in state["taps"]]
    if missing_taps:
        missing_label = (
            f"Ensuring taps · {len(missing_taps)} missing: {_format_name_list(missing_taps)}"
        )
        ui.step(missing_label)
        for tap in missing_taps:
            if not ensure_tap(tap, runner):
                ui.error(fmt("tap.failed", name=tap))
                return 1
        ui.active(
            f"Ensured taps · {len(missing_taps)} missing: {_format_name_list(missing_taps)}"
        )
    else:
        ui.active("Ensured taps · all present")

    ui.step("Updating Homebrew")
    result = runner.run(["update"], ui)
    if result.returncode != 0:
        ui.error(fmt("brew.update.failed"))
        return 1
    ui.active("Updated Homebrew")

    ui.step("Upgrading formulae")
    result = runner.run(["upgrade", "--formula", "-y"], ui)
    if result.returncode != 0:
        ui.error(fmt("brew.upgrade.formula.failed"))
        return 1
    ui.active("Upgraded formulae")

    ui.step("Repairing taps")
    result = runner.run(["tap", "--repair"], ui)
    if result.returncode != 0:
        ui.error(fmt("brew.tap.repair.failed"))
        return 1
    ui.active("Repaired taps")

    ui.step("Reloading Homebrew state")
    state = load_brew_state(runner, ui)
    ui.active("Reloaded Homebrew state")

    eligible = collect_casks_to_upgrade(casks, state["casks"])
    to_upgrade = filter_excluded(eligible, exclude)
    if exclude:
        ui.info(fmt("exclude.info", n=len(exclude), names=_format_name_list(exclude)))

    if to_upgrade:
        ui.step(f"Upgrading casks · {len(to_upgrade)} of {len(eligible)} eligible")
        result = runner.run(["upgrade", "--cask", "-y", *to_upgrade], ui)
        if result.returncode != 0:
            ui.error(fmt("brew.upgrade.cask.failed"))
            return 1
        ui.active(f"Upgraded casks · {len(to_upgrade)} of {len(eligible)} eligible")
    elif eligible and exclude:
        ui.info(fmt("casks.all_excluded"))
    else:
        ui.info(fmt("casks.none_installed"))

    ui.step("Cleaning up Homebrew")
    result = runner.run(["cleanup", "--prune=1"], ui)
    if result.returncode != 0:
        ui.error(fmt("brew.cleanup.failed"))
        return 1
    ui.active("Cleaned up Homebrew")
    return 0
