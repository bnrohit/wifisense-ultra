FROM python:3.12-slim

ARG INSTALL_EXTRAS=core
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_EXTRAS" = "full" ]; then pip install --no-cache-dir -r requirements-full.txt; \
    elif [ "$INSTALL_EXTRAS" = "ml" ]; then pip install --no-cache-dir -r requirements-ml.txt; \
    elif [ "$INSTALL_EXTRAS" = "homeassistant" ]; then pip install --no-cache-dir -r requirements-homeassistant.txt; fi

COPY . .
RUN mkdir -p /app/data
EXPOSE 5000 8089
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3).read()" || exit 1
CMD ["python", "-m", "wifisense_ultra.main"]
