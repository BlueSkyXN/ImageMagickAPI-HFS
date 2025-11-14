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

# 验证Magick是否可用
echo "Checking dependencies..."
magick --version | head -n 1
which heif-enc

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