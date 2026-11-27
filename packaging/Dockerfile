FROM python:3.11-slim-bullseye
# glibc 2.31 baseline. The archived security repository retains the final patches.
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list \
    && sed -i 's|https://deb.debian.org/debian-security|https://archive.debian.org/debian-security|g' /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
    binutils curl squashfs-tools patchelf \
    libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xinerama0 libxcb-randr0 libxcb-shape0 \
    libxcb-sync1 libxcb-xfixes0 libxcb-xkb1 libdbus-1-3 libfontconfig1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN python -c 'import tomllib,subprocess; p=tomllib.load(open("pyproject.toml","rb"))["project"]; subprocess.check_call(["pip","install","--no-cache-dir",*p["dependencies"],"pyinstaller==6.16.0"])'
COPY src ./src
RUN pip install --no-deps .
COPY run.py ./
COPY packaging ./packaging
COPY scripts ./scripts
RUN bash scripts/build_appimage.sh \
    && python -c 'import shutil; shutil.rmtree("build"); shutil.rmtree("dist/robot-web-installer")'
CMD ["/bin/bash"]
