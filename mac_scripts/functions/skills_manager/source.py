from __future__ import annotations

import os, re, unicodedata
from collections.abc import Callable
from errno import ELOOP, ENOENT, ENOTDIR, errorcode
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit


class SourceError(ValueError):
    """Raised when a source cannot be represented without credential risk."""


def js_string_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def nfc_codepoint_key(value: str) -> str:
    spelling = js_string_key(value).decode("utf-16-be", errors="surrogatepass")
    return unicodedata.normalize("NFC", spelling)


_GITHUB_COMPONENT = r"[A-Za-z0-9._-]+"
_SHORTHAND = re.compile(
    rf"^({_GITHUB_COMPONENT})/({_GITHUB_COMPONENT})(?:\.git)?$"
)
_SHORTHAND_CANDIDATE = re.compile(r"^[^\s/]+/[^\s/]+$")
_GENERIC_SCP_PREFIX = re.compile(r"^git@[^:/\s]+:", re.IGNORECASE)
_GENERIC_SCP = re.compile(
    rf"^git@([A-Za-z0-9.-]+):({_GITHUB_COMPONENT})/({_GITHUB_COMPONENT}?)(?:\.git)?$",
    re.IGNORECASE,
)
_GITHUB_SSH_PREFIX = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/)", re.IGNORECASE
)
_GITHUB_SSH = re.compile(
    rf"^(?:git@github\.com:|ssh://git@github\.com/)"
    rf"({_GITHUB_COMPONENT})/({_GITHUB_COMPONENT}?)(?:\.git)?$",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:access[_-]?token|token|auth|key|password|secret)\s*=", re.IGNORECASE
)
_URL_CREDENTIALS = re.compile(r"://[^/\s]*@")
_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WEB_URL = re.compile(r"^(https?):[\\/]*(.*)$", re.IGNORECASE)


def _normalize_web_url(value: str) -> str:
    match = _WEB_URL.fullmatch(value)
    return value if match is None else f"{match[1]}://{match[2].replace('\\', '/')}"


def _has_opaque_credential_risk(value: str) -> bool:
    return bool(
        "?" in value or "#" in value or _URL_CREDENTIALS.search(value)
        or _CREDENTIAL_ASSIGNMENT.search(value)
    )


def _has_provider_credential_risk(value: str) -> bool:
    return bool("?" in value or "#" in value or _CREDENTIAL_ASSIGNMENT.search(value))


def _is_github_provider_candidate(value: str) -> bool:
    return bool(_GITHUB_SSH_PREFIX.search(value) or _SHORTHAND_CANDIDATE.search(value))


def _without_url_secrets(parts: SplitResult) -> SplitResult:
    hostname = parts.hostname
    if hostname is None:
        return parts._replace(query="", fragment="")
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as error:
        raise SourceError("Invalid source URL") from error
    netloc = f"{host}:{port}" if port is not None else host
    return parts._replace(netloc=netloc, query="", fragment="")


def redact_source(value: str) -> str:
    source = str(value)
    if _ASCII_CONTROL.search(source):
        return "[unsafe source redacted]"

    provider_base = re.split(r"[?#]", source, maxsplit=1)[0]
    if _GENERIC_SCP_PREFIX.search(source):
        return (
            provider_base
            if _GENERIC_SCP.fullmatch(provider_base)
            else "[unsafe source redacted]"
        )
    if _is_github_provider_candidate(source):
        return (
            provider_base
            if _GITHUB_SSH.fullmatch(provider_base)
            or _SHORTHAND.fullmatch(provider_base)
            else "[unsafe source redacted]"
        )

    source = _normalize_web_url(source)
    try:
        parts = urlsplit(source)
        if not parts.scheme:
            raise ValueError("not an absolute URL")
        if parts.hostname is None and _has_opaque_credential_risk(source):
            return "[unsafe source redacted]"
        return urlunsplit(_without_url_secrets(parts)).removesuffix("/")
    except (SourceError, ValueError):
        without_userinfo = re.sub(r"//[^/@]+@", "//", source)
        redacted = re.split(r"[?#]", without_userinfo, maxsplit=1)[0]
        return (
            "[unsafe source redacted]"
            if _has_opaque_credential_risk(redacted)
            else redacted
        )


def _realpath_error_site(value: str, expected: OSError) -> tuple[str, str]:
    path = os.path.sep
    for part in Path(value).parts[1:]:
        candidate = os.path.join(path, part)
        operation = "stat" if os.path.islink(candidate) else "lstat"
        try:
            if operation == "stat":
                os.stat(candidate)
            path = os.path.realpath(candidate, strict=True)
        except OSError as error:
            if error.errno == expected.errno:
                return operation, candidate
            raise expected
    raise expected


def _strict_realpath(value: str) -> str:
    try:
        return os.path.realpath(value, strict=True)
    except OSError as error:
        if error.errno not in (ENOENT, ENOTDIR, ELOOP):
            raise
        description = {ELOOP: "too many symbolic links encountered"}.get(
            error.errno, os.strerror(error.errno).lower()
        )
        operation, path = _realpath_error_site(value, error)
        code = errorcode[error.errno]
        raise SourceError(f"{code}: {description}, {operation} '{path}'") from error


def canonicalize_source(
    value: str,
    *,
    cwd: Path | None = None,
    realpath: Callable[[str], str] = _strict_realpath,
) -> str:
    raw_source = str(value)
    if _ASCII_CONTROL.search(raw_source):
        raise SourceError("Unsafe source control characters")
    source = raw_source.strip()
    if not source:
        raise SourceError("Source must not be empty")

    if source.startswith(("./", "../")) or Path(source).is_absolute():
        base = Path.cwd() if cwd is None else cwd
        return realpath(os.path.abspath(base / source))

    provider_candidate = _is_github_provider_candidate(source)
    if provider_candidate and _has_provider_credential_risk(source):
        raise SourceError("Unsafe source credentials")

    if _GITHUB_SSH_PREFIX.search(source):
        match = _GITHUB_SSH.fullmatch(source)
        if match is None:
            raise SourceError("Invalid GitHub SSH source")
        return f"{match[1]}/{match[2].removesuffix('.git')}"

    if _GENERIC_SCP_PREFIX.search(source):
        if _has_provider_credential_risk(source):
            raise SourceError("Unsafe source credentials")
        match = _GENERIC_SCP.fullmatch(source)
        if match is None:
            raise SourceError("Invalid generic SCP source")
        return (
            f"git@{match[1].lower()}:"
            f"{match[2]}/{match[3].removesuffix('.git')}"
        )

    if _SHORTHAND_CANDIDATE.fullmatch(source):
        if _SHORTHAND.fullmatch(source) is None:
            raise SourceError("Invalid GitHub shorthand source")
        return source.removesuffix(".git")

    source = _normalize_web_url(source)
    try:
        parts = urlsplit(source)
        if not parts.scheme:
            raise ValueError("not an absolute URL")
        if parts.hostname is None and _has_opaque_credential_risk(source):
            raise SourceError("Unsafe source credentials")
        safe_parts = _without_url_secrets(parts)
        if safe_parts.hostname == "github.com":
            path = tuple(filter(None, safe_parts.path.lstrip("/").split("/")))
            if len(path) == 2:
                return f"{path[0]}/{path[1].removesuffix('.git')}"
        path = re.sub(r"\.git/?$", "", safe_parts.path).removesuffix("/")
        return urlunsplit(safe_parts._replace(path=path)).removesuffix("/")
    except SourceError:
        raise
    except ValueError as error:
        if _has_opaque_credential_risk(source):
            raise SourceError("Unsafe source credentials") from error
        return source
