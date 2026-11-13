# Green Metrics Studio: one image for Hugging Face Spaces, Render or any Docker host.
#   docker build -t green-metrics-studio .
#   docker run -p 7860:7860 -v gms-data:/data green-metrics-studio
ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=7860 \
    GMS_DATA_DIR=/data \
    HOME=/home/app

# Java runs OPSIN (chemical name -> structure). libxrender/libxext are needed by RDKit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py ./
COPY .streamlit ./.streamlit
COPY config ./config
COPY gms ./gms
COPY ui ./ui

# Run as a normal user (uid 1000, as Hugging Face Spaces expects). /data holds the feedback
# database and the lookup caches: mount a volume there to keep them.
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app:app /data /app
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '7860') + '/_stcore/health', timeout=4)"

# XSRF and CORS protection stay on (see .streamlit/config.toml). The platform sets PORT where needed.
CMD ["sh", "-c", "exec streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0"]
