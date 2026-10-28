# fass in a GPU-enabled FFmpeg image (M6).
#
# Base: jrottenberg/ffmpeg:9.0-nvidia2404 — FFmpeg 9.0 built with NVIDIA
# hardware acceleration (nvenc/nvdec) on Ubuntu 24.04.
#
#   docker build -f docker/Dockerfile -t fass:latest .
#
# The image is a DEPLOYMENT build: execution is enabled (FASS_ALLOW_EXECUTE)
# and the container expects four mounted volumes:
#   /input  host folder with the source media (read-only is fine)
#   /output where run results are written
#   /log    run logs (/log/runs/<id>.log)
#   /config config.json (the web UI's Config tab reads and writes it)
#
# TSDuck's tsanalyze (the Info/Log tab's TSAnalyze button) is compiled in a
# builder stage on the same Ubuntu release (matching glibc) and installed
# under /usr/local, which does not clash with the ffmpeg binaries.

# ---------- tsanalyze builder ----------
# Same Ubuntu release as the base image so libtsduck links against the same
# glibc/libstdc++ that the final image provides.
FROM ubuntu:24.04 AS tsduck
ARG TSDUCK_TAG=v3.45-4798
# Space-separated TSDuck make options (see CONFIG.txt in the TSDuck repo);
# NODOC is added on top in the build below — the doc target needs
# asciidoctor (Ruby), which is pointless to pull in to ship the HTML guide.
ARG TS_OPTS="NOTEST NODEKTEC NOHIDES NOVATEK NOPCSC NOEDITLINE NOJAVA"
ARG JOBS=16
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        python3 \
        pkg-config \
        libcurl4-openssl-dev \
        libssl-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${TSDUCK_TAG}" \
        https://github.com/tsduck/tsduck.git /src/tsduck
# Turn each option name into a NAME=1 make variable.
RUN cd /src/tsduck \
    && OPTS="$(for o in ${TS_OPTS} NODOC; do printf '%s=1 ' "$o"; done)" \
    && echo "TSDuck build options: ${OPTS}" \
    && make -j"${JOBS}" ${OPTS} \
    && make install ${OPTS} SYSROOT=/install SYSPREFIX=/usr/local

# ---------- fass ----------
FROM jrottenberg/ffmpeg:9.0-nvidia2404

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# mediainfo: the Files tab's mediainfo inspect path (api.py) 503s when the
# binary is absent — the base image ships only FFmpeg.
# libcurl4t64: the only RUNTIME dependency tsanalyze needs that the base
# image does not ship (libssl3, zlib1g, libstdc++6 are already installed).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv ca-certificates mediainfo \
        libcurl4t64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY fass/ /work/fass/
COPY pyproject.toml README.md /work/
COPY docker/entrypoint.sh /entrypoint.sh
# M10.4: the base image links libvmaf but ships no model file; the
# comparison plan's libvmaf metric loads this one (FASS_VMAF_MODEL)
COPY docker/models/ /opt/vmaf-models/
# TSDuck: binaries (/usr/local/bin/ts*), libtsduck/libtscore
# (/usr/local/lib), plugins (/usr/local/lib/tsduck), data files
# (/usr/local/share/tsduck).
COPY --from=tsduck /install/usr/local /usr/local
RUN chmod +x /entrypoint.sh \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir ".[api]" \
    && ldconfig

ENV PATH="/opt/venv/bin:$PATH" \
    FASS_CONFIG=/config/config.json \
    FASS_VMAF_MODEL=/opt/vmaf-models/vmaf_v0.6.1.json \
    FASS_ALLOW_EXECUTE=1 \
    FASS_RUN_DIR=/log/runs \
    FASS_INPUT_DIR=/input \
    FASS_OUTPUT_DIR=/output \
    FASS_CONTAINER_NAME=fass \
    FASS_HOST=0.0.0.0 \
    FASS_PORT=8000

VOLUME ["/input", "/output", "/log", "/config"]
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
