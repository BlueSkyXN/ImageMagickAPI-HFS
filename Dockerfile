FROM python:3.10-slim AS imagemagick-package
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/install-imagemagick.sh /tmp/install-imagemagick.sh
RUN sh /tmp/install-imagemagick.sh

FROM python:3.10-slim
ENV PATH=/opt/imagemagick/bin:$PATH
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV TEMP_DIR=/app/temp
ENV MAGICK_MEMORY_LIMIT=512MiB
ENV MAGICK_MAP_LIMIT=1GiB
ENV MAGICK_DISK_LIMIT=4GiB
ENV MAGICK_TIME_LIMIT=300
ENV MAGICK_THREAD_LIMIT=2
ENV WORKERS=1
ENV MAX_CONCURRENT_PER_WORKER=1
ENV ENCODER_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    libheif-examples libheif-plugin-aomenc libheif-plugin-x265 libheif-plugin-libde265 libheif-plugin-dav1d \
    libx11-6 libfontconfig1 libfreetype6 libfribidi0 libharfbuzz0b libstdc++6 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=imagemagick-package /opt/imagemagick /opt/imagemagick
RUN magick --version | grep -F 'ImageMagick 7.1.2-32 '

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY entrypoint.sh .
COPY static/ ./static/
COPY templates/ ./templates/
RUN chmod +x /app/entrypoint.sh && mkdir -p /app/temp && chmod 777 /app/temp

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
