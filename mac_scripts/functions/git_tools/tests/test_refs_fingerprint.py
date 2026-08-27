from git_tools.refs_fingerprint import fingerprints_equal, parse_ls_remote_fingerprint


LS_REMOTE_SAMPLE = "\n".join(
    [
        "aaa\tHEAD",
        "bbb\trefs/heads/main",
        "ccc\trefs/tags/v1",
        "ddd\trefs/pull/1/head",
        "eee\trefs/heads/develop",
    ]
)


def test_fingerprint_keeps_heads_and_tags_sorted() -> None:
    assert parse_ls_remote_fingerprint(LS_REMOTE_SAMPLE) == (
        "bbb refs/heads/main",
        "ccc refs/tags/v1",
        "eee refs/heads/develop",
    )


def test_fingerprint_ignores_blank_malformed_and_other_refs() -> None:
    raw = "  \nnot enough\nsha refs/notes/x\nsha\trefs/tags/v2\textra\n"
    assert parse_ls_remote_fingerprint(raw) == ("sha refs/tags/v2",)


def test_fingerprints_equal_detects_match_and_mismatch() -> None:
    a = parse_ls_remote_fingerprint("x\trefs/heads/main\n")
    b = parse_ls_remote_fingerprint("x\trefs/heads/main\n")
    c = parse_ls_remote_fingerprint("y\trefs/heads/main\n")
    assert fingerprints_equal(a, b)
    assert not fingerprints_equal(a, c)
