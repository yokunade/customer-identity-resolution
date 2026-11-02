FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# Exact versions with hashes (requirements.lock), so every build installs the same
# packages. requirements.txt keeps the version floors for installing as a library.
COPY requirements.lock .
RUN pip install --require-hashes --no-deps -r requirements.lock

COPY app ./app
COPY benchmark ./benchmark

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
