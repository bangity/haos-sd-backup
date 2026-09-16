ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM $BUILD_FROM

RUN apk update && apk add --no-cache \
    bash \
    pv \
    pigz \
    curl \
    util-linux \
    coreutils \
    sqlite \
    parted \
    gptfdisk \
    jq \
    python3 \
    tzdata \
    rclone

COPY run.sh /run.sh
COPY web_ui.py /web_ui.py
RUN chmod +x /run.sh /web_ui.py

ENTRYPOINT [ "/run.sh" ]