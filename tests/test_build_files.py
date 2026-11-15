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


MACOS_WORKFLOW = (ROOT / ".github" / "workflows" / "test-macos.yml").read_text(
    encoding="utf-8"
)
WINDOWS_WORKFLOW = (ROOT / ".github" / "workflows" / "test-windows.yml").read_text(
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
    assert "uses: ./.github/workflows/test-macos.yml" in BUILD_WORKFLOW
    assert "workflow_call:" in TEST_WORKFLOW and "workflow_call:" in MACOS_WORKFLOW
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
    assert "pytest tests/" in TEST_WORKFLOW and "ruff" in TEST_WORKFLOW
    assert "--no-group build" in TEST_WORKFLOW  # the tests don't need Nuitka
    # ... and `uv run` would put it back, so every uv call after the sync says so too
    assert TEST_WORKFLOW.count("uv run --no-group build") == 3 and "uv run pytest" not in TEST_WORKFLOW  # fmt: skip


def test_the_push_run_is_linux_only_and_the_other_systems_are_run_by_hand():
    # (the Actions quota: macOS minutes cost ten times Linux's)
    assert "runs-on: ubuntu-latest" in TEST_WORKFLOW
    assert re.findall(r"runs-on: (\S+)", TEST_WORKFLOW) == ["ubuntu-latest"]
    for text, runner in ((MACOS_WORKFLOW, "macos-15"), (WINDOWS_WORKFLOW, "windows-latest")):  # fmt: skip
        on = text.split("jobs:")[0]
        assert "workflow_dispatch:" in on
        assert "push:" not in on and "pull_request" not in on and "schedule:" not in on
        assert f"runs-on: {runner}" in text
        assert "pytest tests/" in text and text.count("uv run --no-group build") == 1


def test_the_packaging_tools_are_a_dependency_group_not_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build = " ".join(project["dependency-groups"]["build"]).lower()
    for tool in ("nuitka", "dmgbuild", "pyside6-addons"):
        assert tool in build, tool
    assert "nuitka" not in " ".join(project["project"]["dependencies"]).lower()
    assert (
        "build" in project["tool"]["uv"]["default-groups"]
    )  # the build scripts' `uv sync`


# -- the Linux build (scripts/build_linux.sh, build_ffmpeg_linux.sh, assets/linux, the AppImage) -----------

LINUX_SCRIPT = (ROOT / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
FFMPEG_MACOS = (ROOT / "scripts" / "build_ffmpeg_macos.sh").read_text(encoding="utf-8")
FFMPEG_LINUX = (ROOT / "scripts" / "build_ffmpeg_linux.sh").read_text(encoding="utf-8")


def assigned(script: str, name: str) -> str:
    found = re.search(rf'^{name}="([^"]*)"', script, re.MULTILINE)
    assert found, name
    return found.group(1)


def data_args(script: str) -> list[str]:
    return re.findall(r"--include-data-(?:dir|files)=[^\s\"]+", script)


def test_the_linux_and_macos_builds_bundle_the_same_data():
    assert data_args(LINUX_SCRIPT) and data_args(LINUX_SCRIPT) == data_args(BUILD_SCRIPT)  # fmt: skip
    sources = re.findall(r"--include-data-(?:dir|files)=([^=\s]+)=", LINUX_SCRIPT)
    for source in sources:
        if not source.startswith("../build/"):  # (ffmpeg: made by the script)
            assert (ROOT / "src" / source).resolve().exists(), source


def test_the_linux_build_has_no_macos_parts_and_names_what_it_needs():
    code = "\n".join(l for l in LINUX_SCRIPT.splitlines() if not l.lstrip().startswith("#"))  # fmt: skip
    for macos in ("MACOSX", "codesign", "plutil", "dmgbuild", "sed -i ''", "sysctl", "objc", "DiscRecording"):  # fmt: skip
        assert macos not in code, macos
    for module in ("pyrekordbox", "sqlcipher3", "cffi", "_cffi_backend"):
        assert module in LINUX_SCRIPT, module  # (found by name, so named)
    assert assigned(LINUX_SCRIPT, "APP_NAME") == assigned(BUILD_SCRIPT, "APP_NAME")
    assert "build_ffmpeg_linux.sh" in LINUX_SCRIPT and "dubcrate/bin" in LINUX_SCRIPT
    assert "patchelf" in LINUX_SCRIPT and "build-essential" in LINUX_SCRIPT


def test_both_ffmpeg_builds_are_the_same_lgpl_build():
    for name in ("FFMPEG_VERSION", "FFMPEG_SHA256", "LAME_VERSION", "LAME_SHA256"):
        assert assigned(FFMPEG_LINUX, name) == assigned(FFMPEG_MACOS, name), name
    for needed in ("--enable-libmp3lame", "--enable-encoder=pcm_s16le,libmp3lame", "--enable-muxer=pcm_s16le", "--disable-autodetect"):  # fmt: skip
        assert needed in FFMPEG_LINUX, needed
    for banned in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
        assert not re.search(rf"^\s*{banned}\b", FFMPEG_LINUX, re.MULTILINE), banned
    assert (
        "--enable-gpl" in FFMPEG_LINUX
    )  # (the check that refuses a GPL build names it)
    configure = lambda text: re.findall(
        r"^\s+(--(?:enable|disable)-[^\s\\]+)", text, re.MULTILINE
    )
    shared = [c for c in configure(FFMPEG_MACOS) if c != "--enable-zlib"]
    assert set(shared) <= set(configure(FFMPEG_LINUX)) | {"--enable-gpl"}


def test_the_appimage_is_assembled_from_files_that_exist_and_agree():
    desktop = (ROOT / "assets" / "linux" / "Dubcrate.desktop").read_text(encoding="utf-8")  # fmt: skip
    fields = dict(line.split("=", 1) for line in desktop.splitlines() if "=" in line)
    assert fields["Name"] == "Dubcrate" and fields["Exec"] == "main.bin"
    assert fields["Icon"] == assigned(LINUX_SCRIPT, "APP_NAME")  # AppDir/Dubcrate.png
    assert "AudioVideo" in fields["Categories"].split(";") and fields["Terminal"] == "false"  # fmt: skip
    assert 'cp "$ROOT_DIR/assets/linux/$APP_NAME.desktop"' in LINUX_SCRIPT
    assert 'cp "$ROOT_DIR/assets/icon/Dubcrate-512.png" "$APPDIR/$APP_NAME.png"' in LINUX_SCRIPT  # fmt: skip
    assert 'exec "$HERE/usr/bin/main.bin"' in LINUX_SCRIPT
    icon = (ROOT / "assets" / "icon" / "Dubcrate-512.png").read_bytes()
    assert icon[:8] == b"\x89PNG\r\n\x1a\n" and icon[16:24] == (512).to_bytes(4, "big") * 2  # fmt: skip


def test_the_appimage_tool_is_pinned_with_a_hash_for_each_architecture():
    assert re.fullmatch(r"\d+\.\d+\.\d+", assigned(LINUX_SCRIPT, "APPIMAGETOOL_VERSION"))  # fmt: skip
    for arch in ("X86_64", "AARCH64"):
        assert re.fullmatch(r"[0-9a-f]{64}", assigned(LINUX_SCRIPT, f"APPIMAGETOOL_SHA256_{arch}"))  # fmt: skip
    assert (
        "sha256sum -c" in LINUX_SCRIPT and "--appimage-extract-and-run" in LINUX_SCRIPT
    )


def test_the_linux_names_the_script_makes_are_the_ones_the_workflow_uploads_and_smoke_tests():
    assert 'IMAGE="$APP_NAME-linux-$ARCH.AppImage"' in LINUX_SCRIPT
    assert 'zip -q "$APP_NAME-linux-$ARCH.zip" "$IMAGE"' in LINUX_SCRIPT
    job = BUILD_WORKFLOW.split("  build-linux:")[1].split("\n  release:")[0]
    assert "runs-on: ubuntu-24.04" in job and "build_linux.sh" in job
    assert "${{ env.APP_NAME }}-linux-x86_64.zip" in job
    assert (
        "$APP_NAME-linux-x86_64.AppImage" in job
        and "src/$APP_NAME.dist/main.bin" in job
    )
    assert job.count("--smoke-test=") == 2  # the app, and the AppImage that carries it
    assert "build_ffmpeg_linux.sh" in job and "needs: [test, test-macos]" in job
    assert "build-linux" in BUILD_WORKFLOW.split("  release:")[1].split("steps:")[0]


def test_the_docker_build_wraps_the_same_script_and_checks_the_result():
    wrapper = (ROOT / "scripts" / "build_linux_docker.sh").read_text(encoding="utf-8")  # fmt: skip
    dockerfile = (ROOT / "scripts" / "linux-build.Dockerfile").read_text(encoding="utf-8")  # fmt: skip
    assert "scripts/build_linux.sh" in wrapper and wrapper.count("--smoke-test=") == 2
    for needed in ("build-essential", "patchelf", "nasm", "zip", "file", "zlib1g-dev"):
        assert needed in dockerfile, needed
        assert needed in LINUX_SCRIPT, needed
