# build_windows.ps1 - builds the Dubcrate Windows app and its installer
# Can be run from anywhere. Handles uv sync, the bundled ffmpeg, the packaging (pyside6-deploy / Nuitka) and the installer
# (Inno Setup), the same way as JV Studio and AKAISDS. Output, in the repository root: Dubcrate-windows-x64-Setup.exe.
# If PowerShell blocks running scripts, run once as your own user:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Needs, once (see BUILDING.md):
#   - uv (https://docs.astral.sh/uv/) and the Visual Studio Build Tools' C++ workload (Nuitka compiles C; it can also download MinGW itself)
#   - MSYS2 (https://www.msys2.org), to build ffmpeg: winget install MSYS2.MSYS2, then in an MSYS2 MINGW64 shell:
#       pacman -S --needed base-devel diffutils make nasm xz curl mingw-w64-x86_64-gcc mingw-w64-x86_64-zlib mingw-w64-x86_64-pkgconf
#     and, for cdrdao (built in the MSYS shell, which is a different environment), in an MSYS2 MSYS shell:
#       pacman -S --needed base-devel gcc make autoconf automake libtool pkgconf diffutils libiconv-devel gettext-devel curl
#     (set MSYS2_ROOT if it is not in C:\msys64)
#   - Inno Setup 6 (winget install JRSoftware.InnoSetup), to make the installer; without it the app is still built
#
# The app bundles LGPL builds of ffmpeg and ffprobe (scripts/build_ffmpeg_windows.sh makes them, the first time), which
# dubcrate/tools.py looks for before PATH. So does cdrdao, the burner (scripts/build_cdrdao_windows.sh: GPL, so its source and
# licence go in the bundle too).
# NOT YET RUN: written from JV Studio's script and scripts/build_linux.sh; the first run is the first CI run.

$ErrorActionPreference = "Stop"

$AppName    = "Dubcrate"
$RootDir    = Split-Path -Parent $PSScriptRoot
$SrcDir     = Join-Path $RootDir "src"
$VersionFile = Join-Path $SrcDir "dubcrate\_build_version.py"

Set-Location $RootDir
Write-Host "Syncing environment with uv..."
uv sync
if ($LASTEXITCODE -ne 0) { Write-Host "uv sync failed - fix the error before continuing"; exit 1 }
uv pip install pip

# the app and installer icon, from the 1024 px master (not tracked)
uv run python scripts/make_ico.py
if ($LASTEXITCODE -ne 0) { Write-Host "making the icon failed"; exit 1 }

# ffmpeg and ffprobe (a no-op if already built: delete build\ffmpeg to build them again), copied to a fixed folder name
# so the data argument below does not depend on the architecture
$Msys = if ($env:MSYS2_ROOT) { $env:MSYS2_ROOT } else { "C:\msys64" }
$FfmpegOut = Join-Path $RootDir "build\ffmpeg\x86_64"
$Bash = Join-Path $Msys "usr\bin\bash.exe"
if (-not ((Test-Path (Join-Path $FfmpegOut "ffmpeg.exe")) -and (Test-Path (Join-Path $FfmpegOut "ffprobe.exe")))) {
    if (-not (Test-Path $Bash)) {
        Write-Host "MSYS2 was not found at $Msys (needed to build ffmpeg). Install it (winget install MSYS2.MSYS2) or set MSYS2_ROOT."
        exit 1
    }
    $env:MSYSTEM = "MINGW64"
    $env:CHERE_INVOKING = "1"   # stay in the current folder
    $Script = ($RootDir -replace '\\', '/') + "/scripts/build_ffmpeg_windows.sh"
    Write-Host "Building ffmpeg and ffprobe (a few minutes, the first time)..."
    & $Bash -lc "'$Script'"
    if ($LASTEXITCODE -ne 0) { Write-Host "building ffmpeg failed - fix the error above before continuing."; exit 1 }
}
$Bundle = Join-Path $RootDir "build\ffmpeg\bundle"
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
Copy-Item -Recurse $FfmpegOut $Bundle

# cdrdao, the burner (GPL: its licence, offer of source and source go with it), built in MSYS2's MSYS shell, not the MINGW64 one:
# cdrdao is a Unix program that Windows runs through the POSIX layer (see the script). cdrdao.exe and its two DLLs go in the same
# bundle as ffmpeg, so dubcrate/bin has them all.
$CdrdaoOut = Join-Path $RootDir "build\cdrdao\x86_64"
if (-not (Test-Path (Join-Path $CdrdaoOut "cdrdao.exe"))) {
    $env:MSYSTEM = "MSYS"
    $env:CHERE_INVOKING = "1"
    $CdrdaoScript = ($RootDir -replace '\\', '/') + "/scripts/build_cdrdao_windows.sh"
    Write-Host "Building cdrdao (a few minutes, the first time)..."
    & $Bash -lc "'$CdrdaoScript'"
    if ($LASTEXITCODE -ne 0) { Write-Host "building cdrdao failed - fix the error above before continuing."; exit 1 }
}
Copy-Item (Join-Path $CdrdaoOut "*.exe"), (Join-Path $CdrdaoOut "*.dll") $Bundle
Copy-Item (Join-Path $CdrdaoOut "licenses\*") (Join-Path $Bundle "licenses")

Set-Location $SrcDir

# derive the version from the nearest git tag - falls back to a dev placeholder if no tags exist yet
# (a repository with no tags makes git print "fatal: No names found" to stderr: in Windows PowerShell 5.1 that is a terminating error under
# $ErrorActionPreference = "Stop", which stopped this script on the first dev build, so the call runs with errors not stopping)
$RawTag = & { $ErrorActionPreference = "Continue"; git -C $RootDir describe --tags --abbrev=0 2>$null }
if (-not $RawTag) { $Version = "0.0.0-dev" } else { $Version = $RawTag -replace '^v', '' }
Write-Host "Building version: $Version"
# the built app shows this on its splash screen; it is only needed while compiling (a run from source says "development"),
# so it is removed again when the script ends
"APP_VERSION = `"$Version`"" | Set-Content $VersionFile

try {
    # Windows --product-version needs EXACTLY 4 numeric parts, no suffix allowed (eg "0.0.0-dev" is invalid):
    # strip any non-numeric suffix, then pad or trim to exactly 4 parts
    $NumericVersion = $Version -replace '[^0-9.].*$', ''
    $Parts = $NumericVersion.Split('.')
    while ($Parts.Count -lt 4) { $Parts += "0" }
    $ProductVersion = ($Parts[0..3] -join '.')
    Write-Host "Windows product version: $ProductVersion"

    if (-not (Test-Path "pysidedeploy.spec")) {
        Write-Host "No spec file found - generating one with our known-good settings..."
        uv run pyside6-deploy --init main.py
        (Get-Content pysidedeploy.spec) -replace '^title = .*', "title = $AppName" | Set-Content pysidedeploy.spec
        (Get-Content pysidedeploy.spec) -replace '^mode = .*', 'mode = standalone' | Set-Content pysidedeploy.spec
        Write-Host "Spec generated and configured."
    }
    # the spec keeps the interpreter and icon it was generated with, so point it at this project's on every run
    (Get-Content pysidedeploy.spec) -replace '^icon = .*', 'icon = ../assets/icon/Dubcrate.ico' | Set-Content pysidedeploy.spec
    $Python = Join-Path $RootDir ".venv\Scripts\python.exe"
    (Get-Content pysidedeploy.spec) -replace '^python_path = .*', "python_path = $Python" | Set-Content pysidedeploy.spec

    # Data the app reads at run time, paths relative to src\ (where this runs), as SRC=DEST inside the app: the same as
    # scripts/build_macos.sh and scripts/build_linux.sh (tests/test_build_files.py checks that they agree, and that every file exists).
    # The exception is the tools: Nuitka's --include-data-dir leaves out every .exe (and .dll), so ffmpeg, ffprobe, cdrdao and its two DLLs are named one by one.
    $DataArgs = "--include-data-dir=dubcrate/ui/icons=dubcrate/ui/icons --include-data-files=dubcrate/ui/style.qss.template=dubcrate/ui/style.qss.template --include-data-files=dubcrate/ui/style_system6.qss.template=dubcrate/ui/style_system6.qss.template --include-data-dir=dubcrate/ui/fonts=dubcrate/ui/fonts --include-data-dir=dubcrate/ui/help=dubcrate/ui/help --include-data-files=../assets/icon/AppIcon.png=dubcrate/ui/AppIcon.png --include-data-files=../LICENSE=LICENSE --include-data-files=../build/ffmpeg/bundle/ffmpeg.exe=dubcrate/bin/ffmpeg.exe --include-data-files=../build/ffmpeg/bundle/ffprobe.exe=dubcrate/bin/ffprobe.exe --include-data-files=../build/ffmpeg/bundle/cdrdao.exe=dubcrate/bin/cdrdao.exe --include-data-files=../build/ffmpeg/bundle/msys-2.0.dll=dubcrate/bin/msys-2.0.dll --include-data-files=../build/ffmpeg/bundle/msys-iconv-2.dll=dubcrate/bin/msys-iconv-2.dll --include-data-dir=../build/ffmpeg/bundle/licenses=dubcrate/bin/licenses"

    # Modules that are loaded by name, which Nuitka's import scan can miss: the rekordbox reader with its encrypted-database driver,
    # and the C bridge miniaudio is built on (cffi). (No PyObjC: DiscRecording is macOS's.) --smoke-test shows if one is missing.
    $ModuleArgs = "--include-package=pyrekordbox --include-package=sqlcipher3 --include-package=sqlalchemy.dialects.sqlite --include-package=cffi --include-module=_cffi_backend"

    # extra_args is regenerated on EVERY run (not just first-time setup), so the version stays current even on an existing spec
    (Get-Content pysidedeploy.spec) -replace '^extra_args = .*', "extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --windows-console-mode=disable --windows-product-name=$AppName --windows-file-description=$AppName --product-version=$ProductVersion $ModuleArgs $DataArgs" | Set-Content pysidedeploy.spec

    Write-Host "Building $AppName..."
    uv run pyside6-deploy -c pysidedeploy.spec
    if ($LASTEXITCODE -ne 0) { Write-Host "pyside6-deploy failed - fix the error above before continuing."; exit 1 }

    $Dist = Join-Path $SrcDir "$AppName.dist"
    foreach ($Needed in @("main.exe", "dubcrate\bin\ffmpeg.exe", "dubcrate\bin\ffprobe.exe", "dubcrate\bin\cdrdao.exe", "dubcrate\bin\msys-2.0.dll", "dubcrate\bin\msys-iconv-2.dll", "dubcrate\bin\licenses\cdrdao-COPYING.txt")) {
        if (-not (Test-Path (Join-Path $Dist $Needed))) { Write-Host "The build is missing $Needed"; exit 1 }
    }
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $VersionFile
}

# The installer (Inno Setup): assets\windows\Dubcrate.iss packs src\Dubcrate.dist into Dubcrate-windows-x64-Setup.exe
$Iscc = @(
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
Set-Location $RootDir
if ($Iscc) {
    Write-Host "Making the installer..."
    & $Iscc "/DMyAppVersion=$Version" "assets\windows\$AppName.iss"
    if ($LASTEXITCODE -ne 0) { Write-Host "Inno Setup failed - fix the error above."; exit 1 }
    Write-Host ""
    Write-Host "Done - the app is $Dist (run main.exe in it)."
    Write-Host "Installer: $RootDir\$AppName-windows-x64-Setup.exe"
} else {
    Write-Host ""
    Write-Host "Done - the app is $Dist (run main.exe in it)."
    Write-Host "Inno Setup 6 was not found, so no installer was made (winget install JRSoftware.InnoSetup, then run this again)."
}
