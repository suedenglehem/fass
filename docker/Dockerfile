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

FROM jrottenberg/ffmpeg:9.0-nvidia2404

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY fass/ /work/fass/
COPY pyproject.toml README.md /work/
COPY docker/entrypoint.sh /entrypoint.sh
# M10.4: the base image links libvmaf but ships no model file; the
# comparison plan's libvmaf metric loads this one (FASS_VMAF_MODEL)
COPY docker/models/ /opt/vmaf-models/
RUN chmod +x /entrypoint.sh \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir ".[api]"

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
