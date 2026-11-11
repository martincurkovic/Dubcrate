"""Checks GitHub Releases for a newer Dubcrate.

Adapted from JV Studio's update checker. It uses the unauthenticated GitHub REST API (the most
recent releases). Only published (non-draft, non-prerelease) releases count, exactly as
`.github/workflows/build.yml` publishes them. The newest is the update; the notes shown are those
of every release newer than the installed version, newest first, so someone several versions
behind reads everything they missed. Nothing is downloaded: the dialog sends you to the release page.

No Qt here: the window runs this on a worker (`ui/update_check.py`).
"""

import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from dubcrate.ui.version import app_version

log = logging.getLogger(__name__)

REPO = "martincurkovic/Dubcrate"
RELEASES_PER_PAGE = 30  # more than anyone is behind by
RELEASES_API_URL = (
    f"https://api.github.com/repos/{REPO}/releases?per_page={RELEASES_PER_PAGE}"
)
REQUEST_TIMEOUT = 5  # seconds: this must never hold anything up

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class UpdateInfo:
    version: str  # "1.1.0", no leading "v"
    tag: str  # "v1.1.0"
    name: str  # the release's title
    html_url: str  # the GitHub release page, where the downloads are
    notes: str = ""  # Markdown: those of every release newer than the installed one, newest first


class UpdateCheckError(RuntimeError):
    """The check couldn't be done or understood. The message is written for the user."""


def _http_failure(error: urllib.error.HTTPError) -> str:
    if error.code == 404:
        # GitHub answers 404 for a private repository too, so this is also "not published yet"
        return "GitHub has no public releases of Dubcrate to find (the repository may not be public yet)."
    if error.code in (403, 429):
        return "GitHub is limiting requests from this connection right now. Try again later."
    return f"GitHub answered with an error ({error.code} {error.reason})."


def _ssl_context() -> ssl.SSLContext:
    # A bundled Python (the Nuitka macOS build) has no system CA store, so the default context
    # fails with CERTIFICATE_VERIFY_FAILED. certifi carries its own bundle.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def parse_version(text: str) -> tuple[int, int, int] | None:
    """(major, minor, patch), or None if this is not a clean release number (a development
    build, anything with a suffix)."""
    match = _VERSION_RE.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _combined_notes(newer: list[tuple[str, str]]) -> str:
    """The notes of the releases newer than the installed one, [(name, body)] newest first. One
    release's notes are its own, as written; several get a heading each and a rule between them."""
    if len(newer) == 1:
        return newer[0][1]
    return "\n\n---\n\n".join(
        f"# {name}\n\n{body.strip()}".rstrip() for name, body in newer
    )


def check_for_update(
    current_version: str | None = None, *, urlopen: Callable = urllib.request.urlopen
) -> UpdateInfo | None:
    """An `UpdateInfo` if a newer release exists, else None.

    Raises `UpdateCheckError` (with a message for the user) if the network or the answer fails;
    the caller decides whether that is worth telling anyone.
    """
    current_version = app_version() if current_version is None else current_version
    current = parse_version(current_version)
    if current is None:
        return None  # a development build: nothing to compare against

    request = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Dubcrate/{current_version}",  # GitHub's API refuses a request with none
        },
    )
    try:
        with urlopen(
            request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:  # before URLError, which it subclasses
        raise UpdateCheckError(_http_failure(e)) from e
    except TimeoutError as e:
        raise UpdateCheckError("GitHub took too long to answer.") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise UpdateCheckError(f"Could not reach GitHub ({reason}).") from e
    except ValueError as e:  # json.JSONDecodeError, or a body that isn't text
        raise UpdateCheckError("GitHub's answer could not be read.") from e
    if not isinstance(payload, list):
        raise UpdateCheckError("GitHub's answer wasn't a list of releases.")

    newer = []
    for release in payload:
        if (
            not isinstance(release, dict)
            or release.get("draft")
            or release.get("prerelease")
        ):
            continue
        version = parse_version(release.get("tag_name") or "")
        if version is not None and version > current:
            newer.append((version, release))
    if not newer:
        return None
    newer.sort(
        key=lambda pair: pair[0], reverse=True
    )  # GitHub lists by date, not by version
    latest, release = newer[0]
    tag = release["tag_name"]
    return UpdateInfo(
        version=".".join(str(part) for part in latest),
        tag=tag,
        name=release.get("name") or tag,
        html_url=release.get("html_url") or f"https://github.com/{REPO}/releases",
        notes=_combined_notes(
            [(r.get("name") or r["tag_name"], r.get("body") or "") for _v, r in newer]
        ),
    )
