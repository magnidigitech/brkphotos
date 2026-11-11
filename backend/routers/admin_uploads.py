import os
import sys
import time
import shutil
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form, Request

from backend.config import (
    QUEUE_DIR, STORAGE, current_task, task_lock, log_buffer,
    task_cancel_event, append_log
)
from backend.dependencies import get_auth_token
from backend.db.connection import get_db, now_ist
from backend.db.tasks import (
    enqueue_upload_task, get_queue_tasks, get_queue_task_by_id,
    get_queue_task_logs, cancel_queue_task, retry_queue_task,
    clear_completed_queue_tasks, get_queue_stats,
    create_task_record, stop_running_tasks, get_latest_task,
    list_task_history, get_task_by_id
)
from backend.db.automations import (
    get_automations, get_automation_by_id, create_automation,
    update_automation, delete_automation, toggle_automation,
    get_automation_history, record_automation_history, advance_automation_schedule
)
from backend.services.indexing import run_indexing_task
from backend.services.scheduler import trigger_queue_worker, is_queue_worker_running

router = APIRouter(tags=["admin_uploads"])
active_process = None

@router.post("/api/admin/events/{slug}/upload")
@router.post("/api/admin/events/{slug}/upload-queue")
async def admin_upload_to_event(
    slug: str,
    files: list[UploadFile] = File(...),
    album_slug: str = Form("highlights"),
    action: str = Form("now"),
    auto_index: bool = Form(True),
    token: str = Depends(get_auth_token)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    if not event:
        cursor.execute("SELECT id, name FROM events WHERE slug = 'brk-sir' OR slug = 'wedding' LIMIT 1")
        event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "Event not found")
        
    eid = event["id"]
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND slug = ?", (eid, album_slug))
    arow = cursor.fetchone()
    album_name = arow["name"] if arow else album_slug
    conn.close()
    
    task_id = f"up_{secrets.token_hex(4)}"
    staged_dir = QUEUE_DIR / task_id
    staged_dir.mkdir(parents=True, exist_ok=True)
    
    file_count = 0
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".zip"}
    
    for f in files:
        fname = Path(f.filename or "upload").name
        ext = Path(fname).suffix.lower()
        if ext in valid_exts:
            content = await f.read()
            dest_file = staged_dir / fname
            with open(dest_file, "wb") as dst:
                dst.write(content)
            file_count += 1
            
    if file_count == 0:
        shutil.rmtree(staged_dir, ignore_errors=True)
        raise HTTPException(400, "No supported image or ZIP files provided.")
        
    task_name = f"Upload to {album_name} ({file_count} files)"
    enqueue_upload_task(
        task_id=task_id,
        task_name=task_name,
        task_type="direct_upload",
        event_slug=slug,
        target_album_slug=album_slug,
        target_album_name=album_name,
        source_info=f"{file_count} files",
        staged_dir=str(staged_dir),
        file_count=file_count
    )
    
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Upload task #{task_id} created with {file_count} files for '{album_name}'.")
    append_log(f"[{now_str}] Task #{task_id} added to queue.")
    
    with task_lock:
        is_running = current_task.get("status") == "running"
        
    trigger_queue_worker()
    
    if action == "now" and not is_running:
        msg = f"Upload #{task_id} started immediately."
        res_status = "started"
    else:
        msg = f"Task #{task_id} added to upload queue (FIFO order)."
        res_status = "queued"
        
    return {
        "status": res_status,
        "task_id": task_id,
        "file_count": file_count,
        "album": album_slug,
        "message": msg
    }

@router.post("/api/admin/events/{slug}/gdrive")
@router.post("/api/admin/events/{slug}/gdrive-queue")
def admin_event_gdrive(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(400, "Google Drive URL is required.")
        
    album_slug = payload.get("album_slug", "").strip() or "auto"
    allowed_exts = payload.get("allowed_extensions") or ["jpg", "jpeg", "png", "webp"]
    action = payload.get("action", "now")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    eid = event["id"] if event else 1
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND slug = ?", (eid, album_slug))
    arow = cursor.fetchone()
    album_name = arow["name"] if arow else album_slug
    conn.close()
    
    task_id = f"gd_{secrets.token_hex(4)}"
    task_name = f"Google Drive Ingest ({album_name})"
    enqueue_upload_task(
        task_id=task_id,
        task_name=task_name,
        task_type="gdrive_sync",
        event_slug=slug,
        target_album_slug=album_slug,
        target_album_name=album_name,
        source_info=url,
        staged_dir="",
        file_count=0
    )
    
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Google Drive task #{task_id} created for '{album_name}'.")
    append_log(f"[{now_str}] Task #{task_id} added to queue.")
    
    with task_lock:
        is_running = current_task.get("status") == "running"
        
    trigger_queue_worker()
    
    if action == "now" and not is_running:
        msg = f"Google Drive ingest #{task_id} started immediately."
        res_status = "started"
    else:
        msg = f"Google Drive task #{task_id} added to upload queue."
        res_status = "queued"
        
    return {
        "status": res_status,
        "task_id": task_id,
        "album": album_slug,
        "message": msg
    }

@router.get("/api/admin/queue")
def admin_get_queue(limit: int = 30, token: str = Depends(get_auth_token)):
    tasks = get_queue_tasks(limit=limit)
    stats = get_queue_stats()
    with task_lock:
        mem_task = dict(current_task)
    return {
        "tasks": tasks,
        "stats": stats,
        "current_task": mem_task,
        "is_worker_running": is_queue_worker_running()
    }

@router.get("/api/admin/queue/{task_id}")
def admin_get_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    task = get_queue_task_by_id(task_id)
    if not task:
        raise HTTPException(404, "Queue task not found")
    return {"task": task}

@router.get("/api/admin/queue/{task_id}/logs")
def admin_get_queue_task_logs(task_id: str, token: str = Depends(get_auth_token)):
    logs = get_queue_task_logs(task_id)
    if not logs:
        hist = get_task_by_id(task_id)
        if hist:
            return {"task_id": task_id, "task_name": hist.get("task_type", ""), "status": hist["status"], "current_step": hist["current_step"], "log_text": hist["log_text"]}
        raise HTTPException(404, "Task logs not found")
    return logs


@router.post("/api/admin/queue/{task_id}/cancel")
def admin_cancel_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    global active_process
    with task_lock:
        if current_task.get("task_id") == task_id and current_task.get("status") == "running":
            task_cancel_event.set()
            if active_process and active_process.poll() is None:
                try:
                    active_process.terminate()
                    time.sleep(0.3)
                    if active_process.poll() is None:
                        active_process.kill()
                except Exception:
                    pass
            active_process = None
            current_task["status"] = "cancelled"
            current_task["message"] = "Task stopped by admin."
            
    ok = cancel_queue_task(task_id)
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Task #{task_id} cancelled by administrator.")
    trigger_queue_worker()
    return {"status": "ok", "task_id": task_id, "cancelled": ok}

@router.post("/api/admin/queue/{task_id}/retry")
def admin_retry_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    ok = retry_queue_task(task_id)
    if not ok:
        raise HTTPException(400, "Task cannot be retried (must be failed or cancelled).")
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Task #{task_id} re-queued for retry by administrator.")
    trigger_queue_worker()
    return {"status": "ok", "task_id": task_id}

@router.post("/api/admin/queue/clear-completed")
def admin_clear_completed_queue(token: str = Depends(get_auth_token)):
    cleared = clear_completed_queue_tasks()
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Administrator cleared {cleared} completed/cancelled tasks from queue.")
    return {"status": "ok", "cleared_count": cleared}

@router.get("/api/admin/automations")
def admin_get_automations(event_slug: str = "brk-sir", token: str = Depends(get_auth_token)):
    autos = get_automations(event_slug)
    return {"automations": autos}

@router.post("/api/admin/automations")
def admin_create_automation_endpoint(payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Automation name is required.")
    gdrive_url = payload.get("gdrive_url", "").strip()
    if not gdrive_url:
        raise HTTPException(400, "Google Drive URL or Folder ID is required.")
    event_slug = payload.get("event_slug", "brk-sir").strip()
    target_album = payload.get("target_album_slug", "auto").strip() or "auto"
    formats = payload.get("formats", ["jpg", "png", "webp"])
    trigger_date = payload.get("trigger_date", "").strip() or now_ist().strftime("%Y-%m-%d")
    trigger_time = payload.get("trigger_time", "").strip() or now_ist().strftime("%H:%M")
    repeat = payload.get("repeat_interval", "none").strip()
    is_active = 1 if payload.get("is_active", True) else 0

    auto_id = create_automation(
        name=name,
        event_slug=event_slug,
        gdrive_url=gdrive_url,
        target_album_slug=target_album,
        formats_list=formats,
        trigger_date=trigger_date,
        trigger_time=trigger_time,
        repeat_interval=repeat,
        is_active=is_active
    )
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Configured automation '{name}' (Schedule: {trigger_date} {trigger_time}, Repeat: {repeat}).")
    return {"status": "ok", "id": auto_id, "automation": get_automation_by_id(auto_id)}

@router.put("/api/admin/automations/{auto_id}")
def admin_update_automation_endpoint(auto_id: int, payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    gdrive_url = payload.get("gdrive_url", "").strip()
    target_album = payload.get("target_album_slug", "auto").strip() or "auto"
    formats = payload.get("formats", ["jpg", "png", "webp"])
    trigger_date = payload.get("trigger_date", "").strip() or now_ist().strftime("%Y-%m-%d")
    trigger_time = payload.get("trigger_time", "").strip() or now_ist().strftime("%H:%M")
    repeat = payload.get("repeat_interval", "none").strip()
    is_active = 1 if payload.get("is_active", True) else 0

    ok = update_automation(
        auto_id=auto_id,
        name=name,
        gdrive_url=gdrive_url,
        target_album_slug=target_album,
        formats_list=formats,
        trigger_date=trigger_date,
        trigger_time=trigger_time,
        repeat_interval=repeat,
        is_active=is_active
    )
    if not ok:
        raise HTTPException(404, "Automation not found.")
    return {"status": "ok", "automation": get_automation_by_id(auto_id)}

@router.delete("/api/admin/automations/{auto_id}")
def admin_delete_automation_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    ok = delete_automation(auto_id)
    return {"status": "ok", "deleted": ok}

@router.post("/api/admin/automations/{auto_id}/toggle")
def admin_toggle_automation_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    ok = toggle_automation(auto_id)
    return {"status": "ok", "automation": get_automation_by_id(auto_id)}

@router.post("/api/admin/automations/{auto_id}/run-now")
def admin_run_automation_now_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    auto = get_automation_by_id(auto_id)
    if not auto:
        raise HTTPException(404, "Automation not found.")
    task_id = f"auto_{secrets.token_hex(4)}"
    enqueue_upload_task(
        task_id=task_id,
        task_name=f"🤖 {auto['name']} (Run Now)",
        task_type="gdrive_sync",
        event_slug=auto.get("event_slug", "brk-sir"),
        target_album_slug=auto.get("target_album_slug", "auto"),
        target_album_name="Auto Ingestion",
        source_info=auto.get("gdrive_url", ""),
        staged_dir="",
        file_count=0
    )
    record_automation_history(
        automation_id=auto_id,
        automation_name=auto["name"],
        task_id=task_id,
        status="queued",
        files_count=0,
        result_message=f"Manual Run Now executed at {now_ist().strftime('%Y-%m-%d %H:%M:%S')}. Added to Upload Queue as #{task_id}."
    )
    trigger_queue_worker()
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Manually triggered automation '{auto['name']}'. Task #{task_id} queued.")
    return {"status": "ok", "task_id": task_id}

@router.get("/api/admin/automations/history")
@router.get("/api/admin/automations/{auto_id}/history")
def admin_get_automation_history_endpoint(auto_id: Optional[int] = None, token: str = Depends(get_auth_token)):
    hist = get_automation_history(auto_id, limit=60)
    return {"history": hist}


@router.post("/api/admin/tasks/stop")
def admin_stop_task(token: str = Depends(get_auth_token)):
    global active_process
    task_cancel_event.set()
    if active_process and active_process.poll() is None:
        try:
            active_process.terminate()
            time.sleep(0.3)
            if active_process.poll() is None:
                active_process.kill()
        except Exception:
            pass
    active_process = None
    with task_lock:
        current_task["status"] = "cancelled"
        current_task["message"] = "Task stopped by admin."
        current_task["current_step"] = "Task stopped by admin."

    try:
        stop_running_tasks("Task stopped by administrator.")
    except Exception as e:
        print(f"Note on stop_running_tasks: {e}")

    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Active background task was stopped by administrator.")
    trigger_queue_worker()
    return {"status": "stopped", "message": "Background task successfully terminated."}

@router.post("/api/admin/events/{slug}/index")
def admin_event_index(slug: str, token: str = Depends(get_auth_token)):
    with task_lock:
        if current_task.get("status") == "running":
            raise HTTPException(409, "A task is already running.")
        current_task["status"] = "running"
    task_cancel_event.clear()
    threading.Thread(target=run_indexing_task, args=(slug,), daemon=True).start()
    return {"status": "started"}

@router.get("/api/admin/logs")
def admin_logs(token: str = Depends(get_auth_token)):
    log_file = STORAGE / "index.log"
    lines = []
    if log_file.exists():
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.strip().splitlines()[-100:]
        except Exception:
            lines = log_buffer[-100:]
    else:
        lines = log_buffer[-100:]
    return {"logs": lines, "task": current_task}

@router.get("/api/admin/tasks/current")
def admin_current_task(token: str = Depends(get_auth_token)):
    latest = get_latest_task()
    with task_lock:
        mem_task = dict(current_task)
    return {
        "memory_task": mem_task,
        "latest_task": latest,
        "is_running": mem_task.get("status") == "running" or (latest and latest.get("status") == "running")
    }

@router.get("/api/admin/tasks/history")
def admin_task_history(limit: int = 50, token: str = Depends(get_auth_token)):
    tasks = list_task_history(limit=limit)
    return {"tasks": tasks}

@router.get("/api/admin/tasks/{task_id}/logs")
def admin_task_logs(task_id: str, token: str = Depends(get_auth_token)):
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {"task": task}

