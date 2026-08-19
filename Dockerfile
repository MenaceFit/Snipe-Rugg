FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# bot_main is the real deployment (all 8 phases wired together; requires
# DISCORD_TOKEN and DISCORD_ALERT_CHANNEL_ID - see .env.example). main.py's
# Phase-1-only demo is for local `--wallet <address>` runs, not this image.
CMD ["python", "-m", "snipe_rugg.bot_main"]
