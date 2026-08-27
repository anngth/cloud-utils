import json
import os
import tempfile
from dataclasses import dataclass

from messages import fmt

FIELDS = ("formulas", "casks", "taps")
DEFAULT_CONFIG_SUFFIX = (
    "Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
)


@dataclass
class BudPaths:
    config_dir: str
    desired_file: str
    example_file: str


def resolve_paths(env: dict) -> BudPaths:
    config_dir = env.get("CLOUD_UTILS_CONFIG_DIR") or os.path.join(
        env.get("HOME") or "", DEFAULT_CONFIG_SUFFIX
    )
    return BudPaths(
        config_dir=config_dir,
        desired_file=os.path.join(config_dir, "bud", "desired.json"),
        example_file=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "desired.example.json"
        ),
    )


def normalize(doc: dict) -> dict:
    return {
        "version": doc["version"],
        **{field: sorted(set(doc[field])) for field in FIELDS},
    }


def _is_nonempty_string(value) -> bool:
    return isinstance(value, str) and value != ""


def _is_valid_desired(value) -> bool:
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    for field in FIELDS:
        items = value.get(field)
        if not isinstance(items, list) or any(
            not _is_nonempty_string(item) for item in items
        ):
            return False
    return True


def save_desired(path: str, doc: dict) -> None:
    if not _is_valid_desired(doc):
        raise ValueError(fmt("desired.invalid", file=path))
    normalized = normalize(doc)
    dest_dir = os.path.dirname(path) or "."
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError as err:
        raise OSError(fmt("config.dir")) from err
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".desired.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as err:
        raise ValueError(fmt("desired.invalid", file=path)) from err


def load_desired(env: dict, brew_taps_fn=None) -> dict:
    paths = resolve_paths(env)
    try:
        document = _read_json(paths.desired_file)
    except FileNotFoundError:
        pass
    else:
        if not _is_valid_desired(document):
            raise ValueError(fmt("desired.invalid", file=paths.desired_file))
        return document

    try:
        document = _read_json(paths.example_file)
    except FileNotFoundError as err:
        raise FileNotFoundError(
            fmt("desired.example.missing", file=paths.example_file)
        ) from err
    if not _is_valid_desired(document):
        raise ValueError(fmt("desired.invalid", file=paths.example_file))

    if not document["taps"] and brew_taps_fn is not None:
        document = {**document, "taps": list(brew_taps_fn())}

    document = normalize(document)
    save_desired(paths.desired_file, document)
    return document
