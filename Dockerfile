FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_HOST=0.0.0.0 PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m playwright install --with-deps chromium \
    && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser agent_workbench ./agent_workbench
RUN mkdir /app/var && chown appuser:appuser /app/var
USER appuser
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=2)"
CMD ["python", "-m", "agent_workbench", "serve", "--container", "--host", "0.0.0.0", "--port", "8765"]
