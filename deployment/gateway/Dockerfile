# Base pinned by tag and multi-arch index digest; Dependabot (docker) proposes updates.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY deployment/gateway/requirements-linux.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && mkdir -p /var/lib/aethertwin/assets \
    && chown -R 10001:10001 /var/lib/aethertwin
COPY command_center/__init__.py command_center/auth.py command_center/controls.py command_center/protocol.py command_center/service.py command_center/gateway.py /app/command_center/
COPY visualization/__init__.py visualization/websocket_broadcaster.py /app/visualization/
COPY web/dist/ /app/web/dist/
USER 10001:10001
EXPOSE 8443
# Liveness only (/healthz needs no credential and reveals nothing); /readyz reports upstream readiness.
# The probe skips certificate checks because it targets this container's own loopback listener.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import ssl,urllib.request as u; u.urlopen('https://127.0.0.1:8443/healthz', context=ssl._create_unverified_context(), timeout=4)"]
ENTRYPOINT ["python", "-m", "command_center.gateway"]
CMD ["--host", "0.0.0.0", "--port", "8443", "--frontend", "/app/web/dist", "--asset-cache", "/var/lib/aethertwin/assets", "--ssl-certfile", "/run/tls/fullchain.pem", "--ssl-keyfile", "/run/tls/privkey.pem"]
