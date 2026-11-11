"""The update check: version comparison and release selection. The network is faked: these
never reach GitHub."""

import json
import urllib.error

import pytest

from dubcrate import updates
from dubcrate.updates import UpdateInfo, check_for_update, parse_version


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *a):
        return self._data


def answer(payload):
    """A stand-in for urlopen that answers with `payload` and remembers the request."""
    seen = []

    def urlopen(request, timeout=None, context=None):
        seen.append((request, timeout, context))
        return FakeResponse(payload)

    urlopen.seen = seen
    return urlopen


def release(tag, **extra):
    return {"tag_name": tag, "name": f"Dubcrate {tag}", "html_url": f"https://example/{tag}", "body": "", **extra}  # fmt: skip


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("v0.10.0", (0, 10, 0)),
        (" v1.0.0 ", (1, 0, 0)),
    ],
)
def test_clean_versions_are_understood(raw, expected):
    assert parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["0.0.0-dev", "v1.2.3-rc1", "1.2", "development", ""])
def test_anything_else_is_not_a_release_version(raw):
    assert parse_version(raw) is None


def test_the_newest_published_release_is_the_update():
    payload = [
        release("v0.2.0"),
        release("v0.3.0"),
        release("v0.1.5"),
    ]  # (dates, not versions)
    info = check_for_update("0.1.0", urlopen=answer(payload))
    assert isinstance(info, UpdateInfo)
    assert (info.version, info.tag) == ("0.3.0", "v0.3.0")
    assert info.html_url == "https://example/v0.3.0" and info.name == "Dubcrate v0.3.0"


def test_being_up_to_date_or_ahead_is_no_update():
    payload = [release("v0.1.0"), release("v0.0.9")]
    assert check_for_update("0.1.0", urlopen=answer(payload)) is None
    assert check_for_update("0.2.0", urlopen=answer(payload)) is None


def test_drafts_and_prereleases_do_not_count():
    payload = [
        release("v0.9.0", draft=True),
        release("v0.8.0", prerelease=True),
        release("v0.2.0"),
    ]
    assert check_for_update("0.1.0", urlopen=answer(payload)).version == "0.2.0"
    assert check_for_update("0.2.0", urlopen=answer(payload[:2])) is None


def test_tags_that_are_not_versions_are_ignored():
    payload = [release("nightly"), release("v2"), release("v0.2.0")]
    assert check_for_update("0.1.0", urlopen=answer(payload)).version == "0.2.0"


def test_a_build_with_no_release_version_never_asks_github():
    for version in ("development", "0.0.0-dev", "0.2.0-rc1"):
        asked = answer([release("v9.9.9")])
        assert check_for_update(version, urlopen=asked) is None
        assert asked.seen == [], version


def test_one_newer_release_shows_its_own_notes_as_written():
    payload = [release("v0.2.0", body="Fixes the burn window.")]
    assert (
        check_for_update("0.1.0", urlopen=answer(payload)).notes
        == "Fixes the burn window."
    )


def test_several_missed_releases_show_every_note_newest_first():
    payload = [release("v0.2.0", body="Two."), release("v0.3.0", body="Three."), release("v0.1.0", body="Old.")]  # fmt: skip
    notes = check_for_update("0.1.0", urlopen=answer(payload)).notes
    assert notes.index("Three.") < notes.index("Two.") and "Old." not in notes
    assert "# Dubcrate v0.3.0" in notes and "---" in notes


def test_a_release_with_no_description_still_works():
    payload = [release("v0.2.0", body=None, name=None)]
    info = check_for_update("0.1.0", urlopen=answer(payload))
    assert info.notes == "" and info.name == "v0.2.0"


def test_the_request_names_the_app_and_never_waits_long():
    asked = answer([release("v0.2.0")])
    check_for_update("0.1.0", urlopen=asked)
    [(request, timeout, context)] = asked.seen
    assert request.full_url.startswith("https://api.github.com/repos/martincurkovic/Dubcrate/releases")  # fmt: skip
    assert (
        request.get_header("User-agent") == "Dubcrate/0.1.0"
    )  # GitHub refuses a request without one
    assert timeout is not None and timeout <= 10
    assert context is not None  # certifi's CA bundle: a built app has no system one


def test_an_answer_that_is_not_a_list_is_an_error():
    with pytest.raises(ValueError, match="list of releases"):
        check_for_update("0.1.0", urlopen=answer({"message": "Not Found"}))


def test_network_errors_reach_the_caller():
    def refuse(*a, **k):
        raise urllib.error.URLError("no route")

    with pytest.raises(urllib.error.URLError):
        check_for_update("0.1.0", urlopen=refuse)


def test_the_repository_is_the_one_on_github():
    assert updates.REPO == "martincurkovic/Dubcrate"


def test_without_certifi_the_systems_certificates_are_used(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_certifi(name, *a, **k):
        if name == "certifi":
            raise ImportError("gone")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    assert updates._ssl_context() is not None
