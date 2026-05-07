"""
JMComic 下载器 API 服务

启动:
  python usage/api_server.py

接口:
  GET   /                 - 前端页面
  POST  /download         - 提交下载任务 {"id": "438516", "type": "album", "browser": true}
  GET   /status/{id}      - 查询任务状态
  GET   /tasks            - 查看所有任务列表
  GET   /download/{task_id}  - 下载 PDF (浏览器模式用完即删)

环境变量:
  JM_API_OPTION         - option 配置文件路径 (默认 assets/option/option_api_server.yml)
  JM_API_MAX_WORKERS    - 最大并发下载数 (默认 3)
  JM_API_PORT           - 端口 (默认 7000)
  JM_API_SAVE_DIR       - 服务器保存模式的文件路径 (默认 ~/jmcomic_downloads)
"""

import uuid
import threading
import os
import shutil
import logging
import re
import time
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jmcomic-api")

app = FastAPI(title="JMComic Downloader")

# ---------------------------------------------------------------------------
# 并发控制
# ---------------------------------------------------------------------------
_MAX_WORKERS = int(os.getenv('JM_API_MAX_WORKERS', '3'))
_semaphore = threading.Semaphore(_MAX_WORKERS)

# ---------------------------------------------------------------------------
# 任务存储
# ---------------------------------------------------------------------------
_tasks: dict[str, dict] = {}
_running_index: dict[str, str] = {}
_tasks_lock = threading.Lock()

# Option 配置文件
_OPTION_FILE = os.getenv('JM_API_OPTION', None)
_DEFAULT_OPTION_FILE = str(
    Path(__file__).resolve().parent.parent / "assets" / "option" / "option_api_server.yml"
)

# 临时下载目录
_TEMP_DIR = Path(__file__).parent / "_temp_downloads"
_TEMP_DIR.mkdir(exist_ok=True)

# 服务器保存目录（可通过环境变量覆盖）
_SAVE_DIR = Path(os.getenv('JM_API_SAVE_DIR', str(Path.home() / 'jmcomic_downloads')))
_SAVE_DIR.mkdir(parents=True, exist_ok=True)
log.info(f"服务器保存目录: {_SAVE_DIR.resolve()}")

# PDF 保留时间（秒），超时后自动清理
_PDF_TTL = 1800  # 30 分钟
_START_TIME = datetime.now()


def _report_status():
    """每 5 分钟打印一次服务状态"""
    while True:
        time.sleep(300)
        uptime = datetime.now() - _START_TIME
        with _tasks_lock:
            total = len(_tasks)
            running = sum(1 for t in _tasks.values() if t["status"] in ("running", "downloading", "converting"))
            completed = sum(1 for t in _tasks.values() if t["status"] == "completed")
            failed = sum(1 for t in _tasks.values() if t["status"] == "failed")
            workers = _semaphore._value
        log.info(
            f"[心跳] 运行 {uptime.days}d {uptime.seconds // 3600}h "
            f"| 总任务 {total} | 运行中 {running} | 已完成 {completed} | 失败 {failed} | 可用并发 {workers}"
        )


def _cleanup_old_pdfs():
    """定期清理过期的临时 PDF 文件"""
    while True:
        time.sleep(300)  # 每 5 分钟检查一次
        now = datetime.now().timestamp()
        for f in _TEMP_DIR.iterdir():
            if f.suffix == '.pdf' and (now - f.stat().st_mtime) > _PDF_TTL:
                try:
                    f.unlink(missing_ok=True)
                    log.info(f"清理过期 PDF: {f.name}")
                except Exception:
                    pass


# 启动后台线程：清理过期 PDF 和心跳报告
_cleanup_thread = threading.Thread(target=_cleanup_old_pdfs, daemon=True)
_cleanup_thread.start()
_heartbeat_thread = threading.Thread(target=_report_status, daemon=True)
_heartbeat_thread.start()

# 启动时清理上一次残留的临时文件
shutil.rmtree(_TEMP_DIR, ignore_errors=True)
_TEMP_DIR.mkdir(exist_ok=True)
log.info("已清理上一次残留的临时文件")


class DownloadRequest(BaseModel):
    id: str
    type: str = "album"  # "album" 或 "photo"
    browser: bool = True  # True=PDF 到浏览器，False=存到服务器硬盘


def _get_option():
    from jmcomic import create_option_by_file
    file = _OPTION_FILE or _DEFAULT_OPTION_FILE
    log.info(f"使用配置文件: {file}")
    return create_option_by_file(file)


def _sanitize_filename(name: str) -> str:
    """移除文件名中的非法字符"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name[:80]


def _images_to_pdf(image_dir: Path, pdf_path: str) -> str:
    """把目录下所有图片按文件名排序合并为一个 PDF"""
    import img2pdf

    suffixes = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    img_files = sorted(
        [str(p) for p in image_dir.rglob('*') if p.suffix.lower() in suffixes]
    )

    if not img_files:
        raise Exception("没有找到图片文件")

    log.info(f"  共 {len(img_files)} 张图片，合并为 PDF ...")
    with open(pdf_path, 'wb') as f:
        f.write(img2pdf.convert(img_files))

    size_mb = Path(pdf_path).stat().st_size / 1024 / 1024
    log.info(f"  PDF 生成完成 ({size_mb:.1f} MB)")
    return pdf_path


def _download_task(task_id: str, jm_id: str, download_type: str, browser: bool):
    """后台下载任务"""
    start_time = datetime.now()
    log.info(f"[{task_id[:8]}] 任务开始: {'浏览器模式' if browser else '服务器模式'}, ID={jm_id}")

    try:
        with _semaphore:
            from jmcomic import download_album, download_photo, disable_jm_log
            disable_jm_log()

            # 1. 下载到临时目录
            temp_dir = _TEMP_DIR / task_id
            temp_dir.mkdir(parents=True, exist_ok=True)

            option = _get_option()
            option.dir_rule.base_dir = str(temp_dir)
            option.dir_rule.rule_dsl = 'Bd_Ptitle'
            option.client.retry_times = 2  # 不存在 ID 快速失败

            with _tasks_lock:
                _tasks[task_id]["status"] = "downloading"

            log.info(f"[{task_id[:8]}] 正在下载 ...")
            if download_type == "album":
                detail, downloader = download_album(jm_id, option)
                pdf_title = f"jm_{detail.album_id}_{_sanitize_filename(detail.name)}"
                log.info(f"[{task_id[:8]}] 下载完成: {detail.name} ({detail.page_count} 页)")
            else:
                detail, downloader = download_photo(jm_id, option)
                pdf_title = f"jm_{detail.photo_id}_{_sanitize_filename(detail.name)}"
                log.info(f"[{task_id[:8]}] 下载完成: {detail.name}")

            # 检查是否有图片下载失败
            failed_count = len(downloader.download_failed_image)
            if failed_count > 0:
                log.warning(f"[{task_id[:8]}] 有 {failed_count} 张图片下载失败，将纳入日志")
                for img, exc in downloader.download_failed_image[:5]:
                    log.warning(f"  图片失败: {img.download_url} -> {exc}")
                if failed_count > 5:
                    log.warning(f"  ... 共 {failed_count} 张失败（仅显示前5张）")

            # 2. 合成为 PDF
            with _tasks_lock:
                _tasks[task_id]["status"] = "converting"

            log.info(f"[{task_id[:8]}] 正在合并为 PDF ...")
            pdf_path = str(_TEMP_DIR / f"{task_id}.pdf")
            _images_to_pdf(temp_dir, pdf_path)

            shutil.rmtree(temp_dir, ignore_errors=True)
            file_size = Path(pdf_path).stat().st_size

            if browser:
                # === 浏览器模式：留待用户下载 ===
                with _tasks_lock:
                    _tasks[task_id] = {
                        "status": "completed",
                        "type": download_type,
                        "id": jm_id,
                        "download_url": f"/download/{task_id}",
                        "file_size": file_size,
                        "title": pdf_title,
                    }
                log.info(f"[{task_id[:8]}] 任务完成，等待用户下载: {pdf_title}.pdf")
            else:
                # === 服务器模式：移动到保存目录 ===
                save_path = _SAVE_DIR / f"{pdf_title}.pdf"

                counter = 1
                while save_path.exists():
                    save_path = _SAVE_DIR / f"{pdf_title}_{counter}.pdf"
                    counter += 1

                shutil.move(pdf_path, save_path)

                with _tasks_lock:
                    _tasks[task_id] = {
                        "status": "completed",
                        "type": download_type,
                        "id": jm_id,
                        "saved_path": str(save_path),
                        "file_size": file_size,
                        "title": pdf_title,
                    }
                log.info(f"[{task_id[:8]}] 任务完成，PDF 已保存: {save_path}")

    except Exception as e:
        log.error(f"[{task_id[:8]}] 任务失败: {e}")
        with _tasks_lock:
            _tasks[task_id] = {
                "status": "failed",
                "type": download_type,
                "id": jm_id,
                "error": str(e),
            }
    finally:
        shutil.rmtree(_TEMP_DIR / task_id, ignore_errors=True)
        with _tasks_lock:
            if _running_index.get(jm_id) == task_id:
                del _running_index[jm_id]

        elapsed = (datetime.now() - start_time).total_seconds()
        log.info(f"[{task_id[:8]}] 耗时: {elapsed:.0f} 秒")


def _submit(jm_id: str, download_type: str, browser: bool) -> tuple[str, bool]:
    with _tasks_lock:
        existing = _running_index.get(jm_id)
        if existing and _tasks.get(existing, {}).get("status") in ("running", "downloading", "converting"):
            return existing, False

        task_id = str(uuid.uuid4())
        _tasks[task_id] = {
            "status": "running",
            "type": download_type,
            "id": jm_id,
            "browser": browser,
        }
        _running_index[jm_id] = task_id

    thread = threading.Thread(
        target=_download_task,
        args=(task_id, jm_id, download_type, browser),
        daemon=True,
    )
    thread.start()
    return task_id, True


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.post("/download")
async def download(req: DownloadRequest):
    task_id, is_new = _submit(req.id.strip(), req.type, req.browser)
    if is_new:
        log.info(f"收到下载请求: ID={req.id}, 浏览器模式={req.browser}")
        return {"task_id": task_id, "status": "started", "id": req.id, "browser": req.browser}
    return {"task_id": task_id, "status": "already_running", "id": req.id, "browser": req.browser}


@app.get("/status/{task_id_or_jm_id}")
async def get_status(task_id_or_jm_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id_or_jm_id)
    if task:
        return {"id": task_id_or_jm_id, **task}

    with _tasks_lock:
        for tid, info in reversed(list(_tasks.items())):
            if info["id"] == task_id_or_jm_id:
                return {"id": task_id_or_jm_id, **info}

    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/download/{task_id}")
async def download_file(task_id: str):
    """下载 PDF，下载后自动删除"""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="Task is not completed yet")

    pdf_path = _TEMP_DIR / f"{task_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="File not found or already expired")

    title = task.get("title", f"jm_{task['id']}")
    filename = f"{title}.pdf"

    # 同一任务只打一次下载日志，防止刷屏
    if not task.get('_downloaded'):
        task['_downloaded'] = True
        log.info(f"用户下载 PDF: {filename}")
    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )


@app.get("/tasks")
async def list_tasks():
    with _tasks_lock:
        return dict(_tasks)


@app.get("/workers")
async def worker_info():
    return {
        "max_workers": _MAX_WORKERS,
        "running": sum(1 for t in _tasks.values() if t["status"] in ("running", "downloading", "converting")),
        "available": _semaphore._value,
    }


@app.get("/health")
async def health():
    uptime = datetime.now() - _START_TIME
    with _tasks_lock:
        total = len(_tasks)
        running = sum(1 for t in _tasks.values() if t["status"] in ("running", "downloading", "converting"))
        completed = sum(1 for t in _tasks.values() if t["status"] == "completed")
        failed = sum(1 for t in _tasks.values() if t["status"] == "failed")
    return {
        "status": "ok",
        "uptime_seconds": int(uptime.total_seconds()),
        "uptime_human": f"{uptime.days}d {uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m",
        "tasks": {"total": total, "running": running, "completed": completed, "failed": failed},
        "workers": {"max": _MAX_WORKERS, "available": _semaphore._value},
    }


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('JM_API_PORT', '7000'))
    log.info(f"服务启动: http://0.0.0.0:{port}")
    log.info(f"最大并发下载数: {_MAX_WORKERS}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
