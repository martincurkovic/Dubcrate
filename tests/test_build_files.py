"""The packaging files agree with each other and with the app: a build that silently lacks a
data file only shows up once someone opens it, so check the names and paths here."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = (ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
BUILD_WORKFLOW = (ROOT / ".github" / "workflows" / "build.yml").read_text(
    encoding="utf-8"
)
TEST_WORKFLOW = (ROOT / ".github" / "workflows" / "test.yml").read_text(
    encoding="utf-8"
)


def numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def test_the_dmg_layout_and_its_background_picture_agree():
    settings = (ROOT / "scripts" / "dmg_settings.py").read_text(encoding="utf-8")
    picture = (ROOT / "assets" / "dmg" / "create_dmg_background.py").read_text(encoding="utf-8")  # fmt: skip
    window = re.search(r"window_rect = \(\(\d+, \d+\), \((\d+), (\d+)\)\)", settings)
    size = re.search(r"WIDTH, HEIGHT = (\d+), (\d+)", picture)
    assert window and size and window.groups() == size.groups()
    app = re.search(r"app_name: \((\d+), (\d+)\)", settings)
    applications = re.search(r'"Applications": \((\d+), (\d+)\)', settings)
    marks = re.search(r"APP_ICON_X, APPLICATIONS_ICON_X, ICON_Y = (\d+), (\d+), (\d+)", picture)  # fmt: skip
    assert app and applications and marks
    assert (app.group(1), applications.group(1), app.group(2)) == marks.groups()
    assert applications.group(2) == app.group(2)  # side by side
    assert (ROOT / "assets" / "dmg" / "dmg_background.tiff").stat().st_size > 1000


def test_every_data_file_the_build_bundles_exists():
    sources = re.findall(r"--include-data-(?:dir|files)=([^=\s]+)=", BUILD_SCRIPT)
    assert sources, "the build script names no data to bundle"
    for source in sources:  # relative to src/, where the script runs
        if source.startswith(
            "../build/"
        ):  # made by the script itself (ffmpeg), so not in a clean checkout
            continue
        assert (ROOT / "src" / source).resolve().exists(), source


def test_the_build_bundles_everything_the_app_reads_at_run_time():
    for needed in (
        "dubcrate/ui/icons",  # the stylesheet's arrows and checkmarks
        "dubcrate/ui/fonts",  # the System 6 look
        "dubcrate/ui/help",  # the user guide
        "dubcrate/ui/style.qss.template",
        "dubcrate/ui/style_system6.qss.template",
        "AppIcon.png",  # the splash screen and the window icon
    ):
        assert needed in BUILD_SCRIPT, needed
        if not needed.endswith(".png"):
            assert (ROOT / "src" / needed).exists(), needed


def test_ffmpeg_is_built_and_bundled_where_the_app_looks_for_it():
    from dubcrate import tools

    ffmpeg_script = (ROOT / "scripts" / "build_ffmpeg_macos.sh").read_text(encoding="utf-8")  # fmt: skip
    assert "build_ffmpeg_macos.sh" in BUILD_SCRIPT
    bundled = re.search(r"--include-data-dir=\.\./build/ffmpeg/bundle=(\S+?)\"", BUILD_SCRIPT)  # fmt: skip
    assert bundled and bundled.group(1) == f"dubcrate/{tools.BUNDLE_DIR.name}"
    for needed in ("--enable-libmp3lame", "--enable-encoder=pcm_s16le,libmp3lame", "--enable-muxer=pcm_s16le"):  # fmt: skip
        assert needed in ffmpeg_script, needed
    for banned in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
        assert not re.search(rf"^\s*{banned}\b", ffmpeg_script, re.MULTILINE), banned
    floor = re.search(r'^MIN_MACOS="(\d+\.\d+)"', BUILD_SCRIPT, re.MULTILINE).group(1)
    assert (
        f'MIN_MACOS="{floor}"' in ffmpeg_script
    )  # the tools target the same macOS as the app
    for workflow in (BUILD_WORKFLOW,):
        assert "build_ffmpeg_macos.sh" in workflow


def test_the_macos_floor_is_written_once_and_used_everywhere():
    floor = re.search(r'^MIN_MACOS="(\d+\.\d+)"', BUILD_SCRIPT, re.MULTILINE)
    assert floor
    # the compiler's deployment target and the Info.plist both come from that one number
    assert 'export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"' in BUILD_SCRIPT
    assert 'plutil -replace LSMinimumSystemVersion -string "$MIN_MACOS"' in BUILD_SCRIPT
    major = floor.group(1).split(".")[0]
    assert f"macOS {major}" in (ROOT / "BUILDING.md").read_text(encoding="utf-8")
    assert f"macOS {major}" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_dmg_names_the_script_makes_are_the_ones_the_workflow_uploads():
    app_name = re.search(r'^APP_NAME="([^"]+)"', BUILD_SCRIPT, re.MULTILINE).group(1)
    assert re.search(
        rf"^\s+APP_NAME: {re.escape(app_name)}$", BUILD_WORKFLOW, re.MULTILINE
    )
    assert 'DMG_NAME="$APP_NAME-macos-$DMG_ARCH.dmg"' in BUILD_SCRIPT
    for arch in ("arm", "intel"):
        assert f"${{{{ env.APP_NAME }}}}-macos-{arch}.dmg" in BUILD_WORKFLOW, arch


def test_the_build_is_gated_on_the_tests_and_only_releases_from_a_tag():
    assert "uses: ./.github/workflows/test.yml" in BUILD_WORKFLOW
    assert "workflow_call:" in TEST_WORKFLOW
    assert "if: github.ref_type == 'tag'" in BUILD_WORKFLOW
    assert (
        "--smoke-test=" in BUILD_WORKFLOW
    )  # each build is opened once before it ships
    assert (
        "draft: true" in BUILD_WORKFLOW
    )  # and a release is looked at before it is public


def test_the_tests_run_on_every_push():
    on = TEST_WORKFLOW.split("jobs:")[0]
    assert re.search(r"^  push:\s*(#.*)?$", on, re.MULTILINE)
    assert "branches" not in on.split("pull_request")[0].split("push:")[1]  # any branch
    assert "uv run pytest" in TEST_WORKFLOW and "ruff" in TEST_WORKFLOW
    assert "--no-group build" in TEST_WORKFLOW  # the tests don't need Nuitka


def test_the_packaging_tools_are_a_dependency_group_not_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build = " ".join(project["dependency-groups"]["build"]).lower()
    for tool in ("nuitka", "dmgbuild", "pyside6-addons"):
        assert tool in build, tool
    assert "nuitka" not in " ".join(project["project"]["dependencies"]).lower()
    assert (
        "build" in project["tool"]["uv"]["default-groups"]
    )  # the build scripts' `uv sync`
