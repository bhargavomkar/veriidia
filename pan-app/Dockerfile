FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    APP_ENV=local

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11-utils x11vnc novnc websockify gosu nginx fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/veriidia
COPY backend/requirements.txt /opt/veriidia/requirements.txt
RUN pip install --no-cache-dir -r /opt/veriidia/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 runner \
    && chmod -R a+rX /ms-playwright

COPY backend/app.py backend/registration.py backend/database.py /opt/veriidia/backend/
COPY index.html registration.html /opt/veriidia/
COPY nginx.production.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /opt/veriidia/entrypoint.sh
RUN rm -f /etc/nginx/sites-enabled/default \
    && chmod 0755 /opt/veriidia/entrypoint.sh

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:10000/api/health', timeout=3)"
ENTRYPOINT ["/opt/veriidia/entrypoint.sh"]
