import pytest

from twofa.totp import generate_totp, normalize_base32


# RFC 6238 Appendix B secret (ASCII) as Base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
LONG_SECRET = "AEAQCAIB" * 13


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (59, "287082"),
        (1_111_111_109, "081804"),
        (1_111_111_111, "050471"),
        (1_234_567_890, "005924"),
        (2_000_000_000, "279037"),
        (20_000_000_000, "353130"),
    ],
)
def test_generate_totp_matches_six_digit_rfc_vectors(now: int, expected: str) -> None:
    assert generate_totp(RFC_SECRET, now=now) == expected


def test_normalize_base32_strips_whitespace_and_uppercases() -> None:
    assert normalize_base32(" gezd gnbv\n") == "GEZDGNBV"


@pytest.mark.parametrize(
    ("secret", "message"),
    [
        ("", "Base32 secret is required"),
        ("   ", "Base32 secret is required"),
        ("!!!!", "Invalid Base32 secret"),
    ],
)
def test_generate_totp_rejects_invalid_base32(secret: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        generate_totp(secret, now=59)


def test_generate_totp_rejects_nonzero_discarded_base32_bits() -> None:
    with pytest.raises(ValueError, match="^Invalid Base32 secret$"):
        generate_totp("GEZD", now=59)


def test_generate_totp_accepts_canonical_padded_base32() -> None:
    assert generate_totp("GEZDGNA=", now=59) == "336582"


def test_generate_totp_preserves_leading_zeroes() -> None:
    assert generate_totp(RFC_SECRET, now=1_111_111_109) == "081804"


def test_generate_totp_accepts_long_base32_secret() -> None:
    assert generate_totp(LONG_SECRET, now=59) == "135355"
