import binascii
import hashlib
import time

import pyotp


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_VALID_PADDING = {(0, 0), (2, 6), (4, 4), (5, 3), (7, 1)}


def normalize_base32(value: str) -> str:
    return "".join(value.split()).upper()


def _validate_base32(secret: str) -> None:
    if any(char not in _ALPHABET + "=" for char in secret):
        raise ValueError("invalid Base32 character")

    data, separator, padding = secret.partition("=")
    if separator:
        padding = separator + padding
        if padding.strip("="):
            raise ValueError("padding must appear only at the end")
        if len(secret) % 8 != 0:
            raise ValueError("padded Base32 length must be a multiple of 8")
        if (len(data) % 8, len(padding)) not in _VALID_PADDING:
            raise ValueError("invalid Base32 padding")
    elif len(data) % 8 not in {0, 2, 4, 5, 7}:
        raise ValueError("invalid unpadded Base32 length")

    if not data:
        raise ValueError("empty Base32 data")

    buffer = 0
    bits = 0
    decoded_bytes = 0
    for char in data:
        buffer = (buffer << 5) | _ALPHABET.index(char)
        bits += 5
        while bits >= 8:
            bits -= 8
            buffer &= (1 << bits) - 1
            decoded_bytes += 1

    if buffer:
        raise ValueError("invalid Base32 encoding")
    if not decoded_bytes:
        raise ValueError("decoded secret is empty")


def generate_totp(secret: str, *, now: float | int | None = None) -> str:
    normalized = normalize_base32(secret)
    if not normalized:
        raise ValueError("empty secret")

    _validate_base32(normalized)

    try:
        return pyotp.TOTP(normalized, digits=6, digest=hashlib.sha1).at(
            time.time() if now is None else now
        )
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError("invalid Base32 encoding") from error
