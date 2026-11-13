# (cdrdao, xorriso and genisoimage are the tools Dubcrate drives on Linux; xvfb and imagemagick give an X display and screenshots; the libxcb ones are what Qt's X11 plugin needs on a desktop, which
# the offscreen tests never load.)
# The Linux test environment for scripts/test_linux.sh: Ubuntu 24.04 (glibc 2.39: the Arm Linux PySide6 wheel needs 2.39,
# the Intel one 2.34), the system libraries Qt's wheels expect, ffmpeg, and the project's dependencies, installed here so a
# test run only has to install Dubcrate itself. Mirrors the Linux leg of .github/workflows/test.yml.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy PATH="/root/.local/bin:$PATH"
RUN apt-get update -qq && apt-get install -y -qq ffmpeg libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 \
        libglib2.0-0 fonts-dejavu-core curl ca-certificates rsync \
        cdrdao xorriso genisoimage xvfb xauth imagemagick \
        libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0 >/dev/null \
    && curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
WORKDIR /deps
COPY --from=repo pyproject.toml uv.lock .python-version ./
RUN uv sync --no-install-project --no-group build
