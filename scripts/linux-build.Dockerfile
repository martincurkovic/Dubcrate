# The Linux build environment for scripts/build_linux_docker.sh: Ubuntu 24.04 (the same as the Linux CI runner and the test image,
# and glibc 2.39: an AppImage built here runs on distributions at least that new), the compiler Nuitka needs, nasm for ffmpeg, the
# system libraries Qt's wheels expect (so the built app can be smoke-tested here), and the project's dependencies including the
# build group, installed in /work/.venv so a build only has to install Dubcrate itself. Mirrors the build-linux job in
# .github/workflows/build.yml.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive UV_LINK_MODE=copy PATH="/root/.local/bin:$PATH"
RUN apt-get update -qq && apt-get install -y -qq build-essential patchelf curl xz-utils nasm zip file zlib1g-dev autoconf automake libtool pkg-config git rsync ca-certificates \
        libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0 fonts-dejavu-core libnss3 libasound2t64 libpulse0 \
        libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 \
        libxcb-sync1 libxcb-xkb1 libxkbcommon-x11-0 libxcomposite1 libxdamage1 libxi6 libxtst6 >/dev/null \
    && curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
WORKDIR /work
COPY --from=repo pyproject.toml uv.lock .python-version ./
RUN uv sync --no-install-project
