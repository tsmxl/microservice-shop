FROM debian:13.1-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iptables kmod util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY infra/firewall/fw.sh /usr/local/bin/fw

ENTRYPOINT ["fw"]
CMD ["run"]
