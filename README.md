---
title: Magick 图像转换器 # 显示在 Space 页面的标题 (可自定义)
emoji: 🖼️ # Space 图标的 Emoji (可选)
colorFrom: blue # 主题颜色起始 (可选)
colorTo: green # 主题颜色结束 (可选)
sdk: docker # 指定这是一个基于 Docker 的 Space (非常重要)
app_port: 8000 # 你的 FastAPI 应用在容器内部监听的端口 (必须与 Dockerfile CMD 中指定的端口一致)
pinned: false # 是否在你的个人资料页置顶这个 Space (可选)
---

# 🧙‍♂️ Magick 动态图像转换 API (V3)

本项目提供一个基于 FastAPI 和 ImageMagick 的高性能 REST API，支持通过动态 URL 路径对图像进行多格式转换，包括动画图像处理。

## ✨ 功能特性

* **🌐 图形化界面**: 访问根路径即可使用现代化 Web 上传界面，零编程门槛
* **🎯 动态路径 API**: 通过 URL 路径直接指定目标格式、转换模式和质量参数
* **📤 双上传模式**: 支持网页表单上传和 RESTful API 调用两种方式
* **🎬 动画支持**: 完整支持 GIF、Animated WebP/AVIF/APNG 等动画格式
* **🔄 多格式转换**: 支持 AVIF、WebP、JPEG、PNG、GIF、HEIF 格式互转
* **⚙️ 灵活配置**: 支持有损/无损两种模式，质量参数可在 0-100 范围自由调节
* **🛡️ 安全可靠**: 文件大小限制、超时控制、格式验证、依赖预检查
* **🚀 性能优化**: 智能 `-coalesce` 使用、异步处理、后台清理

## 📡 API 端点

### 1. Web 上传界面 (新功能!)

**端点**: `GET /`

访问根路径即可打开用户友好的图形化上传界面，无需编程知识即可使用。

**特点**:
- 🎨 现代化响应式界面
- 📱 支持移动设备
- 🖱️ 拖拽上传支持
- 📊 实时参数预览
- 🔗 快速访问 API 文档

**表单上传端点**: `POST /`

支持通过 HTML 表单提交转换请求（与网页界面使用相同端点）。

**表单参数**:
- `file`: 图像文件（必需）
- `target_format`: 目标格式（默认: `webp`）
- `mode`: 转换模式（默认: `lossy`）
- `setting`: 质量参数 0-100（默认: `80`）

**示例**:
```bash
# 使用表单方式上传（与网页界面相同）
curl -X POST "http://localhost:8000/" \
  -F "file=@input.jpg" \
  -F "target_format=avif" \
  -F "mode=lossy" \
  -F "setting=85" \
  -o output.avif
```

### 2. API 端点（程序化调用）

**端点**: `POST /convert/{target_format}/{mode}/{setting}`

**路径参数**:
- `target_format`: 目标格式 (`avif` | `webp` | `jpeg` | `png` | `gif` | `heif`)
- `mode`: 转换模式 (`lossy` | `lossless`)
- `setting`: 质量/压缩参数 (0-100)
  - **lossy 模式**: `0`=最低质量，`100`=最高质量
  - **lossless 模式**: `0`=最慢/最佳压缩，`100`=最快/最低压缩

**请求体**: `multipart/form-data` 文件上传 (字段名: `file`)

**响应**: 转换后的图像文件

**支持的输入格式**: JPG, PNG, GIF, WebP, AVIF, HEIF, HEIC, BMP, TIFF

**示例**:
```bash
# 转换为高质量有损 AVIF (质量 80)
curl -X POST "https://your-api.hf.space/convert/avif/lossy/80" \
  -F "file=@input.jpg" \
  -o output.avif

# 转换为无损 WebP (最佳压缩)
curl -X POST "https://your-api.hf.space/convert/webp/lossless/0" \
  -F "file=@animation.gif" \
  -o output.webp

# 转换为中等质量 JPEG (质量 75)
curl -X POST "https://your-api.hf.space/convert/jpeg/lossy/75" \
  -F "file=@input.png" \
  -o output.jpg
```

### 3. 健康检查

**端点**: `GET /health`

**响应**: JSON 格式的服务状态信息

```json
{
  "status": "healthy",
  "imagemagick": "Version: ImageMagick 7.1.0-x",
  "avif_encoder": "/usr/bin/heif-enc",
  "disk_space": {
    "free_mb": 15234.56,
    "temp_dir": "/tmp"
  },
  "resource_limits": {
    "max_file_size_mb": 200,
    "timeout_seconds": 300
  }
}
```

## 🔧 技术细节

### 转换模式详解

#### Lossy (有损) 模式
- **AVIF**: 使用 `cq-level` 参数 (0-63)，setting=100 映射为 cq=0 (最佳质量)
- **WebP**: 使用 `-quality` 参数 (0-100)，直接映射
- **JPEG**: 使用 `-quality` 参数 (0-100)，直接映射
- **HEIF**: 使用 `-quality` 参数 (0-100)，直接映射
- **PNG/GIF**: 通过 `-colors` 减少调色板颜色模拟有损 (2-256 色)

#### Lossless (无损) 模式
- **AVIF**: 使用 `avif:lossless=true` + `avif:speed` (0-10)
- **WebP**: 使用 `webp:lossless=true` + `webp:method` (0-6)
- **PNG**: 使用 zlib 压缩级别 (0-9)，映射到 `-quality` (91-100)
- **HEIF**: 使用 `heif:lossless=true` + `heif:speed` (0-10)
- **JPEG**: 使用 `-quality 100` (JPEG 无真正无损模式)
- **GIF**: 使用 `-layers optimize` 优化帧

### 性能优化

1. **智能 Coalesce**: 仅对动画格式 (GIF, WebP, APNG) 使用 `-coalesce`，避免静态图片性能损失
2. **异步处理**: 使用 asyncio 进行非阻塞 I/O 操作
3. **后台清理**: 使用 FastAPI BackgroundTasks 异步清理临时文件
4. **超时控制**: 5 分钟超时保护，防止长时间占用资源

### 安全特性

1. **文件大小限制**: 默认最大 200MB
2. **格式验证**: 仅接受白名单内的图像格式
3. **依赖预检查**: AVIF/HEIF 转换前检查 heif-enc 可用性
4. **路径隔离**: 每个请求使用独立的 UUID 临时目录
5. **错误处理**: 完整的异常捕获和 HTTP 状态码返回

## 🚀 部署

### Docker 部署

```bash
# 构建镜像
docker build -t magick-api .

# 运行容器
docker run -p 8000:8000 magick-api
```

### Hugging Face Spaces 部署

1. Fork 或上传此仓库到 Hugging Face Spaces
2. 确保 README.md 前置元数据配置正确 (`sdk: docker`, `app_port: 8000`)
3. Space 会自动构建和部署

### 环境变量

- `TEMP_DIR`: 临时文件目录 (默认: 系统临时目录，Docker 中为 `/app/temp`)
- `PORT`: 服务监听端口 (默认: 8000)

## 📦 依赖

- Python 3.10+
- FastAPI
- Uvicorn
- ImageMagick 7+
- libheif-examples (提供 heif-enc 编码器)

## 🐛 已知问题与修复

### V4 版本更新 (当前版本)

1. ✅ **新增 Web 上传界面**: 在根路径 `/` 提供现代化图形化上传界面
2. ✅ **新增表单上传 API**: 支持通过 `POST /` 使用表单数据上传
3. ✅ **代码架构重构**: 提取核心转换逻辑，实现代码复用
4. ✅ **改进用户体验**: 实时参数提示、拖拽上传、响应式设计
5. ✅ **完善 API 文档**: 在 Web 界面提供快速访问链接

### V3 版本修复

1. ✅ **修复 Timeout 实现**: 超时现在正确应用于进程执行而非进程创建
2. ✅ **修复 WebP 无损质量**: 无损模式下 quality 固定为 100
3. ✅ **修复 PNG 质量映射**: 修正为完整的 91-100 范围
4. ✅ **修复 WebP effort 计算**: 使用线性插值确保精确映射 0-6
5. ✅ **修复临时目录硬编码**: 支持环境变量和系统临时目录
6. ✅ **优化 -coalesce 性能**: 仅对动画格式使用
7. ✅ **修复 BackgroundTasks**: 移除重复参数传递
8. ✅ **添加文件格式验证**: 上传前验证文件扩展名
9. ✅ **添加依赖预检查**: AVIF/HEIF 转换前检查编码器可用性
10. ✅ **修复测试脚本**: 使用正确的 API 路径格式

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📞 联系方式

如有问题或建议，请在 GitHub Issues 中提出。