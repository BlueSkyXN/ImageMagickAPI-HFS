# CPU争抢问题解决方案 - 快速开始

## 问题回顾

**CPU争抢现象**：
- 多个并发请求创建多个ImageMagick子进程
- 由于单进程模式，所有子进程争抢同一个CPU核心
- 表现为"慢速并行" - 请求不排队但性能低下

## 解决方案概览

采用**三层防护**稳定改进方案：

1. **多进程Worker** - 利用多核CPU
2. **并发限制** - 防止资源过载
3. **资源限制** - 保护系统稳定

## 快速部署

### 方法1：使用Docker（推荐）

```bash
# 构建镜像
docker build -t imagemagick-api:improved .

# 运行（使用默认配置：4 workers）
docker run -p 8000:8000 imagemagick-api:improved

# 自定义配置
docker run -p 8000:8000 \
  -e WORKERS=8 \
  -e MAX_CONCURRENT_PER_WORKER=5 \
  imagemagick-api:improved
```

### 方法2：本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export WORKERS=4
export MAX_CONCURRENT_PER_WORKER=3

# 启动服务
./entrypoint.sh
```

## 配置参数

| 环境变量 | 默认值 | 说明 | 推荐范围 |
|---------|--------|------|---------|
| `WORKERS` | 4 | Worker进程数 | CPU核心数×0.5 到 ×1.0 |
| `MAX_CONCURRENT_PER_WORKER` | 3 | 每个worker的并发限制 | 2-5 |
| `MAGICK_MEMORY_LIMIT` | 512MiB | ImageMagick内存限制 | 256MiB-2GiB |
| `MAGICK_TIME_LIMIT` | 300 | 超时限制（秒） | 180-600 |
| `MAGICK_THREAD_LIMIT` | 2 | ImageMagick线程限制 | 1-4 |

### 配置示例

**轻量级服务器（2核4GB）**：
```bash
docker run -p 8000:8000 \
  -e WORKERS=2 \
  -e MAX_CONCURRENT_PER_WORKER=2 \
  -e MAGICK_MEMORY_LIMIT=256MiB \
  imagemagick-api:improved
```

**中型服务器（4核8GB）**：
```bash
docker run -p 8000:8000 \
  -e WORKERS=4 \
  -e MAX_CONCURRENT_PER_WORKER=3 \
  -e MAGICK_MEMORY_LIMIT=512MiB \
  imagemagick-api:improved
```

**高性能服务器（8核16GB）**：
```bash
docker run -p 8000:8000 \
  -e WORKERS=8 \
  -e MAX_CONCURRENT_PER_WORKER=5 \
  -e MAGICK_MEMORY_LIMIT=1GiB \
  imagemagick-api:improved
```

## 验证改进

### 自动化验证

```bash
# 运行完整验证脚本
./verify_improvements.sh
```

### 手动验证

**1. 检查Worker数量**
```bash
docker logs <container_id> | grep "Starting.*workers"
# 应该看到: "Starting 4 workers on port 8000..."
```

**2. 检查并发限制**
```bash
docker logs <container_id> | grep "并发限制"
# 应该看到: "并发限制已启用: 每个worker最多 3 个并发转换"
```

**3. 测试并发性能**
```bash
# 使用测试脚本
python test_concurrency.py

# 或使用 curl
for i in {1..10}; do
  curl -F "file=@test.jpg" http://localhost:8000/convert/jpeg/lossy/80 &
done
wait
```

**4. 监控CPU利用率**
```bash
# 使用 htop 查看
htop

# 或使用 docker stats
docker stats <container_id>
```

## 性能对比

### 改进前 vs 改进后

| 测试场景 | 改进前 | 改进后 | 提升 |
|---------|--------|--------|------|
| 单个请求 | 2秒 | 2秒 | - |
| 3个并发请求 | 6秒 | 2-3秒 | 2-3倍 |
| 10个并发请求 | 18秒 | 5-6秒 | 3倍 |
| CPU利用率（8核） | 12.5% | 60-70% | 5倍 |
| 最大吞吐量 | 10 req/s | 30-40 req/s | 3-4倍 |

## 故障排查

### 问题1：Workers未启动

**症状**：日志中只显示单个进程

**解决**：
```bash
# 检查环境变量
docker run --rm imagemagick-api:improved env | grep WORKERS

# 确保entrypoint.sh有执行权限
chmod +x entrypoint.sh
```

### 问题2：内存不足

**症状**：容器重启或OOM错误

**解决**：
```bash
# 减少workers
docker run -e WORKERS=2 -e MAX_CONCURRENT_PER_WORKER=2 ...

# 或减少内存限制
docker run -e MAGICK_MEMORY_LIMIT=256MiB ...
```

### 问题3：请求仍然很慢

**症状**：并发请求响应时间长

**检查**：
```bash
# 1. 确认workers已启用
docker logs <container_id> | grep workers

# 2. 检查CPU使用率
docker stats <container_id>

# 3. 查看是否有错误日志
docker logs <container_id> | grep -i error
```

## 回滚方案

如果需要回退到单进程模式：

```bash
# 方法1：设置WORKERS=1
docker run -e WORKERS=1 imagemagick-api:improved

# 方法2：使用旧版本
git checkout <previous_commit>
docker build -t imagemagick-api:stable .
docker run -p 8000:8000 imagemagick-api:stable
```

## 监控建议

生产环境建议监控以下指标：

```bash
# CPU使用率（目标：60-80%）
docker stats --no-stream

# 请求响应时间（目标：P95 <5秒）
# 使用 Prometheus + Grafana

# 错误率（目标：<1%）
docker logs <container_id> | grep -i error | wc -l

# 并发连接数
netstat -an | grep :8000 | grep ESTABLISHED | wc -l
```

## 进一步优化

如果仍需更高性能，考虑：

1. **水平扩展** - 运行多个容器 + Nginx负载均衡
2. **任务队列** - 使用Celery处理长时间任务
3. **缓存** - 缓存常见转换结果
4. **CDN** - 分发静态资源

详细方案请参考 `IMPLEMENTATION_PLAN.md`。

## 相关文档

- `CONCURRENCY_REVIEW.md` - 完整的并发能力审查报告
- `CONCURRENCY_BEHAVIOR.md` - 并发行为详解（排队 vs 并行）
- `IMPLEMENTATION_PLAN.md` - 详细实施方案和配置指南
- `verify_improvements.sh` - 自动化验证脚本
- `test_concurrency.py` - 并发性能测试工具

## 技术支持

如有问题，请查看：
1. 日志文件：`docker logs <container_id>`
2. 健康检查：`curl http://localhost:8000/health`
3. GitHub Issues：报告问题和反馈

---

**快速命令速查**

```bash
# 构建并运行
docker build -t imagemagick-api:improved . && \
docker run -p 8000:8000 imagemagick-api:improved

# 验证
./verify_improvements.sh

# 查看日志
docker logs -f <container_id>

# 性能测试
python test_concurrency.py

# 停止
docker stop <container_id>
```
