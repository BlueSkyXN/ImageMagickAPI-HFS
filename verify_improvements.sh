#!/bin/bash

echo "=========================================="
echo "ImageMagick API 改进验证脚本"
echo "=========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 测试结果计数
PASSED=0
FAILED=0

# 辅助函数
print_test() {
    echo ""
    echo "测试: $1"
    echo "----------------------------------------"
}

print_pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((PASSED++))
}

print_fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((FAILED++))
}

print_info() {
    echo -e "${YELLOW}ℹ INFO${NC}: $1"
}

# 测试1: 检查Docker镜像构建
print_test "Docker镜像构建"
if docker build -t imagemagick-api:test . > /dev/null 2>&1; then
    print_pass "Docker镜像构建成功"
else
    print_fail "Docker镜像构建失败"
fi

# 测试2: 检查环境变量
print_test "环境变量配置"
ENV_CHECK=$(docker run --rm imagemagick-api:test env | grep -E "(WORKERS|MAX_CONCURRENT|MAGICK_)")
if echo "$ENV_CHECK" | grep -q "WORKERS"; then
    print_pass "WORKERS环境变量已配置"
    print_info "$(echo "$ENV_CHECK" | grep WORKERS)"
else
    print_fail "WORKERS环境变量未配置"
fi

if echo "$ENV_CHECK" | grep -q "MAX_CONCURRENT_PER_WORKER"; then
    print_pass "MAX_CONCURRENT_PER_WORKER环境变量已配置"
    print_info "$(echo "$ENV_CHECK" | grep MAX_CONCURRENT_PER_WORKER)"
else
    print_fail "MAX_CONCURRENT_PER_WORKER环境变量未配置"
fi

if echo "$ENV_CHECK" | grep -q "MAGICK_MEMORY_LIMIT"; then
    print_pass "MAGICK_MEMORY_LIMIT环境变量已配置"
    print_info "$(echo "$ENV_CHECK" | grep MAGICK_MEMORY_LIMIT)"
else
    print_fail "MAGICK_MEMORY_LIMIT环境变量未配置"
fi

# 测试3: 启动服务并检查日志
print_test "服务启动"
print_info "启动Docker容器..."
CONTAINER_ID=$(docker run -d -p 18000:8000 imagemagick-api:test)

if [ -z "$CONTAINER_ID" ]; then
    print_fail "容器启动失败"
else
    print_pass "容器启动成功 (ID: ${CONTAINER_ID:0:12})"

    # 等待服务启动
    print_info "等待服务启动..."
    sleep 5

    # 检查日志
    print_test "服务日志检查"
    LOGS=$(docker logs $CONTAINER_ID 2>&1)

    if echo "$LOGS" | grep -q "Starting.*workers"; then
        WORKER_COUNT=$(echo "$LOGS" | grep -oP "Starting \K\d+" | head -1)
        print_pass "多进程模式已启用 ($WORKER_COUNT workers)"
    else
        print_fail "未检测到多进程模式"
    fi

    if echo "$LOGS" | grep -q "并发限制已启用"; then
        print_pass "并发限制已启用"
        print_info "$(echo "$LOGS" | grep '并发限制已启用')"
    else
        print_fail "并发限制未启用"
    fi

    # 测试4: 健康检查
    print_test "API健康检查"
    HEALTH_RESPONSE=$(curl -s http://localhost:18000/health)

    if echo "$HEALTH_RESPONSE" | grep -q "healthy"; then
        print_pass "健康检查通过"
        print_info "ImageMagick: $(echo "$HEALTH_RESPONSE" | jq -r '.imagemagick' 2>/dev/null | head -c 50)"
    else
        print_fail "健康检查失败"
        print_info "响应: $HEALTH_RESPONSE"
    fi

    # 测试5: 基础功能测试
    print_test "基础功能测试"

    # 创建测试图片
    if command -v convert &> /dev/null; then
        convert -size 800x600 xc:blue /tmp/test_verify.jpg 2>/dev/null

        if [ -f /tmp/test_verify.jpg ]; then
            print_info "测试图片已创建"

            # 发送转换请求
            CONVERT_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/output_verify.jpeg \
                -F "file=@/tmp/test_verify.jpg" \
                http://localhost:18000/convert/jpeg/lossy/80)

            if [ "$CONVERT_RESPONSE" = "200" ]; then
                print_pass "图像转换成功 (HTTP 200)"

                if [ -f /tmp/output_verify.jpeg ] && [ -s /tmp/output_verify.jpeg ]; then
                    OUTPUT_SIZE=$(stat -f%z /tmp/output_verify.jpeg 2>/dev/null || stat -c%s /tmp/output_verify.jpeg 2>/dev/null)
                    print_pass "输出文件已生成 (大小: $OUTPUT_SIZE bytes)"
                else
                    print_fail "输出文件为空或不存在"
                fi
            else
                print_fail "图像转换失败 (HTTP $CONVERT_RESPONSE)"
            fi

            # 清理
            rm -f /tmp/test_verify.jpg /tmp/output_verify.jpeg
        else
            print_info "跳过功能测试 (无法创建测试图片)"
        fi
    else
        print_info "跳过功能测试 (ImageMagick未安装在主机)"
    fi

    # 测试6: 并发性能测试（简化版）
    print_test "并发性能测试"
    if command -v convert &> /dev/null; then
        convert -size 400x300 xc:red /tmp/test_concurrent.jpg 2>/dev/null

        print_info "发送3个并发请求..."
        START_TIME=$(date +%s)

        for i in {1..3}; do
            curl -s -o /tmp/out_$i.jpeg \
                -F "file=@/tmp/test_concurrent.jpg" \
                http://localhost:18000/convert/jpeg/lossy/80 &
        done

        wait
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))

        SUCCESS_COUNT=0
        for i in {1..3}; do
            if [ -f /tmp/out_$i.jpeg ] && [ -s /tmp/out_$i.jpeg ]; then
                ((SUCCESS_COUNT++))
            fi
        done

        if [ $SUCCESS_COUNT -eq 3 ]; then
            print_pass "3个并发请求全部成功 (耗时: ${DURATION}秒)"

            if [ $DURATION -le 10 ]; then
                print_pass "并发性能良好 (耗时 ≤ 10秒)"
            else
                print_info "并发性能可接受 (耗时: ${DURATION}秒)"
            fi
        else
            print_fail "仅$SUCCESS_COUNT/3个请求成功"
        fi

        # 清理
        rm -f /tmp/test_concurrent.jpg /tmp/out_*.jpeg
    else
        print_info "跳过并发测试 (ImageMagick未安装在主机)"
    fi

    # 测试7: 资源监控
    print_test "资源使用情况"
    STATS=$(docker stats $CONTAINER_ID --no-stream --format "table {{.CPUPerc}}\t{{.MemUsage}}")
    print_info "容器资源:"
    echo "$STATS" | sed 's/^/  /'

    # 清理容器
    print_info "停止并删除测试容器..."
    docker stop $CONTAINER_ID > /dev/null 2>&1
    docker rm $CONTAINER_ID > /dev/null 2>&1
    print_pass "测试容器已清理"
fi

# 测试8: 代码检查
print_test "代码完整性检查"

if grep -q "conversion_semaphore" main.py; then
    print_pass "main.py包含并发信号量"
else
    print_fail "main.py缺少并发信号量"
fi

if grep -q "WORKERS" entrypoint.sh; then
    print_pass "entrypoint.sh配置了workers"
else
    print_fail "entrypoint.sh未配置workers"
fi

if grep -q "MAGICK_MEMORY_LIMIT" Dockerfile; then
    print_pass "Dockerfile包含ImageMagick资源限制"
else
    print_fail "Dockerfile缺少ImageMagick资源限制"
fi

# 总结
echo ""
echo "=========================================="
echo "测试总结"
echo "=========================================="
echo -e "${GREEN}通过: $PASSED${NC}"
echo -e "${RED}失败: $FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ 所有测试通过！改进已成功实施。${NC}"
    echo ""
    echo "建议的下一步:"
    echo "  1. 提交并推送代码"
    echo "  2. 在生产环境部署前进行更全面的负载测试"
    echo "  3. 配置监控和告警"
    exit 0
else
    echo -e "${RED}✗ 部分测试失败，请检查并修复问题。${NC}"
    exit 1
fi
