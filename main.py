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

import fastapi
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
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess
import asyncio
import tempfile
import os
import shutil
import logging
import uuid
from typing import Literal

# --- 1. 应用配置 ---

# 配置日志记录器
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 资源限制
MAX_FILE_SIZE_MB = 200  # 允许上传的最大文件大小 (MB)
TIMEOUT_SECONDS = 300   # Magick 进程执行的超时时间 (秒)
TEMP_DIR = os.getenv("TEMP_DIR", tempfile.gettempdir())  # 临时文件存储目录，优先使用环境变量，否则使用系统临时目录

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
    current_position = upload_file.file.tell()
    upload_file.file.seek(0, 2)  # 移动到文件末尾
    size = upload_file.file.tell()
    upload_file.file.seek(current_position)  # 恢复原始指针位置
    return size

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

# --- 5. API 端点 ---

@app.get("/", summary="上传界面")
async def root(request: Request):
    """
    返回用户友好的HTML上传表单页面。
    提供图形化界面进行图像转换，支持4套主题切换。
    """
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health", summary="服务健康检查")
async def health_check():
    """
    提供详细的API和服务依赖（ImageMagick, heif-enc）的健康状态。
    (继承自 imagemagickapi-hfs 实践)
    """
    try:
        # 检查 ImageMagick
        proc_magick = await asyncio.subprocess.create_subprocess_exec(
            'magick', '--version', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout_m, stderr_m = await proc_magick.communicate()
        magick_version = stdout_m.decode().split('\n')[0] if proc_magick.returncode == 0 else "Not available"
        
        # 检查 AVIF/HEIF 编码器
        proc_heif = await asyncio.subprocess.create_subprocess_exec(
            'which', 'heif-enc', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout_h, stderr_h = await proc_heif.communicate()
        heif_encoder_path = stdout_h.decode().strip() if proc_heif.returncode == 0 else "Not available (AVIF/HEIF conversion will fail)"

        # 检查磁盘空间
        disk_info = os.statvfs(TEMP_DIR)
        free_space_mb = (disk_info.f_bavail * disk_info.f_frsize) / (1024 * 1024)
        
        return {
            "status": "healthy",
            "imagemagick": magick_version,
            "avif_encoder": heif_encoder_path,
            "disk_space": {"free_mb": round(free_space_mb, 2), "temp_dir": TEMP_DIR},
            "resource_limits": {
                "max_file_size_mb": MAX_FILE_SIZE_MB,
                "timeout_seconds": TIMEOUT_SECONDS
            }
        }
    except Exception as e:
        logger.error(f"健康检查失败: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "unhealthy", "error": str(e)})

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

    # 预检查: AVIF/HEIF 格式需要 heif-enc 依赖
    if target_format in ["avif", "heif"]:
        try:
            proc_check = await asyncio.subprocess.create_subprocess_exec(
                'which', 'heif-enc',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc_check.communicate()
            if proc_check.returncode != 0:
                raise HTTPException(
                    status_code=503,
                    detail=f"AVIF/HEIF encoding is not available. heif-enc encoder not found."
                )
        except Exception as e:
            logger.error(f"依赖检查失败: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Unable to verify AVIF/HEIF encoder availability."
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

    # 2. 验证文件大小
    file_size_mb = await get_upload_file_size(file) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        logger.warning(f"文件过大: {file_size_mb:.2f}MB (最大: {MAX_FILE_SIZE_MB}MB)")
        raise HTTPException(
            status_code=400, 
            detail=f"File too large. Max size is {MAX_FILE_SIZE_MB}MB."
        )

    # 3. 创建唯一的临时工作目录
    session_id = str(uuid.uuid4())
    temp_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(temp_dir, exist_ok=True)

    _, file_extension = os.path.splitext(file.filename)
    input_path = os.path.join(temp_dir, f"input{file_extension}")
    output_path = os.path.join(temp_dir, f"output.{target_format}")

    logger.info(f"正在临时目录中处理: {temp_dir}")

    cleanup_scheduled = False
    try:
        # 4. 保存上传的文件到临时输入路径
        logger.info(f"正在保存上传的文件 '{file.filename}' 至 '{input_path}'")
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info("文件保存成功。")

        # 5. 动态构建 ImageMagick 命令行参数
        cmd = ['magick', input_path]

        # 关键: 仅对动画格式使用 -coalesce 以优化性能
        # -coalesce 会合并所有帧，确保动图（GIF/WebP/AVIF）被正确处理
        # 检测可能是动画的格式
        animated_formats = ['.gif', '.webp', '.apng', '.png']
        if file_extension.lower() in animated_formats or target_format in ['gif', 'webp']:
            cmd.append('-coalesce')

        # --- 5a. 无损 (lossless) 模式逻辑 ---
        if mode == "lossless":
            # 'setting' (0-100) 代表压缩速度 (0=最佳/最慢, 100=最快/最差)
            
            if target_format == "avif":
                # AVIF speed (0-10), 0 是最慢/最佳
                avif_speed = min(10, int(setting / 10.0))
                cmd.extend(['-define', 'avif:lossless=true'])
                cmd.extend(['-define', f'avif:speed={avif_speed}'])
            
            elif target_format == "heif":
                # HEIF speed (0-10), 0 是最慢/最佳
                heif_speed = min(10, int(setting / 10.0))
                cmd.extend(['-define', 'heif:lossless=true'])
                cmd.extend(['-define', f'heif:speed={heif_speed}'])

            elif target_format == "webp":
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
                pass # Magick 默认值适用于无损GIF

        # --- 5b. 有损 (lossy) 模式逻辑 ---
        elif mode == "lossy":
            # 'setting' (0-100) 代表 质量 (0=最差, 100=最佳)
            quality = setting

            if target_format == "avif":
                # AVIF cq-level (0-63), 0 是最佳
                # 映射: quality(100) -> cq(0) ; quality(0) -> cq(63)
                cq_level = max(0, min(63, int(63 * (1 - quality / 100.0))))
                cmd.extend(['-define', f'avif:cq-level={cq_level}'])
                cmd.extend(['-define', 'avif:speed=4']) # 默认使用较快的速度
            
            elif target_format == "heif":
                # HEIF (heif-enc) 使用 -quality (0-100) 进行有损压缩
                cmd.extend(['-quality', str(quality)])

            elif target_format == "webp":
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


        # 6. 添加输出路径并完成命令构建
        cmd.append(output_path)
        command_str = ' '.join(cmd)
        logger.info(f"正在执行命令: {command_str}")

        # 7. 异步执行 Magick 命令 (继承自 imagemagickapi-hfs 实践)
        process = await asyncio.subprocess.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=TIMEOUT_SECONDS
        )

        # 8. 检查命令执行结果
        if process.returncode != 0:
            error_message = f"Magick failed: {stderr.decode()[:1000]}"
            logger.error(error_message)
            raise HTTPException(status_code=500, detail=error_message)
        
        if not os.path.exists(output_path):
            error_message = "Magick 命令成功执行，但未找到输出文件。"
            logger.error(error_message)
            raise HTTPException(status_code=500, detail=error_message)

        # 9. 成功：准备并返回文件响应
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
        logger.error(f"Magick 处理超时 (>{TIMEOUT_SECONDS}s): {file.filename}")
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
        if not cleanup_scheduled and os.path.exists(temp_dir):
            cleanup_temp_dir(temp_dir)

@app.post("/", response_class=FileResponse, summary="简化上传转换")
async def upload_convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="要转换的图像文件"),
    target_format: str = Form("heif", description="目标格式"),
    mode: str = Form("lossless", description="转换模式"),
    setting: int = Form(0, ge=0, le=100, description="质量参数")
):
    """
    通过HTML表单上传并转换图像。

    这个端点接收表单数据（而非URL路径参数），适合从网页表单调用。
    内部调用与 /convert/{format}/{mode}/{setting} 相同的转换逻辑。

    - **file**: 图像文件
    - **target_format**: 目标格式 (avif, webp, jpeg, png, gif, heif)，默认 webp
    - **mode**: 转换模式 (lossy, lossless)，默认 lossy
    - **setting**: 质量/压缩参数 (0-100)，默认 80
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