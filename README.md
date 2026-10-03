# Magick 动态图像转换 API (V4)

基于 FastAPI、ImageMagick 和 `libheif-examples` 的图像转换服务。它提供 Web 上传界面和 REST API，支持 AVIF、WebP、JPEG、PNG、GIF、HEIF 以及动画图像处理。

GitHub 仓库 `BlueSkyXN/ImageMagickAPI-HFS` 是唯一的产品源码事实来源。Hugging Face Space 不是源码镜像，而是由本仓库导出的五文件 thin wrapper。

## 功能特性

- `GET /` 提供响应式上传界面；`POST /` 支持表单上传。
- `POST /convert/{target_format}/{mode}/{setting}` 提供程序化转换接口。
- 支持 `avif`、`webp`、`jpeg`、`png`、`gif`、`heif` 目标格式，以及 `lossy` 与 `lossless` 模式。
- 对动画 GIF、WebP、APNG 使用按需 `-coalesce`，并按 worker 限制并发转换。
- 上传大小、文件头、临时目录和进程超时均受到保护。
- `GET /health` 显式报告 `magick` 和 `heif-enc` 依赖状态；任一依赖缺失、探测失败或临时目录不可用时返回 `503` 和 `status: unhealthy`。

## API

### Web 上传

访问 `GET /` 打开 Web 界面。表单提交到 `POST /`，参数如下：

- `file`：必需的图像文件。
- `target_format`：目标格式，默认 `heif`。
- `mode`：`lossy` 或 `lossless`，默认 `lossless`。
- `setting`：0–100 的质量或压缩速度参数，默认 `75`；无损模式优先转换速度，显式设为 `0` 则选择慢速高压缩。

```bash
curl -X POST http://localhost:8000/ \
  -F 'file=@input.jpg' \
  -F 'target_format=avif' \
  -F 'mode=lossy' \
  -F 'setting=85' \
  -o output.avif
```

### 程序化转换

```text
POST /convert/{target_format}/{mode}/{setting}
```

`target_format` 可为 `avif`、`webp`、`jpeg`、`png`、`gif`、`heif`；`mode` 可为 `lossy` 或 `lossless`；`setting` 范围为 0–100。请求体是字段名为 `file` 的 `multipart/form-data`，成功时返回转换后的图像。

```bash
curl -X POST http://localhost:8000/convert/webp/lossless/0 \
  -F 'file=@input.png' \
  -o output.webp
```

### 健康检查

```bash
curl --fail http://localhost:8000/health
```

健康响应包含 `dependencies.magick`、`dependencies.heif_enc`、磁盘空间和资源限制。依赖状态是 `available` 时响应为 `200`；任一状态为 `missing` 或 `failed` 时响应为 `503`。

## 运行时与环境变量

镜像基于 `python:3.10-slim`。ImageMagick 固定为当前稳定版 `7.1.2-32`，从官方发布源码构建并校验 SHA-256，而不是依赖发行版较旧的 ImageMagick 包。构建为 Q16、非 HDRI，启用 HEIC/AVIF、PNG、JPEG、WebP、TIFF 和 LCMS；运行镜像只复制安装产物，不包含编译工具。`libheif-examples` 提供 `heif-enc`，配套安装 AOM/x265 编码及 HEIC/AVIF 解码插件。

入口脚本验证依赖，并记录 ImageMagick、libheif 和 AOM 可用参数。`/health` 同时显示 ImageMagick/libheif 版本和转换线程配置。ImageMagick 升级不等于 AOM 编码器升级，也不会改变现有 `setting` 映射。

| 角色 | 键名 | 说明 |
| --- | --- | --- |
| local only | `TEMP_DIR` | 临时文件目录。 |
| variables | `PORT`, `PYTHONUNBUFFERED` | 服务端口（默认 `8000`）和 Python 输出行为。 |
| variables | `MAGICK_MEMORY_LIMIT`, `MAGICK_MAP_LIMIT`, `MAGICK_DISK_LIMIT`, `MAGICK_TIME_LIMIT`, `MAGICK_THREAD_LIMIT` | ImageMagick 资源限制。 |
| variables | `WORKERS`, `MAX_CONCURRENT_PER_WORKER` | 默认 `1` worker、每 worker `1` 个转换；默认单实例的信号量覆盖全部转换。多 worker 时总并发仍会乘以 worker 数。 |
| variables | `ENCODER_THREADS` | 默认 `2`，范围 `1–64`。AOM 使用 `threads`，x265 使用线程池大小并将 frame threads 设为 `1`；不是进程总线程数的硬上限。 |
| secrets | 无 | 当前服务没有已分类的运行时 secret。 |

`hfs-dev.toml` 仅记录这些键名及 HFS v2 语义，绝不记录值或凭据。复制 `.env.example` 用于本地非敏感配置；不要提交 `.env` 或任何凭据文件。

## 本地 Docker

```bash
docker build -t magick-api .
docker run --rm -p 8000:8000 magick-api
```

根 `Dockerfile` 保持产品本地运行路径。它使用 `PORT=8000`、全部既有 `MAGICK_*` 限制、`WORKERS=1`、`MAX_CONCURRENT_PER_WORKER=1` 和 `ENCODER_THREADS=2`。

### 大图、异步与并发

默认配置优先让单张大图使用两个 CPU 核心，转换进程异步等待，文件复制在线程池中执行。上传解析完成后，排队、复制和所有编码阶段共享 300 秒预算；取消或超时时会终止并回收转换进程组，再清理临时文件。复制线程无法强制取消，因此清理会等待正在进行的复制完成。日志分别记录排队、复制、ImageMagick 和编码耗时。

AVIF 固定使用 AOM，HEIF 固定使用 x265，避免不同默认编码器导致线程/速度参数失效。无损 `setting=0–100` 分别映射到 AOM `speed=0–8` 和 x265 `veryslow–ultrafast`；有损模式仍将 `setting` 作为质量，采用 AOM `speed=6` 或 x265 `medium`。PNG 中间文件使用低压缩级别，不改变像素，以降低重复压缩开销。AVIF/HEIF 保留既有静态首帧输出行为。

若更重视同时服务两个用户，可测试 `WORKERS=1 MAX_CONCURRENT_PER_WORKER=2 ENCODER_THREADS=1 MAGICK_THREAD_LIMIT=1`。这只是 CPU 资源分配方式，不保证单张图片更快；不要在 2 vCPU 上恢复 12 个并行转换。

## Hugging Face Space：生成式 thin wrapper

`cloud/hfs/` 包含部署包装器源文件：`README.md`、`Dockerfile.template`、`.dockerignore`、`export_space_bundle.sh` 和 `smoke-test.sh`。导出时只会生成以下五个平铺文件：

```text
.dockerignore
BUILD_SOURCE.txt
Dockerfile
README.md
hfs-dev.toml
```

导出器拒绝非空输出目录，并将解析出的完整 40 字符 Git commit SHA 只注入到生成 `Dockerfile` 的 `SOURCE_COMMIT` 位置。Space Docker 构建会：

1. 克隆 `https://github.com/BlueSkyXN/ImageMagickAPI-HFS.git`；
2. 获取并 detached checkout 该精确 commit；
3. 断言 checkout 后的 `HEAD` 完全相等；
4. 仅从 source stage 复制 `requirements.txt`、应用、模板和静态资源，绝不从 Space context 复制产品文件。

本地检查导出物时可使用：

```bash
mkdir /tmp/imagemagickapi-hfs-space
./cloud/hfs/export_space_bundle.sh --output /tmp/imagemagickapi-hfs-space --commit HEAD
```

发布工作流 `.github/workflows/sync-to-hf-space.yml` 只能通过 `workflow_dispatch` 手动触发，且必须输入 `confirm=PUBLISH_WRAPPER`。candidate/production 由独立 manifest 固定目标，两个 Space 在上传前都必须已经是 private；production target 必须显式等于 canonical `BlueSkyXN/ImageMagickAPI-HFS`。production 上传紧前会重新 fetch `origin/main`，并要求 workflow ref 为 `refs/heads/main`、checkout `HEAD`、`GITHUB_SHA` 与最新 `origin/main` 完全相等；candidate 保留所选手动触发 ref 的既有语义。canonical target、private visibility、thin-wrapper tree 和 production main provenance 等 gate 全部先于首次 HF upload；workflow 不自动删除远端内容，写入后下载比对全部五个文件并回读完整 tree。旧全仓 Space 的清理必须走单独 owner approval。不要使用 token URL、`git push` 或 force-push 来部署 Space。

## 分层验证

```bash
./scripts/static-check.sh
```

静态检查以 AST 和 shell 语法解析运行，不导入应用且不写 Python bytecode；它还调用 `scripts/validate-hfs-contract.sh`，验证 HFS v2 TOML 精确语义、环境键分类、导出 allowlist、完整 SHA provenance、运行时约束、入口/健康检查、忽略边界和工作流安全边界。

`.github/workflows/hfs-verify.yml` 会在 Pull Request 和 `main` 上执行这些检查，导出 wrapper、构建 Docker 镜像、运行异步/并发回归测试、启动容器并运行格式 smoke test。它不使用 secrets。`cloud/hfs/smoke-test.sh` 仅用 Python 标准库生成一个小 PNG，等待 `/health` 后检查首页以及 WebP、AVIF 和 HEIF 的有损/无损转换，包括编码速度端点；验证 HTTP 状态、`Content-Type` 与 RIFF/WEBP 或 ISO-BMFF `ftyp`/兼容品牌。

安装 `requirements.txt` 后，可从仓库根目录运行 `python -B -m unittest discover -s tests -v`。这些测试不需要图像工具，覆盖编码参数、文件复制不阻塞、并发限制、总超时以及真实子进程取消/回收。

`tests/check_avif.py BASE_URL` 需要本地 `magick`、`heif-enc` 和 `heif-info`；所有输出均为 AVIF，覆盖 PNG/JPEG/WebP/HEIC 输入、有损与无损、速度 0/75/100、尺寸、AVIF 品牌、透明通道、RGB 矩阵和位深，并检查 8-bit RGB/RGBA 的无损像素回读。CI 在真实运行容器内执行。当前 16-bit PNG 输入会由 `heif-enc` 默认转换为 10-bit AVIF；这不等同于源位深完全无损，不承诺 HDR/ICC/EXIF 全链路保留。

`test_magick.py` 保留为历史手工测试脚本，未被自动化流程改写。

## 依赖

- Python 3.10+
- FastAPI、Uvicorn、Jinja2、python-multipart
- ImageMagick 7+
- `libheif-examples`

## 许可证

MIT，详见 [LICENSE](LICENSE)。
