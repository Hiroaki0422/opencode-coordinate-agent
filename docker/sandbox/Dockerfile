FROM python:3.12-slim

ARG OPENCODE_VERSION=1.15.11

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git nodejs npm \
    && npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /workspace

CMD ["python", "--version"]
