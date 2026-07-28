#!/bin/sh

# 打印环境信息用于调试
echo "=========================================="
echo "Magick API Service Starting"
echo "=========================================="
echo "Configuration:"
echo "  PORT: ${PORT:-8000}"
echo "  WORKERS: ${WORKERS:-4}"
echo "  MAX_CONCURRENT_PER_WORKER: ${MAX_CONCURRENT_PER_WORKER:-3}"
echo "  MAGICK_MEMORY_LIMIT: ${MAGICK_MEMORY_LIMIT:-512MiB}"
echo "  MAGICK_TIME_LIMIT: ${MAGICK_TIME_LIMIT:-300}"
echo "=========================================="

# 依赖缺失或探测失败时拒绝启动，避免暴露一个无法转换的服务。
echo "Checking dependencies..."
if ! command -v magick >/dev/null 2>&1; then
    echo "fatal: magick executable is required" >&2
    exit 1
fi
if ! command -v heif-enc >/dev/null 2>&1; then
    echo "fatal: heif-enc executable is required" >&2
    exit 1
fi
if ! magick --version >/dev/null 2>&1; then
    echo "fatal: magick version probe failed" >&2
    exit 1
fi
if ! heif-enc --help >/dev/null 2>&1; then
    echo "fatal: heif-enc help probe failed" >&2
    exit 1
fi
printf '%s\n' "  magick: $(command -v magick)"
printf '%s\n' "  heif-enc: $(command -v heif-enc)"

# 确保使用正确的端口变量
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

echo "Starting $WORKERS workers on port $PORT..."
echo "=========================================="

# 执行 uvicorn 服务器 - 启用多进程模式
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port $PORT \
    --workers $WORKERS \
    --log-level info \
    --access-log