import os
import sys
import time
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

from backend.config import (
    STORAGE, DATA, current_task, task_lock,
    _system_metrics_cache, _system_metrics_cache_time, THRESHOLD
)
from backend.dependencies import get_auth_token
from backend.db.connection import get_db, now_ist
from backend.db.tasks import get_queue_stats
from backend.db.settings import get_all_settings, set_setting, get_indexing_errors
from backend.services.scheduler import is_queue_worker_running

router = APIRouter(tags=["admin_settings"])

@router.get("/api/admin/system/metrics")
def admin_system_metrics(token: str = Depends(get_auth_token)):
    global _system_metrics_cache, _system_metrics_cache_time
    now_t = time.time()
    if _system_metrics_cache and (now_t - _system_metrics_cache_time < 5):
        return _system_metrics_cache

    import resource
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1, load5, load15 = (0.0, 0.0, 0.0)

    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_mb = round(usage.ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else usage.ru_maxrss / 1024, 1)

    # Disk usage on storage volume
    try:
        disk = shutil.disk_usage(STORAGE)
        disk_total_gb = round(disk.total / (1024**3), 1)
        disk_used_gb = round(disk.used / (1024**3), 1)
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_pct = round((disk.used / max(disk.total, 1)) * 100, 1)
    except Exception:
        disk_total_gb = disk_used_gb = disk_free_gb = disk_pct = 0.0

    db_size_mb = 0.0
    db_file = DATA / "magniwedding.db"
    if db_file.exists():
        db_size_mb = round(db_file.stat().st_size / (1024 * 1024), 2)

    stats = get_queue_stats()
    with task_lock:
        mem_task = dict(current_task)

    # Photo & face library metrics
    total_photos = 0
    total_faces = 0
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(id) FROM photos WHERE is_deleted = 0")
        total_photos = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(id) FROM face_detections")
        total_faces = c.fetchone()[0] or 0
        conn.close()
    except Exception:
        pass

    result = {
        "status": "healthy",
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "memory_rss_mb": max_rss_mb,
        "db_size_mb": db_size_mb,
        "disk": {
            "total_gb": disk_total_gb,
            "used_gb": disk_used_gb,
            "free_gb": disk_free_gb,
            "used_pct": disk_pct
        },
        "library_stats": {
            "total_photos": total_photos,
            "total_faces": total_faces
        },
        "active_worker": is_queue_worker_running(),
        "current_task_status": mem_task.get("status", "idle"),
        "queue_stats": stats,
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S")
    }
    _system_metrics_cache = result
    _system_metrics_cache_time = now_t
    return result


@router.get("/api/admin/storage")
def admin_storage_metrics(token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(id) as count, SUM(file_size_bytes) as total_vps_bytes FROM photos WHERE is_deleted = 0")
    row = cursor.fetchone()
    total_photos = row["count"] or 0
    vps_bytes = row["total_vps_bytes"] or 0
    conn.close()
    
    raw_drive_bytes = total_photos * 25 * 1024 * 1024 if total_photos > 0 else 500 * 1024 * 1024
    saved_bytes = max(0, raw_drive_bytes - vps_bytes)
    saved_percent = round((saved_bytes / raw_drive_bytes) * 100, 1) if raw_drive_bytes else 95.0
    
    return {
        "gdrive_capacity_tb": 5.0,
        "raw_drive_bytes": raw_drive_bytes,
        "vps_used_bytes": vps_bytes,
        "saved_bytes": saved_bytes,
        "saved_percent": saved_percent,
        "total_photos": total_photos,
        "compression_ratio": round(raw_drive_bytes / max(vps_bytes, 1), 1) if vps_bytes else 28.5
    }

# --- Analytics & Telemetry API Endpoints ---


@router.get("/api/admin/settings")
def admin_get_settings_endpoint(token: str = Depends(get_auth_token)):
    """Returns all system settings and configurable AI face-match thresholds."""
    settings = get_all_settings()
    return {"status": "ok", "settings": settings}


@router.post("/api/admin/settings")
def admin_update_settings_endpoint(payload: dict, token: str = Depends(get_auth_token)):
    """Updates configurable face-match thresholds and system settings."""
    allowed_keys = {
        "match_threshold_high",
        "match_threshold_medium",
        "match_threshold_min",
        "default_event_slug",
        "dedup_dhash_threshold",
        "max_shared_album_photos"
    }
    updated = {}
    for k, v in payload.items():
        if k in allowed_keys:
            if k == "max_shared_album_photos":
                try:
                    v_int = max(1, min(int(v), 500))
                    v = str(v_int)
                except Exception:
                    v = "100"
            set_setting(k, str(v))
            updated[k] = str(v)
            if k == "match_threshold_min":
                global THRESHOLD
                try:
                    THRESHOLD = float(v)
                except Exception:
                    pass
    return {"status": "ok", "updated": updated}


# ---------------------------------------------------------------------------
# Indexing Errors & Diagnostics
# ---------------------------------------------------------------------------

@router.get("/api/admin/indexing/errors")
def admin_get_indexing_errors_endpoint(
    event_slug: str = "brk-sir",
    limit: int = 100,
    token: str = Depends(get_auth_token)
):
    """Returns indexing error logs and corrupt photo diagnostics."""
    errors = get_indexing_errors(event_slug=event_slug, limit=limit)
    return {"status": "ok", "errors": errors}


# --- Static & Dynamic HTML Page Handlers ---

