#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ImageMagick 动态图像转换 API

本项目基于 FastAPI 和 ImageMagick，提供一个高性能的 RESTful API 服务。
它允许通过动态 URL 路径对上传的图像文件进行多种格式的（有损或无损）转换，
并支持动画图像（如 GIF, APNG, Animated WebP/AVIF）的处理。

主要端点:
- POST /convert/{target_format}/{mode}/{setting}
- GET /health
"""

from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    BackgroundTasks,
    Path,
    Form,
    Request
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import tempfile
import os
import shutil
import logging
import uuid
import imghdr
import signal
from importlib.metadata import version as dist_version
from typing import Literal

# --- 1. 应用配置 ---

# 配置日志记录器
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info(
    "Runtime versions: fastapi=%s starlette=%s jinja2=%s",
    dist_version("fastapi"),
    dist_version("starlette"),
    dist_version("jinja2"),
)

# 资源限制
MAX_FILE_SIZE_MB = 200  # 允许上传的最大文件大小 (MB)
TIMEOUT_SECONDS = 300   # 上传解析后排队、文件复制及转换的总时间预算 (秒)
TEMP_DIR = os.getenv("TEMP_DIR", tempfile.gettempdir())  # 临时文件存储目录，优先使用环境变量，否则使用系统临时目录

# 并发控制配置（防止资源过载）
MAX_CONCURRENT_CONVERSIONS = int(os.getenv("MAX_CONCURRENT_PER_WORKER", "1"))
ENCODER_THREADS = int(os.getenv("ENCODER_THREADS", "2"))
if MAX_CONCURRENT_CONVERSIONS < 1 or not 1 <= ENCODER_THREADS <= 64:
    raise ValueError("MAX_CONCURRENT_PER_WORKER must be positive and ENCODER_THREADS must be 1-64")
conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)
logger.info(
    "并发限制已启用: 每个worker最多 %s 个转换，编码器线程配置 %s",
    MAX_CONCURRENT_CONVERSIONS,
    ENCODER_THREADS,
)

# --- 2. API 参数类型定义 ---

# 定义 API 路径中允许的目标格式
TargetFormat = Literal["avif", "webp", "jpeg", "png", "gif", "heif"]

# 定义 API 路径中允许的转换模式
ConversionMode = Literal["lossless", "lossy"]

# --- 3. FastAPI 应用初始化 ---

app = FastAPI(
    title="Magick 动态图像转换器 (V4)",
    description="通过 Web 界面或 API 实现多种格式的(无)损图像转换，支持动图。提供现代化图形上传界面和灵活的 RESTful API。",
    version="4.0.0"
)

# 挂载静态文件目录（CSS、JS等）
app.mount("/static", StaticFiles(directory="static"), name="static")

# 配置模板引擎
templates = Jinja2Templates(directory="templates")

# 启动时确保临时目录存在
os.makedirs(TEMP_DIR, exist_ok=True)

# --- 4. 辅助函数 ---

async def get_upload_file_size(upload_file: UploadFile) -> int:
    """
    异步获取上传文件的大小（以字节为单位）。
    
    通过 seek 到文件末尾来测量大小，然后重置指针。
    (继承自 ocrmypdf-hfs 实践)

    Args:
        upload_file: FastAPI 的 UploadFile 对象。

    Returns:
        文件大小（字节）。
    """
    if upload_file.size is not None:
        return upload_file.size

    def measure_size():
        current_position = upload_file.file.tell()
        upload_file.file.seek(0, 2)
        size = upload_file.file.tell()
        upload_file.file.seek(current_position)
        return size

    return await asyncio.to_thread(measure_size)

async def validate_image_content(upload_file: UploadFile) -> bool:
    """
    验证上传文件是否为有效的图像文件（通过文件头魔数检测）。
    
    此函数通过检查文件头部的魔数（magic bytes）来验证文件的真实类型，
    防止恶意文件通过修改扩展名绕过验证。

    Args:
        upload_file: FastAPI 的 UploadFile 对象。

    Returns:
        True 如果文件是有效的图像，False 否则。
    """
    # 保存当前位置
    current_position = upload_file.file.tell()
    await upload_file.seek(0)

    # 读取文件头部用于检测
    file_header = await upload_file.read(32)
    await upload_file.seek(current_position)  # 恢复原始指针位置
    
    # 使用 imghdr 检测图像类型
    img_type = imghdr.what(None, h=file_header)
    
    # 支持的图像类型
    valid_types = {'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff'}
    
    if img_type in valid_types:
        return True
    
    # imghdr 不支持某些格式，需要手动检测魔数
    # AVIF/HEIF 检测 (ftyp box)
    if len(file_header) >= 12:
        # HEIF/AVIF 文件以 ftyp box 开头
        ftyp_offset = file_header[4:8]
        if ftyp_offset == b'ftyp':
            # 检查品牌类型
            brand = file_header[8:12]
            # 常见的 HEIF/AVIF 品牌
            heif_brands = [b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1', b'avif', b'avis']
            if brand in heif_brands:
                return True
    
    # WebP 可能 imghdr 检测不到的情况
    if len(file_header) >= 12:
        if file_header[:4] == b'RIFF' and file_header[8:12] == b'WEBP':
            return True
    
    return False

def cleanup_temp_dir(temp_dir: str):
    """
    在后台任务中安全地清理临时会话目录。
    (继承自 ocrmypdf-hfs 实践)

    Args:
        temp_dir: 要递归删除的目录路径。
    """
    try:
        if os.path.exists(temp_dir):
            logger.info(f"后台清理：正在删除临时目录: {temp_dir}")
            shutil.rmtree(temp_dir)
            logger.info(f"后台清理：已成功删除 {temp_dir}")
    except Exception as cleanup_error:
        logger.error(f"后台清理：删除 {temp_dir} 失败: {cleanup_error}", exc_info=True)

async def _save_upload(upload_file: UploadFile, input_path: str):
    def copy_file():
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer, length=1024 * 1024)

    copy_task = asyncio.create_task(asyncio.to_thread(copy_file))
    try:
        await asyncio.shield(copy_task)
    except asyncio.CancelledError:
        # A running copy thread cannot be cancelled; drain it before closing its file.
        await asyncio.shield(copy_task)
        raise


def _heif_encoder_command(target_format: str, mode: str, setting: int,
                          input_path: str, output_path: str) -> list[str]:
    command = ['heif-enc']
    if target_format == "avif":
        speed = round(setting * 8 / 100) if mode == "lossless" else 6
        command.extend(['--avif', '--encoder', 'aom',
                        '-p', f'threads={ENCODER_THREADS}', '-p', f'speed={speed}'])
    else:
        presets = ['veryslow', 'slower', 'slow', 'medium', 'fast',
                   'faster', 'veryfast', 'superfast', 'ultrafast']
        preset = presets[round(setting * (len(presets) - 1) / 100)] if mode == "lossless" else 'medium'
        command.extend(['--encoder', 'x265', '-p', f'preset={preset}',
                        '-p', f'x265:pools={ENCODER_THREADS}',
                        '-p', 'x265:frame-threads=1'])
    if mode == "lossless":
        command.append('--lossless')
    else:
        command.extend(['--quality', str(setting)])
    command.extend(['--output', output_path, input_path])
    return command


async def _stop_conversion_process(process, communication):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    await asyncio.shield(communication)


async def _run_conversion_command(command: list[str]):
    launch = asyncio.create_task(asyncio.subprocess.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    ))
    try:
        process = await asyncio.shield(launch)
    except asyncio.CancelledError:
        # Cancellation can arrive after the OS created the child but before launch returned.
        try:
            process = await asyncio.shield(launch)
        except OSError:
            raise asyncio.CancelledError
        await _stop_conversion_process(process, asyncio.create_task(process.communicate()))
        raise
    except OSError as exc:
        logger.error("无法启动图像转换进程: %s", exc)
        raise HTTPException(status_code=503, detail="Image conversion dependency is unavailable.") from exc

    communication = asyncio.create_task(process.communicate())
    try:
        _, stderr = await asyncio.shield(communication)
    except asyncio.CancelledError:
        await _stop_conversion_process(process, communication)
        raise

    if process.returncode != 0:
        logger.error("Image conversion command failed: %s", stderr.decode(errors="replace"))
        raise HTTPException(
            status_code=500,
            detail="Image conversion failed. Please check your input file and parameters.",
        )


async def _convert_with_limit(file: UploadFile, input_path: str, commands: list[list[str]]):
    loop = asyncio.get_running_loop()
    queued_at = loop.time()
    async with conversion_semaphore:
        logger.info("获取并发许可，排队耗时 %.3fs", loop.time() - queued_at)
        started_at = loop.time()
        await _save_upload(file, input_path)
        logger.info("文件保存成功，耗时 %.3fs", loop.time() - started_at)
        for command in commands:
            started_at = loop.time()
            logger.info("正在执行命令: %s", ' '.join(command))
            await _run_conversion_command(command)
            logger.info("%s 处理耗时 %.3fs", command[0], loop.time() - started_at)


# --- 5. API 端点 ---

@app.get("/", summary="上传界面")
async def root(request: Request):
    """
    返回用户友好的HTML上传表单页面。
    提供图形化界面进行图像转换，支持4套主题切换。
    """
    return templates.TemplateResponse(request=request, name="index.html")

async def _probe_dependency(name: str, probe_arg: str) -> dict:
    """Probe a required executable without relying on an external ``which`` command."""
    executable = shutil.which(name)
    if executable is None:
        return {"status": "missing", "path": None, "detail": "executable not found"}

    try:
        process = await asyncio.subprocess.create_subprocess_exec(
            executable,
            probe_arg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.warning("Unable to start dependency probe for %s: %s", name, exc)
        return {"status": "failed", "path": executable, "detail": "probe could not start"}

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        return {"status": "failed", "path": executable, "detail": "probe timed out"}

    if process.returncode != 0:
        return {"status": "failed", "path": executable, "detail": "probe exited unsuccessfully"}

    details = {"status": "available", "path": executable}
    output = stdout.decode(errors="replace").strip()
    if name == "magick":
        details["version"] = output.split("\n")[0]
    elif name == "heif-enc":
        details["version"] = output.split("\n")[0]
    return details


def _probe_temp_dir() -> dict:
    """Verify that conversion workers can create files in the configured temp directory."""
    try:
        if not os.path.isdir(TEMP_DIR):
            return {"status": "unavailable", "temp_dir": TEMP_DIR, "detail": "not a directory"}
        with tempfile.NamedTemporaryFile(dir=TEMP_DIR, prefix=".health-", delete=True):
            pass
        disk_info = os.statvfs(TEMP_DIR)
    except OSError as exc:
        logger.error("健康检查无法使用临时目录: %s", exc)
        return {"status": "unavailable", "temp_dir": TEMP_DIR, "detail": "not writable"}

    free_space_mb = (disk_info.f_bavail * disk_info.f_frsize) / (1024 * 1024)
    return {
        "status": "available",
        "free_mb": round(free_space_mb, 2),
        "temp_dir": TEMP_DIR,
    }


@app.get("/health", summary="服务健康检查")
async def health_check():
    """Report dependency failures as a non-2xx response before serving conversions."""
    dependencies = {
        "magick": await _probe_dependency("magick", "--version"),
        "heif_enc": await _probe_dependency("heif-enc", "--version"),
    }
    magick = dependencies["magick"]
    heif_enc = dependencies["heif_enc"]
    base_response = {
        "dependencies": dependencies,
        "imagemagick": magick.get("version", "Not available"),
        "avif_encoder": heif_enc.get("path") or "Not available (AVIF/HEIF conversion will fail)",
        "resource_limits": {
            "max_file_size_mb": MAX_FILE_SIZE_MB,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_concurrent_per_worker": MAX_CONCURRENT_CONVERSIONS,
            "encoder_threads": ENCODER_THREADS,
        },
    }

    if any(item["status"] != "available" for item in dependencies.values()):
        base_response["status"] = "unhealthy"
        return JSONResponse(status_code=503, content=base_response)

    temp_dir = _probe_temp_dir()
    if temp_dir["status"] != "available":
        base_response["status"] = "unhealthy"
        base_response["disk_space"] = temp_dir
        return JSONResponse(status_code=503, content=base_response)

    base_response["status"] = "healthy"
    base_response["disk_space"] = {
        "free_mb": temp_dir["free_mb"],
        "temp_dir": temp_dir["temp_dir"],
    }
    return base_response

async def _perform_conversion(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    target_format: str,
    mode: str,
    setting: int
) -> FileResponse:
    """
    核心图像转换逻辑（内部函数）。
    被多个端点复用以避免代码重复。

    Args:
        background_tasks: FastAPI后台任务对象
        file: 上传的图像文件
        target_format: 目标格式 (avif, webp, jpeg, png, gif, heif)
        mode: 转换模式 (lossy, lossless)
        setting: 质量/压缩参数 (0-100)

    Returns:
        FileResponse: 转换后的图像文件
    """
    logger.info(f"开始转换: {target_format}/{mode}/{setting} (文件: {file.filename})")

    # 初始化临时目录变量，确保 finally 块中可以安全访问
    temp_dir = None
    cleanup_scheduled = False

    # 预检查: AVIF/HEIF 格式需要 heif-enc 依赖。
    if target_format in ["avif", "heif"] and shutil.which("heif-enc") is None:
        raise HTTPException(
            status_code=503,
            detail="AVIF/HEIF encoding is not available. heif-enc encoder not found."
        )

    # 1. 验证文件扩展名
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    file_ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.heif', '.heic', '.bmp', '.tiff', '.tif'}
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {file_ext}. Allowed formats: {', '.join(allowed_extensions)}"
        )

    # 2. 验证文件内容（魔数检查，防止恶意文件）
    is_valid_image = await validate_image_content(file)
    if not is_valid_image:
        logger.warning(f"文件内容验证失败: {file.filename} - 文件头魔数不匹配图像格式")
        raise HTTPException(
            status_code=400,
            detail="Invalid image file content. The file does not appear to be a valid image."
        )

    # 3. 验证文件大小
    file_size_mb = await get_upload_file_size(file) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        logger.warning(f"文件过大: {file_size_mb:.2f}MB (最大: {MAX_FILE_SIZE_MB}MB)")
        raise HTTPException(
            status_code=400, 
            detail=f"File too large. Max size is {MAX_FILE_SIZE_MB}MB."
        )

    # 4. 创建唯一的临时工作目录
    session_id = str(uuid.uuid4())
    temp_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(temp_dir, exist_ok=True)

    _, file_extension = os.path.splitext(file.filename)
    input_path = os.path.join(temp_dir, f"input{file_extension}")
    output_path = os.path.join(temp_dir, f"output.{target_format}")

    logger.info(f"正在临时目录中处理: {temp_dir}")

    try:
        # 6. 动态构建转换命令。Debian 的 ImageMagick 包不一定编译了
        # HEIF coder；在这种环境里仅使用 output.avif/output.heif 后缀会
        # 静默写出 PNG。AVIF/HEIF 因此由已校验存在的 heif-enc 负责，
        # ImageMagick 只把输入规范化为 encoder 可读的 PNG。
        use_heif_encoder = target_format in ["avif", "heif"]
        cmd = ['magick', input_path]

        # 关键: 仅对动画格式使用 -coalesce 以优化性能
        # -coalesce 会合并所有帧，确保动图（GIF/WebP/AVIF）被正确处理
        # 检测可能是动画的格式
        animated_formats = ['.gif', '.webp', '.apng', '.png']
        if file_extension.lower() in animated_formats or target_format in ['gif', 'webp']:
            cmd.append('-coalesce')

        # --- 6a. 无损 (lossless) 模式逻辑 ---
        if mode == "lossless":
            # 'setting' (0-100) 代表压缩速度 (0=最佳/最慢, 100=最快/最差)
            
            if target_format == "webp":
                # WebP method (0-6), 6 是最慢/最佳
                # 映射: setting(0) -> method(6), setting(100) -> method(0)
                # 使用线性插值确保精确映射
                webp_method = round(6 - (setting / 100.0) * 6)
                # WebP 无损模式下 quality 应始终为 100
                cmd.extend(['-define', 'webp:lossless=true'])
                cmd.extend(['-define', f'webp:method={webp_method}'])
                cmd.extend(['-quality', '100'])

            elif target_format == "jpeg":
                # JPEG 几乎没有通用的无损模式，使用-quality 100作为最佳有损替代
                cmd.extend(['-quality', '100'])
                
            elif target_format == "png":
                # PNG 始终无损
                # 映射: setting(0) -> compression(9), setting(100) -> compression(0)
                png_compression = min(9, int((100 - setting) * 0.09))
                # Magick -quality 映射: 91=级别0, 100=级别9
                cmd.extend(['-quality', str(91 + png_compression)])
            
            elif target_format == "gif":
                # GIF 始终是基于调色板的无损
                # -layers optimize 用于优化动图帧
                cmd.extend(['-layers', 'optimize'])

        # --- 6b. 有损 (lossy) 模式逻辑 ---
        elif mode == "lossy":
            # 'setting' (0-100) 代表 质量 (0=最差, 100=最佳)
            quality = setting

            if target_format == "webp":
                cmd.extend(['-quality', str(quality)])
                cmd.extend(['-define', 'webp:method=4']) # 默认使用较快的速度
            
            elif target_format == "jpeg":
                cmd.extend(['-quality', str(quality)])
                
            elif target_format == "png":
                # PNG 本身无损，通过量化（减少颜色）模拟 "有损"
                # 映射: quality(100) -> 256色, quality(0) -> 2色
                colors = max(2, int(256 * (quality / 100.0)))
                cmd.extend(['-colors', str(colors), '+dither'])
            
            elif target_format == "gif":
                # GIF "有损" 通过减少调色板颜色实现
                colors = max(2, int(256 * (quality / 100.0)))
                cmd.extend(['-colors', str(colors), '+dither'])
                cmd.extend(['-layers', 'optimize'])


        # 7. 添加输出路径并完成命令构建
        if use_heif_encoder:
            encoder_input_path = os.path.join(temp_dir, "encoder-input.png")
            # heif-enc 只消费单张静态输入；明确选择第一帧，避免
            # ImageMagick 按未知 AVIF/HEIF coder 静默生成错误格式。
            commands = [
                ['magick', f'{input_path}[0]', '-define', 'png:compression-level=1', encoder_input_path],
                _heif_encoder_command(target_format, mode, setting, encoder_input_path, output_path),
            ]
        else:
            cmd.append(output_path)
            commands = [cmd]

        # 上传解析完成后，排队、文件复制和全部转换阶段共享同一时间预算。
        await asyncio.wait_for(
            _convert_with_limit(file, input_path, commands),
            timeout=TIMEOUT_SECONDS,
        )

        # 9. 检查命令执行结果
        if not os.path.exists(output_path):
            error_message = "转换命令成功执行，但未找到输出文件。"
            logger.error(error_message)
            raise HTTPException(status_code=500, detail="Conversion completed but output file not found.")

        # 10. 成功：准备并返回文件响应
        logger.info(f"转换成功。输出文件: '{output_path}'")
        
        original_filename_base = os.path.splitext(file.filename)[0]
        download_filename = f"{original_filename_base}.{target_format}"
        
        # 动态设置 MimeType
        media_type = f"image/{target_format}"
        if target_format == "heif":
            media_type = "image/heif" # HEIF 的 MimeType

        # 注册后台清理任务
        background_tasks.add_task(cleanup_temp_dir, temp_dir)
        cleanup_scheduled = True

        return FileResponse(
            path=output_path,
            media_type=media_type,
            filename=download_filename
        )

    except asyncio.TimeoutError:
        logger.error(f"转换任务超时 (>{TIMEOUT_SECONDS}s): {file.filename}")
        raise HTTPException(status_code=504, detail=f"Conversion timed out after {TIMEOUT_SECONDS} seconds.")
    except HTTPException as http_exc:
        # 重新抛出已知的 HTTP 异常
        raise http_exc
    except Exception as e:
        # 捕获所有其他意外错误
        logger.error(f"发生意外错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {str(e)}")
    finally:
        # 确保关闭上传的文件句柄
        await file.close()
        # 备用清理：仅当未注册后台任务时立即清理
        if temp_dir is not None and not cleanup_scheduled and os.path.exists(temp_dir):
            await asyncio.to_thread(cleanup_temp_dir, temp_dir)

@app.post("/", response_class=FileResponse, summary="简化上传转换")
async def upload_convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="要转换的图像文件"),
    target_format: str = Form("heif", description="目标格式"),
    mode: str = Form("lossless", description="转换模式"),
    setting: int = Form(75, ge=0, le=100, description="质量参数")
):
    """
    通过HTML表单上传并转换图像。

    这个端点接收表单数据（而非URL路径参数），适合从网页表单调用。
    内部调用与 /convert/{format}/{mode}/{setting} 相同的转换逻辑。

    - **file**: 图像文件
    - **target_format**: 目标格式 (avif, webp, jpeg, png, gif, heif)，默认 heif
    - **mode**: 转换模式 (lossy, lossless)，默认 lossless
    - **setting**: 质量/压缩参数 (0-100)，默认 75
    """
    # 验证参数
    valid_formats = ["avif", "webp", "jpeg", "png", "gif", "heif"]
    if target_format not in valid_formats:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid target_format: {target_format}. Must be one of {valid_formats}"
        )

    valid_modes = ["lossy", "lossless"]
    if mode not in valid_modes:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid mode: {mode}. Must be one of {valid_modes}"
        )

    if not (0 <= setting <= 100):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid setting: {setting}. Must be between 0 and 100"
        )

    logger.info(f"收到表单上传请求: {target_format}/{mode}/{setting} (文件: {file.filename})")

    # 调用核心转换逻辑
    return await _perform_conversion(
        background_tasks=background_tasks,
        file=file,
        target_format=target_format,
        mode=mode,
        setting=setting
    )

@app.post(
    "/convert/{target_format}/{mode}/{setting}",
    summary="动态转换图像 (支持动图)",
    response_class=FileResponse,
    responses={
        200: {"description": "转换成功，返回图像文件"},
        400: {"description": "请求无效（例如文件过大）"},
        422: {"description": "路径参数验证失败（例如格式不支持）"},
        500: {"description": "服务器内部转换失败"},
        504: {"description": "转换处理超时"}
    }
)
async def convert_image_dynamic(
    background_tasks: BackgroundTasks,
    target_format: TargetFormat,
    mode: ConversionMode,
    setting: int = Path(..., ge=0, le=100, description="质量(有损) 或 压缩速度(无损) (0-100)"),
    file: UploadFile = File(..., description="要转换的图像文件 (支持动图)")
):
    """
    通过动态 URL 路径接收图像文件，执行转换并返回结果。

    - **target_format**: 目标格式 (avif, webp, jpeg, png, gif, heif)
    - **mode**: 转换模式 (lossless, lossy)
    - **setting**: 模式设置 (0-100)
        - mode=lossy: 0=最差质量, 100=最佳质量
        - mode=lossless: 0=最慢/最佳压缩, 100=最快/最差压缩
    """
    logger.info(f"收到API转换请求: {target_format}/{mode}/{setting} (文件: {file.filename})")

    # 调用核心转换逻辑
    return await _perform_conversion(
        background_tasks=background_tasks,
        file=file,
        target_format=target_format,
        mode=mode,
        setting=setting
    )
