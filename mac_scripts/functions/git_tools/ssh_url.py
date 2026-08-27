from dataclasses import dataclass
import re

_SSH_RE = re.compile(r"^git@([^:]+):(.+)/([^/]+?)(?:\.git)?$", re.IGNORECASE)

@dataclass(frozen=True, slots=True)
class ParsedSshUrl:
    host: str
    owner: str
    repo: str
    ssh_url: str
    canonical: str

    @property
    def project_name(self) -> str:
        return build_project_name(self.owner, self.repo)

def slugify_segment(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", value.lower())).strip("-")

def build_project_name(owner: str, repo: str) -> str:
    return f"{slugify_segment(owner)}-{slugify_segment(repo)}"

def parse_ssh_git_url(value: str) -> ParsedSshUrl:
    """Parse a Git SSH URL, preserving owner/repo casing and path nesting.

    The JavaScript implementation reports these two validation failures with
    stable messages; ValueError carries those messages at the Python boundary.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid SSH URL")

    trimmed = value.strip()
    if re.match(r"^https?://", trimmed, re.IGNORECASE):
        raise ValueError("HTTPS URLs are not supported")

    match = _SSH_RE.match(trimmed)
    if match is None:
        raise ValueError("Invalid SSH URL")

    host, owner, repo = match.groups()
    canonical = f"git@{host.lower()}:{owner}/{repo}"
    return ParsedSshUrl(
        host=host,
        owner=owner,
        repo=repo,
        ssh_url=f"{canonical}.git",
        canonical=canonical,
    )

def canonicalize_ssh_git_url(value: str) -> ParsedSshUrl:
    return parse_ssh_git_url(value)
