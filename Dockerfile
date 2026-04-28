FROM dhi.io/uv:0.11.8-debian13@sha256:0d696ef68d115128547e50bb9862cc565319841a21541f956860830dc2ba74ae AS uv

FROM dhi.io/python:3.14.4-debian13-dev@sha256:8d91cf3840e88d303f55fa61d67eb34c3c760c394edfb1d754d9b88b5f68cb2b AS builder
ARG TARGETPLATFORM
COPY --from=uv /usr/local/bin/uv /usr/local/bin/
WORKDIR /app

RUN python -m venv /app/.venv
# GoReleaser dockers_v2 provides the built wheel under $TARGETPLATFORM/.
COPY ${TARGETPLATFORM}/*.whl /tmp/
RUN uv pip install --python /app/.venv/bin/python /tmp/*.whl

FROM dhi.io/python:3.14.4-debian13@sha256:0d5b16a6304ae84e58b163a0daf4b7dec6df8c829302d8f72948ab131593034e
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /workspace
COPY --from=builder /app/.venv /app/.venv
ENTRYPOINT ["pid"]
