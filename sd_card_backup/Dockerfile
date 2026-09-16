ARG BUILD_FROM=alpine:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache \
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
    rclone \
    smartmontools \
    cifs-utils

COPY run.sh /run.sh
COPY web_ui.py /web_ui.py
RUN chmod +x /run.sh /web_ui.py

ENTRYPOINT [ "/run.sh" ]