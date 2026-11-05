FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8780 \
    MEMSAVE_DATA_DIR=/data

WORKDIR /app

ARG MEMSAVE_COMMIT=""
ARG MEMSAVE_COMMIT_DATE=""
ARG MEMSAVE_VERSION="1.0.2"
ARG MEMSAVE_CREATED=""
ENV MEMSAVE_COMMIT=${MEMSAVE_COMMIT} \
    MEMSAVE_COMMIT_DATE=${MEMSAVE_COMMIT_DATE}
LABEL org.opencontainers.image.title="memsave" \
      org.opencontainers.image.description="Personal image archive with AI tags" \
      org.opencontainers.image.version="${MEMSAVE_VERSION}" \
      org.opencontainers.image.revision="${MEMSAVE_COMMIT}" \
      org.opencontainers.image.created="${MEMSAVE_CREATED}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 8780

HEALTHCHECK --interval=5m --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/api/version', timeout=3)"

CMD ["python", "-m", "memsave", "serve"]



