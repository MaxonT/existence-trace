FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/MaxonT/existence-trace" \
      org.opencontainers.image.description="existence-trace: record your traces of existence, with a self-improving preference loop" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY etrace ./etrace
RUN pip install --no-cache-dir . && rm -rf /src
WORKDIR /data
ENV NO_STAR_PROMPT=1
ENTRYPOINT ["etrace"]
CMD ["--help"]
