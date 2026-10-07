import io
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from git_tools.selector import SelectorItem, create_selector_state, reduce_selector
from git_tools.ui import GitToolsUi


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


def _capture() -> tuple[GitToolsUi, io.StringIO, io.StringIO]:
    stdout, stderr = io.StringIO(), io.StringIO()
    return GitToolsUi(stdout, stderr), stdout, stderr


def test_all_gt_badge_headers_use_green_background() -> None:
    ui, stdout, _ = _capture()

    ui.usage()
    ui.render_backup_selector(
        "Select repos",
        create_selector_state([SelectorItem("git@github.com:org/app.git", "a")]),
    )

    assert "\x1b[42m" in stdout.getvalue()
    assert "\x1b[46m" not in stdout.getvalue()


def test_usage_matches_skm_style_sections_and_signatures() -> None:
    ui, stdout, _ = _capture()

    ui.usage()

    rendered = stdout.getvalue()
    lines = [line.rstrip() for line in _plain(rendered).splitlines()]
    assert next(line for line in lines if "Usage:" in line) == "◇  Usage: gt <command>"

    sections = ["Core", "Backup", "Notes"]
    section_indexes = [lines.index(f"◆  {section}") for section in sections]
    assert section_indexes == sorted(section_indexes)

    for first, continuation in (
        (
            "  gt backup [(-f | --force) | --dry-run]",
            "      Interactive select; force and dry-run are mutually exclusive",
        ),
        (
            "  gt backup --all [(-f | --force) | --dry-run]",
            "      Backup or preview every listed repo",
        ),
    ):
        index = lines.index(first)
        assert lines[index + 1] == continuation

    for line in (
        "  gt (help | -h | --help)  Show this help",
        "  gt push  Force push (safe --force-with-lease)",
        "  gt fetch [--sync-upstream]  Fetch with optional upstream sync",
        "  gt backup add <ssh-url> [<ssh-url> ...]  Add SSH URL(s) to managed list",
        "  gt backup remove <index|ssh-url>  Remove by 1-based index or URL",
        "  Selector: space toggle, a all, c clear, enter start, q quit",
        "  Remove indexes are 1-based (same as skm).",
        "  --force skips fingerprint short-circuit; cannot combine with --dry-run.",
    ):
        assert line in lines

    assert "■" not in rendered
    assert "-n|--new" not in rendered
    assert "backup <ssh-url>" not in rendered
    assert "backup stale" not in rendered
    assert "Run 'gt --help'" not in rendered


def test_status_renders_a_framed_success_item() -> None:
    ui, stdout, _ = _capture()

    ui.status("Checking backup group")

    assert "■ Checking backup group" in _plain(stdout.getvalue())
    assert not re.search(r"^--- ", stdout.getvalue(), re.MULTILINE)


def test_error_uses_skm_style_red_cross_prefix() -> None:
    ui, _, stderr = _capture()

    ui.error("Backup cancelled.")

    assert "❌" in stderr.getvalue()
    assert "Backup cancelled" in stderr.getvalue()


def test_error_detail_omits_an_additional_cross() -> None:
    ui, _, stderr = _capture()

    ui.error("Force push failed")
    ui.error_detail("permission denied")

    assert stderr.getvalue().count("❌") == 1
    assert "permission denied" in _plain(stderr.getvalue())


def test_semantic_frame_renders_headline_tones_details_and_footer() -> None:
    ui, stdout, _ = _capture()

    ui.begin("Fetch and sync repository")
    ui.status("Fetched origin/main")
    ui.status("No upstream configured", tone="warning")
    ui.status("Skipped upstream sync", tone="muted")
    ui.detail("Run: gt fetch --sync-upstream")
    ui.status("Push rejected", tone="failure")
    ui.detail("permission denied", tone="failure")
    ui.end()

    plain = _plain(stdout.getvalue())
    for expected in (
        "GT",
        "◇  Fetch and sync repository",
        "◆  Progress",
        "■ Fetched origin/main",
        "■ No upstream configured",
        "□ Skipped upstream sync",
        "    Run: gt fetch --sync-upstream",
        "■ Push rejected",
        "    permission denied",
        "└",
    ):
        assert expected in plain
    assert "\x1b[33m■\x1b[39m No upstream configured" in stdout.getvalue()
    assert "\x1b[90m□\x1b[39m Skipped upstream sync" in stdout.getvalue()
    assert "\x1b[90mRun: gt fetch --sync-upstream\x1b[39m" in stdout.getvalue()
    assert "\x1b[31m■\x1b[39m Push rejected" in stdout.getvalue()
    assert "\x1b[31mpermission denied\x1b[39m" in stdout.getvalue()


def test_title_and_list_end_frame_output_like_skm() -> None:
    ui, stdout, _ = _capture()

    ui.title("REPO BACKUP")
    ui.step("Done")
    ui.list_end("https://gitlab.com/example")

    assert "REPO BACKUP" in stdout.getvalue()
    assert "◇" in stdout.getvalue()
    assert "└" in stdout.getvalue()
    assert "https://gitlab.com/example" in stdout.getvalue()


def test_detail_indents_continuation_under_an_item_without_a_box() -> None:
    ui, stdout, _ = _capture()

    ui.item("ok  git@github.com:org/a.git")
    ui.detail("→ https://gitlab.com/example/a")

    plain = _plain(stdout.getvalue())
    assert re.search(r"■.*ok {2}git@github\.com:org/a\.git", plain)
    assert re.search(r"\n.* {6}.*→ https://gitlab\.com/example/a", plain)
    assert "■ →" not in plain


def test_multiline_semantic_output_keeps_every_line_inside_the_frame() -> None:
    ui, stdout, stderr = _capture()

    ui.status("Cleaning gone branches: old-feature\nolder-feature")
    ui.detail("remote: first update\r\nremote: second update")
    ui.error_detail("fatal: first failure\nfatal: second failure")

    assert (
        "  ■ Cleaning gone branches: old-feature\n"
        "      older-feature\n"
        "      remote: first update\n"
        "      remote: second update"
    ) in _plain(stdout.getvalue())
    assert _plain(stderr.getvalue()) == (
        "   fatal: first failure\n   fatal: second failure\n"
    )


def test_unknown_tones_fall_back_to_muted_output() -> None:
    ui, stdout, stderr = _capture()

    ui.item("Future item", tone="future")
    ui.detail("Future detail", tone="future")

    assert "undefined" not in stdout.getvalue() + stderr.getvalue()
    assert "\x1b[90m□\x1b[39m Future item" in stdout.getvalue()
    assert "\x1b[90mFuture detail\x1b[39m" in stdout.getvalue()


def test_render_backup_selector_shows_numbers_checkboxes_and_hint() -> None:
    ui, stdout, _ = _capture()
    state = create_selector_state(
        [
            SelectorItem("git@github.com:org/a.git", "git@github.com:org/a.git"),
            SelectorItem("git@gitlab.com:acme/b.git", "git@gitlab.com:acme/b.git"),
        ],
        initial=("git@github.com:org/a.git",),
    )
    state = reduce_selector(state, "down", multiple=True).state

    ui.render_backup_selector(
        "Select repos to backup",
        state,
        list_path=(
            "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/"
            "cloud-utils/gt/backups.json"
        ),
    )

    rendered = stdout.getvalue()
    assert "\x1b[2J\x1b[H" in rendered
    assert "REPO BACKUP" in rendered
    assert "~/Library/Mobile Documents/" in rendered
    assert "Select repos to backup" in rendered
    assert "space toggle, a all, c clear, enter to start, q to quit" in rendered
    assert re.search(r"1\s+.*■.*git@github\.com:org/a\.git", rendered)
    assert re.search(r"2\s+.*□.*git@gitlab\.com:acme/b\.git", rendered)
    assert re.search(r"Last checked: never\n\n\s+2\s+", _plain(rendered))
    assert "└" in rendered


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


def _item(url: str, backup: str | None = None, checked: str | None = None):
    return SimpleNamespace(
        label=url, value=url, last_backup_at=backup, last_checked_at=checked,
    )


def test_render_backup_selector_prints_never_for_null_without_formatting(
    monkeypatch,
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("format called")

    monkeypatch.setattr("git_tools.ui.format_last_backup_label", boom)
    monkeypatch.setattr("git_tools.ui.format_last_checked_label", boom)
    ui, stdout, _ = _capture()
    ui.render_backup_selector(
        "Select repos to backup",
        create_selector_state([_item("git@github.com:org/a.git")]),
        now=NOW,
    )

    rendered = _plain(stdout.getvalue())
    assert "Last backup: never" in rendered
    assert "Last checked: never" in rendered


def test_render_backup_selector_formats_strings_with_supplied_now(monkeypatch) -> None:
    calls = []

    def backup_label(value, now=None):
        calls.append(("backup", value, now))
        return "Last backup: 2 hours ago (2026-08-08 15:00)"

    def checked_label(value, now=None):
        calls.append(("checked", value, now))
        return "Last checked: 1 hour ago (2026-08-08 16:00)"

    monkeypatch.setattr("git_tools.ui.format_last_backup_label", backup_label)
    monkeypatch.setattr("git_tools.ui.format_last_checked_label", checked_label)
    ui, stdout, _ = _capture()
    ui.render_backup_selector(
        "Select repos to backup",
        create_selector_state([
            _item(
                "git@github.com:org/a.git",
                "2026-08-08T08:00:00.000Z",
                "2026-08-08T09:00:00.000Z",
            )
        ]),
        now=NOW,
    )

    rendered = _plain(stdout.getvalue())
    assert calls == [
        ("backup", "2026-08-08T08:00:00.000Z", NOW),
        ("checked", "2026-08-08T09:00:00.000Z", NOW),
    ]
    assert "Last backup: 2 hours ago (2026-08-08 15:00)" in rendered
    assert "Last checked: 1 hour ago (2026-08-08 16:00)" in rendered
    assert "git@github.com:org/a.git" in rendered


def test_render_backup_selector_prints_invalid_timestamp_for_a_broken_string() -> None:
    ui, stdout, _ = _capture()
    ui.render_backup_selector(
        "Select repos to backup",
        create_selector_state([
            _item("git@github.com:org/a.git", "not-iso", "also-bad")
        ]),
        now=NOW,
    )

    rendered = _plain(stdout.getvalue())
    assert "Last backup: Invalid timestamp" in rendered
    assert "Last checked: Invalid timestamp" in rendered


def test_cancelled_backup_selector_shows_timestamp_lines() -> None:
    ui, stdout, _ = _capture()
    ui.cancelled_backup_selector(
        "Select repos to backup",
        create_selector_state([_item("git@github.com:org/a.git")]),
        list_path="~/gt/backups.json",
        now=NOW,
    )

    rendered = _plain(stdout.getvalue())
    assert "Last backup: never" in rendered
    assert "Last checked: never" in rendered
    assert "Selection cancelled" in rendered


def test_cancelled_backup_selector_keeps_list_and_cancelled_footer() -> None:
    ui, stdout, _ = _capture()
    state = create_selector_state(
        [
            SelectorItem("git@github.com:org/a.git", "git@github.com:org/a.git"),
            SelectorItem("git@gitlab.com:acme/b.git", "git@gitlab.com:acme/b.git"),
        ],
        initial=("git@github.com:org/a.git",),
    )

    ui.cancelled_backup_selector(
        "Select repos to backup", state, list_path="~/gt/backups.json"
    )

    rendered = stdout.getvalue()
    assert "\x1b[2J\x1b[H" in rendered
    assert "REPO BACKUP" in rendered
    assert "git@github.com:org/a.git" in rendered
    assert "git@gitlab.com:acme/b.git" in rendered
    assert "Selection cancelled" in rendered
    assert "\x1b[31mSelection cancelled" in rendered


def test_render_backup_selector_without_cancel_has_no_cancelled_footer() -> None:
    ui, stdout, _ = _capture()

    ui.render_backup_selector(
        "Select repos to backup",
        create_selector_state(
            [SelectorItem("git@github.com:org/a.git", "git@github.com:org/a.git")]
        ),
    )

    assert "Selection cancelled" not in stdout.getvalue()
