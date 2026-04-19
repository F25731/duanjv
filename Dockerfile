FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        fluxbox \
        novnc \
        procps \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip \
    && pip install -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . /app

RUN chmod +x /app/docker/start-vnc.sh

CMD ["/app/docker/start-vnc.sh"]
