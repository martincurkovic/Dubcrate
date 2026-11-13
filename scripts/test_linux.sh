#!/usr/bin/env bash
set -euo pipefail

# test_linux.sh - runs the test suite on Linux in Docker (Ubuntu 24.04), from any OS with Docker running.
#
#   scripts/test_linux.sh                       # the whole suite
#   scripts/test_linux.sh tests/test_tools.py   # pytest arguments go straight through
#
# The first run builds an image with the dependencies (a few minutes); later runs reuse it until pyproject.toml or uv.lock
# change. Your files are copied in read-only, so nothing in the repository is touched. On an Apple Silicon Mac the container
# is Arm Linux; the GitHub runner is Intel Linux. No drive, sound card or display is involved, as in CI.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
docker build -q -f "$ROOT_DIR/scripts/linux-test.Dockerfile" --build-context repo="$ROOT_DIR" -t dubcrate-linux-test "$ROOT_DIR/scripts" >/dev/null
docker run --rm -v "$ROOT_DIR":/host:ro dubcrate-linux-test bash -c '
set -e
mkdir /work
rsync -a --exclude .venv --exclude build --exclude "*.app" --exclude "*.dmg" --exclude "*.dist" --exclude "*.build" \
    --exclude src/deployment --exclude "*.spec" --exclude .git --exclude __pycache__ --exclude .pytest_cache \
    --exclude .ruff_cache /host/ /work/
cd /work
uv sync --no-group build
uv run --no-group build pytest ${@:-tests/} -q -ra -o faulthandler_timeout=90 -p no:cacheprovider
' _ "$@"
