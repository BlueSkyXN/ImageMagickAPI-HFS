FROM python:3.10-slim AS imagemagick-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config curl ca-certificates xz-utils \
    libheif-dev libjpeg62-turbo-dev libpng-dev libwebp-dev libtiff-dev \
    liblcms2-dev libxml2-dev libfreetype6-dev libbz2-dev liblzma-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/build-imagemagick.sh /tmp/build-imagemagick.sh
RUN sh /tmp/build-imagemagick.sh

FROM python:3.10-slim
ENV PATH=/opt/imagemagick/bin:$PATH
ENV LD_LIBRARY_PATH=/opt/imagemagick/lib
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
    libheif1 libjpeg62-turbo libpng16-16t64 libwebp7 libwebpmux3 libwebpdemux2 \
    libtiff6 liblcms2-2 libxml2 libfreetype6 libbz2-1.0 liblzma5 zlib1g libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=imagemagick-build /opt/imagemagick /opt/imagemagick

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
