# Minimal frps (relay server) image, built from the official release binary.
FROM alpine:3.20 AS fetch

ARG FRP_VERSION=0.71.0
ARG TARGETARCH=amd64

RUN apk add --no-cache curl tar \
    && curl -fsSL -o /tmp/frp.tar.gz \
        "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -xzf /tmp/frp.tar.gz -C /tmp \
    && mv "/tmp/frp_${FRP_VERSION}_linux_${TARGETARCH}/frps" /usr/local/bin/frps \
    && chmod 755 /usr/local/bin/frps

FROM alpine:3.20
COPY --from=fetch /usr/local/bin/frps /usr/local/bin/frps
ENTRYPOINT ["/usr/local/bin/frps"]
CMD ["-c", "/etc/frp/frps.toml"]
