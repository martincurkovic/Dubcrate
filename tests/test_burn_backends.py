"""Which burn backend a computer uses, and what it says when it can't burn at all."""

import pytest

from dubcrate import burn, tools
from dubcrate.burn import cdrdao


@pytest.mark.parametrize(
    ("platform", "backend"),
    [("darwin", "discrecording"), ("linux", "cdrdao"), ("win32", "cdrdao")],
)
def test_the_default_backend_is_decided_by_the_platform_in_one_place(
    monkeypatch, platform, backend
):
    monkeypatch.setattr(burn.sys, "platform", platform)
    assert burn.default_backend() == backend
    assert backend in burn.BACKENDS


def test_discrecording_is_only_offered_on_a_mac(monkeypatch):
    monkeypatch.setattr(burn.sys, "platform", "linux")
    assert "only works on a Mac" in burn.backend_problem("discrecording")
    monkeypatch.setattr(burn.sys, "platform", "darwin")
    assert burn.backend_problem("discrecording") is None


@pytest.fixture
def no_cdrdao(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "BUNDLE_DIR", tmp_path / "no-bundle")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)


def test_a_missing_cdrdao_is_explained_with_how_to_get_it(no_cdrdao, monkeypatch):
    monkeypatch.setattr(cdrdao.sys, "platform", "linux")
    problem = burn.backend_problem("cdrdao")
    assert "was not found" in problem and "sudo apt install cdrdao" in problem
    assert "sudo dnf install cdrdao" in problem
    monkeypatch.setattr(cdrdao.sys, "platform", "darwin")
    assert "brew install cdrdao" in burn.backend_problem("cdrdao")
    monkeypatch.setattr(cdrdao.sys, "platform", "win32")
    assert "isn't supported" in burn.backend_problem("cdrdao")


def test_cdrdao_is_found_in_the_bundle_or_on_path(no_cdrdao, tmp_path, monkeypatch):
    assert burn.backend_problem("cdrdao") is not None and cdrdao.find_cdrdao() is None
    on_path = tmp_path / "usr" / "cdrdao"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: str(on_path))
    assert cdrdao.find_cdrdao() == str(on_path)
    assert burn.backend_problem("cdrdao") is None


def test_an_unknown_backend_is_a_problem_not_a_crash():
    assert "Unknown burn backend" in burn.backend_problem("nero")


def test_burning_without_cdrdao_says_how_to_get_it(no_cdrdao, tmp_path):
    from dubcrate.burn import BurnError, BurnOptions, Disc

    with pytest.raises(BurnError, match="cdrdao was not found"):
        cdrdao.CdrdaoBurner().burn(Disc(title="t", performer="p"), BurnOptions(), print)
