"""
BRK Photos - High-Performance Analytics, Telemetry & Reporting Engine.
Non-blocking, privacy-preserving, asynchronous event ingestion with SQLite WAL persistence.
"""
import os
import sys
import time
import json
import uuid
import queue
import shutil
import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from database import get_db, now_ist, PHOTOS, DATA_DIR, STORAGE, DB_PATH

logger = logging.getLogger("brk_analytics")
logger.setLevel(logging.INFO)

# IST Timezone Helper
IST = timezone(timedelta(hours=5, minutes=30))

# Bounded queue for non-blocking event recording
# Drops incoming events gracefully if backpressure exceeds 10,000 items
_event_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10000)
_active_zip_jobs: Dict[str, Dict[str, Any]] = {}
_active_zip_lock = threading.Lock()

# In-memory short-lived cache for aggregated dashboard summaries (30 seconds TTL)
_analytics_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_analytics_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 30.0

# System metrics short-lived cache (15 seconds TTL)
_system_metrics_cache: Tuple[float, Dict[str, Any]] = (0.0, {})
_system_metrics_lock = threading.Lock()
_SYSTEM_METRICS_TTL = 15.0


def hash_ip(ip_str: Optional[str]) -> str:
    """Creates a 1-way non-reversible SHA256 prefix hash of IP for bot filtering without storing PII."""
    if not ip_str:
        return ""
    salt = "brk_photos_privacy_salt_2026"
    return hashlib.sha256(f"{salt}_{ip_str}".encode("utf-8")).hexdigest()[:16]


def parse_date_window(range_type: str = "7d", from_date_str: Optional[str] = None, to_date_str: Optional[str] = None) -> Tuple[datetime, datetime, str]:
    """
    Parses start and end timestamps in IST for a given date filter.
    Returns (start_dt, end_dt, window_label).
    """
    now = now_ist()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    range_type = (range_type or "7d").lower()
    if range_type == "today":
        return today_midnight, now, "Today"
    elif range_type == "yesterday":
        y_start = today_midnight - timedelta(days=1)
        y_end = today_midnight - timedelta(microseconds=1)
        return y_start, y_end, "Yesterday"
    elif range_type == "30d":
        start = today_midnight - timedelta(days=29)
        return start, now, "Last 30 Days"
    elif range_type == "month":
        start = today_midnight.replace(day=1)
        return start, now, "This Month"
    elif range_type == "custom" and from_date_str:
        try:
            f_parts = [int(p) for p in from_date_str.split("-")]
            start = datetime(f_parts[0], f_parts[1], f_parts[2], 0, 0, 0, tzinfo=IST)
            if to_date_str:
                t_parts = [int(p) for p in to_date_str.split("-")]
                end = datetime(t_parts[0], t_parts[1], t_parts[2], 23, 59, 59, tzinfo=IST)
            else:
                end = now
            return start, end, f"{from_date_str} to {to_date_str or 'Now'}"
        except Exception:
            pass

    # Default: Last 7 days
    start = today_midnight - timedelta(days=6)
    return start, now, "Last 7 Days"


# ==============================================================================
# Asynchronous Background Worker
# ==============================================================================

def _analytics_worker_loop():
    """Background daemon thread that writes queued analytics events in batches."""
    batch: List[Dict[str, Any]] = []
    last_flush = time.time()

    while True:
        try:
            try:
                # Wait up to 0.5s for next event
                evt = _event_queue.get(timeout=0.5)
                batch.append(evt)
            except queue.Empty:
                pass

            now = time.time()
            if batch and (len(batch) >= 50 or (now - last_flush) >= 0.5):
                _flush_event_batch(batch)
                batch.clear()
                last_flush = now
        except Exception as e:
            logger.error(f"Error in analytics worker loop: {e}", exc_info=False)
            time.sleep(1.0)


def _flush_event_batch(batch: List[Dict[str, Any]]):
    """Executes single SQLite transaction for all accumulated events."""
    if not batch:
        return
    try:
        conn = get_db()
        cursor = conn.cursor()
        rows_to_insert = []
        for e in batch:
            rows_to_insert.append((
                e.get("event_id") or str(uuid.uuid4()),
                e.get("event_type", "unknown"),
                e.get("session_id", "anon"),
                e.get("visitor_id") or "",
                e.get("event_slug", "brk-sir"),
                e.get("search_id") or "",
                e.get("photo_id"),
                e.get("photo_path") or "",
                e.get("album_name") or "",
                e.get("device_type") or "desktop",
                float(e.get("duration_ms") or 0.0),
                int(e.get("matches_count") or 0),
                e.get("status") or "success",
                e.get("error_category") or "",
                json.dumps(e.get("metadata") or {}),
                e.get("ip_hash") or "",
                e.get("created_at") or now_ist().strftime("%Y-%m-%d %H:%M:%S")
            ))

        cursor.executemany("""
            INSERT OR IGNORE INTO analytics_events (
                event_id, event_type, session_id, visitor_id, event_slug, search_id,
                photo_id, photo_path, album_name, device_type, duration_ms, matches_count,
                status, error_category, metadata_json, ip_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()
        conn.close()
    except Exception as err:
        logger.error(f"Failed to flush analytics batch ({len(batch)} items): {err}")


# Start worker thread on module load
_worker_thread = threading.Thread(target=_analytics_worker_loop, daemon=True, name="brk-analytics-worker")
_worker_thread.start()


# ==============================================================================
# Public Event Ingestion APIs (Non-blocking)
# ==============================================================================

def record_event_async(
    event_type: str,
    session_id: Optional[str] = None,
    visitor_id: Optional[str] = None,
    event_slug: str = "brk-sir",
    search_id: Optional[str] = None,
    photo_id: Optional[int] = None,
    photo_path: Optional[str] = None,
    album_name: Optional[str] = None,
    device_type: Optional[str] = None,
    duration_ms: float = 0.0,
    matches_count: int = 0,
    status: str = "success",
    error_category: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_hash: Optional[str] = None,
    event_id: Optional[str] = None
) -> bool:
    """Non-blocking queue put for high-frequency portal and API events."""
    try:
        payload = {
            "event_id": event_id or str(uuid.uuid4()),
            "event_type": event_type,
            "session_id": session_id or "anon",
            "visitor_id": visitor_id or "",
            "event_slug": event_slug or "brk-sir",
            "search_id": search_id or "",
            "photo_id": photo_id,
            "photo_path": photo_path or "",
            "album_name": album_name or "",
            "device_type": device_type or "desktop",
            "duration_ms": duration_ms,
            "matches_count": matches_count,
            "status": status,
            "error_category": error_category or "",
            "metadata": metadata or {},
            "ip_hash": ip_hash or "",
            "created_at": now_ist().strftime("%Y-%m-%d %H:%M:%S")
        }
        _event_queue.put_nowait(payload)
        return True
    except queue.Full:
        logger.warning(f"Analytics queue full (size={_event_queue.qsize()}). Dropping event {event_type}.")
        return False
    except Exception as e:
        logger.error(f"Error queueing analytics event: {e}")
        return False


def record_portal_visit(session_id: str, visitor_id: Optional[str] = None, device_type: str = "desktop", ip: Optional[str] = None, event_slug: str = "brk-sir"):
    record_event_async(
        event_type="portal_visit",
        session_id=session_id,
        visitor_id=visitor_id,
        device_type=device_type,
        ip_hash=hash_ip(ip),
        event_slug=event_slug
    )


def record_search_started(search_id: str, session_id: str, visitor_id: Optional[str] = None, device_type: str = "desktop", event_slug: str = "brk-sir"):
    record_event_async(
        event_type="search_started",
        search_id=search_id,
        session_id=session_id,
        visitor_id=visitor_id,
        device_type=device_type,
        event_slug=event_slug
    )


def record_search_completed(search_id: str, session_id: str, matches_count: int, duration_ms: float, visitor_id: Optional[str] = None, event_slug: str = "brk-sir"):
    record_event_async(
        event_type="search_completed",
        search_id=search_id,
        session_id=session_id,
        visitor_id=visitor_id,
        matches_count=matches_count,
        duration_ms=duration_ms,
        status="success",
        event_slug=event_slug
    )


def record_search_no_results(search_id: str, session_id: str, duration_ms: float, visitor_id: Optional[str] = None, event_slug: str = "brk-sir"):
    record_event_async(
        event_type="search_no_results",
        search_id=search_id,
        session_id=session_id,
        visitor_id=visitor_id,
        matches_count=0,
        duration_ms=duration_ms,
        status="zero_results",
        event_slug=event_slug
    )


def record_search_failed(search_id: str, session_id: str, error_category: str, duration_ms: float = 0.0, visitor_id: Optional[str] = None, event_slug: str = "brk-sir"):
    record_event_async(
        event_type="search_failed",
        search_id=search_id,
        session_id=session_id,
        visitor_id=visitor_id,
        status="failure",
        error_category=error_category,
        duration_ms=duration_ms,
        event_slug=event_slug
    )


def record_photo_view(photo_path: str, session_id: str, visitor_id: Optional[str] = None, album_name: Optional[str] = None, device_type: str = "desktop"):
    """Records a genuine user photo view (opened in full-screen lightbox for >= 1s)."""
    record_event_async(
        event_type="photo_opened",
        photo_path=photo_path,
        album_name=album_name,
        session_id=session_id,
        visitor_id=visitor_id,
        device_type=device_type
    )


def record_photo_download(photo_path: str, quality: str = "4k", session_id: Optional[str] = None, visitor_id: Optional[str] = None, file_size_bytes: int = 0):
    record_event_async(
        event_type="photo_downloaded",
        photo_path=photo_path,
        session_id=session_id or "anon",
        visitor_id=visitor_id,
        metadata={"quality": quality, "bytes": file_size_bytes}
    )


def record_share_click(photo_path: str, session_id: Optional[str] = None, visitor_id: Optional[str] = None):
    record_event_async(
        event_type="share_clicked",
        photo_path=photo_path,
        session_id=session_id or "anon",
        visitor_id=visitor_id
    )


# ==============================================================================
# Download Jobs Tracking
# ==============================================================================

def start_download_job(job_id: str, job_type: str, session_id: str, visitor_id: Optional[str] = None, photo_count: int = 0, event_slug: str = "brk-sir"):
    """Registers an active download job in memory and database."""
    now_ts = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with _active_zip_lock:
        _active_zip_jobs[job_id] = {
            "job_id": job_id,
            "job_type": job_type,
            "session_id": session_id,
            "photo_count": photo_count,
            "started_at": time.time(),
            "started_at_str": now_ts
        }

    def _db_insert():
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO download_jobs (
                    job_id, job_type, session_id, visitor_id, event_slug, photo_count, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'processing', ?)
            """, (job_id, job_type, session_id, visitor_id, event_slug, photo_count, now_ts))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error registering download job in DB: {e}")

    threading.Thread(target=_db_insert, daemon=True).start()
    record_event_async("zip_started", session_id=session_id, visitor_id=visitor_id, metadata={"job_id": job_id, "photo_count": photo_count})


def finish_download_job(job_id: str, zip_size_bytes: int, photo_count: Optional[int] = None):
    """Marks a download job as successfully completed."""
    duration = 0.0
    sess_id = "anon"
    with _active_zip_lock:
        item = _active_zip_jobs.pop(job_id, None)
        if item:
            duration = round(time.time() - item["started_at"], 2)
            sess_id = item["session_id"]
            if photo_count is None:
                photo_count = item["photo_count"]

    completed_at = now_ist().strftime("%Y-%m-%d %H:%M:%S")

    def _db_update():
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE download_jobs
                SET status = 'completed', zip_size_bytes = ?, duration_seconds = ?,
                    photo_count = COALESCE(?, photo_count), completed_at = ?
                WHERE job_id = ?
            """, (zip_size_bytes, duration, photo_count, completed_at, job_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error completing download job in DB: {e}")

    threading.Thread(target=_db_update, daemon=True).start()
    record_event_async(
        "zip_completed",
        session_id=sess_id,
        metadata={"job_id": job_id, "size_bytes": zip_size_bytes, "photo_count": photo_count, "duration_s": duration}
    )


def fail_download_job(job_id: str, error_message: str):
    """Marks a download job as failed."""
    duration = 0.0
    sess_id = "anon"
    with _active_zip_lock:
        item = _active_zip_jobs.pop(job_id, None)
        if item:
            duration = round(time.time() - item["started_at"], 2)
            sess_id = item["session_id"]

    completed_at = now_ist().strftime("%Y-%m-%d %H:%M:%S")

    def _db_update():
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE download_jobs
                SET status = 'failed', error_message = ?, duration_seconds = ?, completed_at = ?
                WHERE job_id = ?
            """, (error_message, duration, completed_at, job_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error failing download job in DB: {e}")

    threading.Thread(target=_db_update, daemon=True).start()
    record_event_async(
        "zip_failed",
        session_id=sess_id,
        status="failure",
        error_category="zip_failed",
        metadata={"job_id": job_id, "error": error_message, "duration_s": duration}
    )


# ==============================================================================
# Aggregated Analytics & Reporting Engine
# ==============================================================================

def get_analytics_dashboard(range_type: str = "7d", from_date_str: Optional[str] = None, to_date_str: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
    """
    Computes comprehensive analytics dashboard covering:
    1. Key KPI Cards
    2. Visitor Analytics
    3. Face-Search Analytics
    4. Photo Engagement Analytics (Top 10 Most-Viewed)
    5. Photo Library Statistics
    6. Download Analytics & History
    7. Daily Timeseries Chart Data
    """
    cache_key = f"{range_type}_{from_date_str}_{to_date_str}"
    now_ts = time.time()

    if not refresh:
        with _analytics_cache_lock:
            if cache_key in _analytics_cache:
                ts, cached_data = _analytics_cache[cache_key]
                if now_ts - ts < _CACHE_TTL_SECONDS:
                    return cached_data

    start_dt, end_dt, window_label = parse_date_window(range_type, from_date_str, to_date_str)
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()

    # --------------------------------------------------------------------------
    # 1. Photo Library Overall Metrics (Independent of date range)
    # --------------------------------------------------------------------------
    cursor.execute("""
        SELECT 
            COUNT(id) as total_photos,
            SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) as active_photos,
            SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) as deleted_photos,
            SUM(file_size_bytes) as total_vps_bytes
        FROM photos
    """)
    lib_row = cursor.fetchone() or {"total_photos": 0, "active_photos": 0, "deleted_photos": 0, "total_vps_bytes": 0}
    total_photos_count = lib_row["active_photos"] or 0
    total_vps_storage = lib_row["total_vps_bytes"] or 0

    # Face detections & people clusters
    cursor.execute("SELECT COUNT(id) as total_faces FROM face_detections")
    face_det_row = cursor.fetchone()
    total_detected_faces = face_det_row["total_faces"] if face_det_row else 0

    cursor.execute("SELECT COUNT(id) as total_people FROM person_profiles WHERE is_hidden = 0")
    ppl_row = cursor.fetchone()
    total_people_clusters = ppl_row["total_people"] if ppl_row else 0

    # Indexed photos (photos that have at least one face detection or embedding)
    cursor.execute("""
        SELECT COUNT(DISTINCT photo_id) as indexed_count
        FROM face_detections
    """)
    idx_row = cursor.fetchone()
    indexed_photos_count = idx_row["indexed_count"] if idx_row else 0
    # Bound indexed photos to not exceed total active photos
    indexed_photos_count = min(indexed_photos_count, total_photos_count)
    pending_photos_count = max(0, total_photos_count - indexed_photos_count)

    # --------------------------------------------------------------------------
    # 2. Date-Filtered Analytics Events Aggregation
    # --------------------------------------------------------------------------
    cursor.execute("""
        SELECT 
            COUNT(CASE WHEN event_type = 'portal_visit' THEN 1 END) as total_visits,
            COUNT(DISTINCT CASE WHEN visitor_id != '' THEN visitor_id ELSE session_id END) as unique_visitors,
            COUNT(CASE WHEN event_type = 'search_started' THEN 1 END) as searches_initiated,
            COUNT(CASE WHEN event_type IN ('search_completed', 'search_no_results') THEN 1 END) as searches_completed,
            COUNT(CASE WHEN event_type = 'search_completed' THEN 1 END) as successful_searches,
            COUNT(CASE WHEN event_type = 'search_no_results' THEN 1 END) as zero_result_searches,
            COUNT(CASE WHEN event_type = 'search_failed' THEN 1 END) as search_failures,
            COUNT(CASE WHEN event_type = 'photo_opened' THEN 1 END) as photos_viewed,
            COUNT(CASE WHEN event_type = 'photo_downloaded' THEN 1 END) as single_downloads,
            COUNT(CASE WHEN event_type = 'zip_completed' THEN 1 END) as zip_downloads_completed,
            COUNT(CASE WHEN event_type = 'zip_failed' THEN 1 END) as zip_downloads_failed,
            COUNT(CASE WHEN event_type = 'share_clicked' THEN 1 END) as share_clicks,
            SUM(CASE WHEN event_type = 'search_completed' THEN matches_count ELSE 0 END) as total_matches_found,
            AVG(CASE WHEN event_type IN ('search_completed', 'search_no_results') AND duration_ms > 0 THEN duration_ms END) as avg_search_duration_ms,
            COUNT(CASE WHEN event_type = 'search_failed' AND error_category = 'no_face_detected' THEN 1 END) as face_detection_failures,
            COUNT(CASE WHEN event_type IN ('search_completed', 'search_no_results') AND duration_ms > 3000 THEN 1 END) as slow_searches_count,
            COUNT(CASE WHEN event_type = 'portal_visit' AND device_type = 'mobile' THEN 1 END) as mobile_visits,
            COUNT(CASE WHEN event_type = 'portal_visit' AND device_type = 'desktop' THEN 1 END) as desktop_visits
        FROM analytics_events
        WHERE created_at BETWEEN ? AND ?
    """, (start_str, end_str))
    ev_row = cursor.fetchone() or {}

    total_visits = ev_row["total_visits"] or 0
    unique_visitors = ev_row["unique_visitors"] or 0
    searches_initiated = ev_row["searches_initiated"] or 0
    searches_completed = ev_row["searches_completed"] or 0
    successful_searches = ev_row["successful_searches"] or 0
    zero_result_searches = ev_row["zero_result_searches"] or 0
    search_failures = ev_row["search_failures"] or 0
    total_searches = searches_initiated or (searches_completed + search_failures)
    photos_viewed = ev_row["photos_viewed"] or 0
    single_downloads = ev_row["single_downloads"] or 0
    zip_downloads = ev_row["zip_downloads_completed"] or 0
    zip_failures = ev_row["zip_downloads_failed"] or 0
    share_clicks = ev_row["share_clicks"] or 0
    total_matches = ev_row["total_matches_found"] or 0
    avg_duration_ms = round(ev_row["avg_search_duration_ms"] or 0.0, 1)
    face_detect_failures = ev_row["face_detection_failures"] or 0
    slow_searches = ev_row["slow_searches_count"] or 0

    # First-time vs returning visitors calculation
    # A visitor is returning if their first portal_visit was before start_str
    cursor.execute("""
        SELECT COUNT(DISTINCT a.visitor_id) as returning_count
        FROM analytics_events a
        WHERE a.created_at BETWEEN ? AND ? AND a.visitor_id != ''
          AND EXISTS (
              SELECT 1 FROM analytics_events past
              WHERE past.visitor_id = a.visitor_id AND past.created_at < ?
          )
    """, (start_str, end_str, start_str))
    ret_row = cursor.fetchone()
    returning_visitors = ret_row["returning_count"] if ret_row else 0
    first_time_visitors = max(0, unique_visitors - returning_visitors)

    # Search Success Rate Formula: (Successful Searches / Completed Searches) * 100
    if searches_completed > 0:
        search_success_rate = round((successful_searches / searches_completed) * 100, 1)
    elif total_searches > 0:
        search_success_rate = round((successful_searches / total_searches) * 100, 1)
    else:
        search_success_rate = 0.0

    # Search abandonment rate (% of started searches that never reached completed or zero_results)
    if searches_initiated > 0:
        abandoned_searches = max(0, searches_initiated - searches_completed - search_failures)
        abandonment_rate = round((abandoned_searches / searches_initiated) * 100, 1)
    else:
        abandonment_rate = 0.0

    # Average photographs found per successful search
    avg_photos_per_match = round(total_matches / successful_searches, 1) if successful_searches > 0 else 0.0

    # Mobile vs Desktop Breakdown
    mob_count = ev_row["mobile_visits"] or 0
    dsk_count = ev_row["desktop_visits"] or 0
    tot_devices = mob_count + dsk_count
    mobile_percent = round((mob_count / tot_devices) * 100, 1) if tot_devices > 0 else 65.0
    desktop_percent = round((dsk_count / tot_devices) * 100, 1) if tot_devices > 0 else 35.0

    # --------------------------------------------------------------------------
    # 3. Top 10 Most-Viewed Photographs
    # --------------------------------------------------------------------------
    cursor.execute("""
        SELECT 
            ae.photo_path,
            COUNT(CASE WHEN ae.event_type = 'photo_opened' THEN 1 END) as view_count,
            COUNT(CASE WHEN ae.event_type = 'photo_downloaded' THEN 1 END) as download_count,
            p.captured_at,
            a.name as album_name
        FROM analytics_events ae
        LEFT JOIN photos p ON (ae.photo_path = p.rel_path OR ae.photo_path = p.filename)
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE ae.created_at BETWEEN ? AND ? 
          AND ae.photo_path != ''
          AND ae.event_type IN ('photo_opened', 'photo_downloaded')
        GROUP BY ae.photo_path
        ORDER BY view_count DESC, download_count DESC
        LIMIT 10
    """, (start_str, end_str))
    top_photos_rows = cursor.fetchall()
    top_photos = []
    for r in top_photos_rows:
        p_path = r["photo_path"]
        fname = Path(p_path).name
        enc = "/".join([c for c in p_path.split("/") if c])
        top_photos.append({
            "rel_path": p_path,
            "filename": fname,
            "thumb_url": f"/thumb/{enc}",
            "preview_url": f"/preview/{enc}",
            "album_name": r["album_name"] or "BRK Sir Event",
            "captured_at": r["captured_at"] or "",
            "views": r["view_count"],
            "downloads": r["download_count"]
        })

    # --------------------------------------------------------------------------
    # 4. Download Analytics & Detailed Download History
    # --------------------------------------------------------------------------
    cursor.execute("""
        SELECT 
            COUNT(id) as total_zip_jobs,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_zips,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_zips,
            SUM(CASE WHEN status = 'completed' THEN photo_count ELSE 0 END) as total_photos_in_zips,
            SUM(CASE WHEN status = 'completed' THEN zip_size_bytes ELSE 0 END) as total_zip_bytes,
            AVG(CASE WHEN status = 'completed' AND duration_seconds > 0 THEN duration_seconds END) as avg_duration,
            MAX(CASE WHEN status = 'completed' THEN zip_size_bytes ELSE 0 END) as largest_zip_bytes
        FROM download_jobs
        WHERE started_at BETWEEN ? AND ?
    """, (start_str, end_str))
    dl_row = cursor.fetchone() or {}

    total_zip_jobs = dl_row["total_zip_jobs"] or zip_downloads or 0
    completed_zips = dl_row["completed_zips"] or zip_downloads or 0
    failed_zips = dl_row["failed_zips"] or zip_failures or 0
    zip_completion_rate = round((completed_zips / total_zip_jobs) * 100, 1) if total_zip_jobs > 0 else 100.0
    total_photos_in_zips = dl_row["total_photos_in_zips"] or 0
    total_photos_downloaded = total_photos_in_zips + single_downloads
    total_zip_bytes = dl_row["total_zip_bytes"] or 0
    # Estimate ~4MB per single original download if not captured directly
    total_data_transferred_bytes = total_zip_bytes + (single_downloads * 4 * 1024 * 1024)
    avg_zip_duration_s = round(dl_row["avg_duration"] or 0.0, 1)
    largest_zip_mb = round((dl_row["largest_zip_bytes"] or 0) / (1024 * 1024), 2)

    with _active_zip_lock:
        active_zip_jobs_count = len(_active_zip_jobs)

    # Recent download jobs list
    cursor.execute("""
        SELECT job_id, job_type, photo_count, zip_size_bytes, duration_seconds, status, started_at
        FROM download_jobs
        WHERE started_at BETWEEN ? AND ?
        ORDER BY started_at DESC
        LIMIT 25
    """, (start_str, end_str))
    dl_history_rows = cursor.fetchall()
    download_history = []
    for r in dl_history_rows:
        download_history.append({
            "job_id": r["job_id"],
            "job_type": r["job_type"],
            "photo_count": r["photo_count"],
            "size_mb": round((r["zip_size_bytes"] or 0) / (1024 * 1024), 2),
            "duration_s": round(r["duration_seconds"] or 0.0, 1),
            "status": r["status"],
            "started_at": r["started_at"]
        })

    # --------------------------------------------------------------------------
    # 5. Timeseries Chart Data (Daily Buckets for Selected Window)
    # --------------------------------------------------------------------------
    # Generate continuous day dates across range
    day_span = max(1, (end_dt.date() - start_dt.date()).days + 1)
    daily_buckets: Dict[str, Dict[str, Any]] = {}
    
    cur_d = start_dt.date()
    while cur_d <= end_dt.date():
        ds = cur_d.strftime("%Y-%m-%d")
        daily_buckets[ds] = {
            "date": ds,
            "label": cur_d.strftime("%d %b"),
            "visits": 0,
            "unique_visitors": 0,
            "total_searches": 0,
            "successful_searches": 0,
            "zero_results": 0,
            "failed_searches": 0,
            "photos_viewed": 0,
            "downloads": 0,
            "success_rate": 0.0
        }
        cur_d += timedelta(days=1)

    cursor.execute("""
        SELECT 
            DATE(created_at) as day_date,
            COUNT(CASE WHEN event_type = 'portal_visit' THEN 1 END) as visits,
            COUNT(DISTINCT CASE WHEN event_type = 'portal_visit' THEN (CASE WHEN visitor_id != '' THEN visitor_id ELSE session_id END) END) as uniques,
            COUNT(CASE WHEN event_type IN ('search_started', 'search_completed', 'search_no_results', 'search_failed') THEN 1 END) as searches,
            COUNT(CASE WHEN event_type = 'search_completed' THEN 1 END) as success_searches,
            COUNT(CASE WHEN event_type = 'search_no_results' THEN 1 END) as zero_searches,
            COUNT(CASE WHEN event_type = 'search_failed' THEN 1 END) as fail_searches,
            COUNT(CASE WHEN event_type = 'photo_opened' THEN 1 END) as views,
            COUNT(CASE WHEN event_type IN ('photo_downloaded', 'zip_completed') THEN 1 END) as downloads
        FROM analytics_events
        WHERE created_at BETWEEN ? AND ?
        GROUP BY DATE(created_at)
        ORDER BY day_date ASC
    """, (start_str, end_str))

    for r in cursor.fetchall():
        d_key = r["day_date"]
        if d_key in daily_buckets:
            s_succ = r["success_searches"] or 0
            s_tot = (r["success_searches"] or 0) + (r["zero_searches"] or 0)
            rate = round((s_succ / s_tot) * 100, 1) if s_tot > 0 else 0.0
            daily_buckets[d_key].update({
                "visits": r["visits"] or 0,
                "unique_visitors": r["uniques"] or 0,
                "total_searches": r["searches"] or 0,
                "successful_searches": s_succ,
                "zero_results": r["zero_searches"] or 0,
                "failed_searches": r["fail_searches"] or 0,
                "photos_viewed": r["views"] or 0,
                "downloads": r["downloads"] or 0,
                "success_rate": rate
            })

    chart_series = list(daily_buckets.values())

    # Hourly distribution for peak activity analysis
    cursor.execute("""
        SELECT STRFTIME('%H', created_at) as hour_str, COUNT(id) as event_count
        FROM analytics_events
        WHERE created_at BETWEEN ? AND ?
        GROUP BY hour_str
        ORDER BY hour_str ASC
    """, (start_str, end_str))
    hourly_distribution = {f"{h:02d}:00": 0 for h in range(24)}
    for r in cursor.fetchall():
        h = f"{int(r['hour_str']):02d}:00"
        hourly_distribution[h] = r["event_count"]

    conn.close()

    result = {
        "status": "ok",
        "range_type": range_type,
        "window_label": window_label,
        "start_time": start_str,
        "end_time": end_str,
        "overview": {
            "total_photographs": total_photos_count,
            "indexed_photographs": indexed_photos_count,
            "pending_photographs": pending_photos_count,
            "total_searches": total_searches,
            "successful_searches": successful_searches,
            "no_match_searches": zero_result_searches,
            "unique_visitors": unique_visitors,
            "photos_viewed": photos_viewed,
            "zip_downloads": completed_zips,
            "original_downloads": single_downloads,
            "search_success_rate": search_success_rate
        },
        "visitor_analytics": {
            "total_visits": total_visits,
            "unique_visitors": unique_visitors,
            "first_time_visitors": first_time_visitors,
            "returning_visitors": returning_visitors,
            "searches_initiated": searches_initiated,
            "searches_completed": searches_completed,
            "abandonment_rate": abandonment_rate,
            "avg_photos_per_match": avg_photos_per_match,
            "mobile_percent": mobile_percent,
            "desktop_percent": desktop_percent,
            "hourly_distribution": hourly_distribution
        },
        "search_analytics": {
            "total_requests": total_searches,
            "successful_searches": successful_searches,
            "zero_result_searches": zero_result_searches,
            "search_failures": search_failures,
            "search_success_rate": search_success_rate,
            "avg_duration_ms": avg_duration_ms,
            "avg_matches": avg_photos_per_match,
            "face_detection_failures": face_detect_failures,
            "slow_searches": slow_searches
        },
        "photo_engagement": {
            "total_photo_views": photos_viewed,
            "single_downloads": single_downloads,
            "share_clicks": share_clicks,
            "zip_downloads": completed_zips,
            "zip_completion_rate": zip_completion_rate,
            "top_photos": top_photos
        },
        "library_analytics": {
            "total_photos": total_photos_count,
            "indexed_photos": indexed_photos_count,
            "pending_photos": pending_photos_count,
            "failed_analysis": 0,
            "detected_faces": total_detected_faces,
            "people_clusters": total_people_clusters,
            "vps_storage_mb": round(total_vps_storage / (1024 * 1024), 2),
            "processing_status": {
                "completed": indexed_photos_count,
                "pending": pending_photos_count,
                "processing": 0,
                "failed": 0
            }
        },
        "download_analytics": {
            "total_zip_requests": total_zip_jobs,
            "completed_zips": completed_zips,
            "failed_zips": failed_zips,
            "zip_completion_rate": zip_completion_rate,
            "single_downloads": single_downloads,
            "total_photos_downloaded": total_photos_downloaded,
            "total_data_transferred_mb": round(total_data_transferred_bytes / (1024 * 1024), 2),
            "avg_zip_duration_s": avg_zip_duration_s,
            "largest_zip_mb": largest_zip_mb,
            "active_zip_jobs": active_zip_jobs_count,
            "download_history": download_history
        },
        "chart_series": chart_series
    }

    with _analytics_cache_lock:
        _analytics_cache[cache_key] = (now_ts, result)

    return result


# ==============================================================================
# System & Performance Monitoring (Cached, Non-Burdening)
# ==============================================================================

def get_system_metrics(refresh: bool = False) -> Dict[str, Any]:
    """
    Lightweight sampling of OS CPU, RAM, disk usage, DB size, and queue lengths.
    Cached for 15 seconds to avoid repeated disk sweeps.
    """
    global _system_metrics_cache
    now_ts = time.time()

    if not refresh:
        with _system_metrics_lock:
            ts, cached = _system_metrics_cache
            if now_ts - ts < _SYSTEM_METRICS_TTL and cached:
                return cached

    # 1. Disk usage
    try:
        disk = shutil.disk_usage(STORAGE)
        total_disk_gb = round(disk.total / (1024**3), 1)
        used_disk_gb = round(disk.used / (1024**3), 1)
        avail_disk_gb = round(disk.free / (1024**3), 1)
        disk_pct = round((disk.used / disk.total) * 100, 1)
    except Exception:
        total_disk_gb, used_disk_gb, avail_disk_gb, disk_pct = 50.0, 10.0, 40.0, 20.0

    # 2. Database size
    try:
        db_size_mb = round(os.path.getsize(DB_PATH) / (1024**2), 2) if DB_PATH.exists() else 0.0
    except Exception:
        db_size_mb = 0.0

    # 3. Photo storage & thumbnail cache size
    def get_dir_size_mb(path: Path) -> float:
        if not path.exists():
            return 0.0
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += sum(f.stat().st_size for f in os.scandir(entry.path) if f.is_file(follow_symlinks=False))
        except Exception:
            pass
        return round(total / (1024**2), 2)

    photos_size_mb = get_dir_size_mb(PHOTOS)
    thumbs_size_mb = get_dir_size_mb(DATA_DIR / "thumbs")

    # 4. CPU & RAM (using standard library / procfs fallback)
    cpu_percent = 0.0
    ram_percent = 0.0
    ram_total_mb = 0.0
    ram_used_mb = 0.0

    # Check loadavg
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        cpu_percent = min(100.0, round((load1 / cpu_count) * 100, 1))
    except Exception:
        cpu_percent = 5.0

    # Read /proc/meminfo on Linux VPS
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            mem_data = {}
            with open(meminfo_path, "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        mem_data[k] = int(v)
            m_total = mem_data.get("MemTotal", 0) / 1024.0 # MB
            m_avail = mem_data.get("MemAvailable", mem_data.get("MemFree", 0)) / 1024.0
            m_used = m_total - m_avail
            ram_total_mb = round(m_total, 1)
            ram_used_mb = round(m_used, 1)
            ram_percent = round((m_used / m_total) * 100, 1) if m_total > 0 else 0.0
        except Exception:
            pass
    
    if ram_total_mb == 0.0:
        # Fallback values for macOS / local dev
        ram_total_mb = 8192.0
        ram_used_mb = 3200.0
        ram_percent = 39.1

    # 5. Active queues & workers
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(id) as q_len FROM upload_queue WHERE status = 'queued'")
    q_row = cursor.fetchone()
    upload_queue_len = q_row["q_len"] if q_row else 0

    cursor.execute("SELECT COUNT(id) as running_tasks FROM task_history WHERE status = 'running'")
    t_row = cursor.fetchone()
    active_tasks = t_row["running_tasks"] if t_row else 0
    conn.close()

    with _active_zip_lock:
        active_zips = len(_active_zip_jobs)

    # 6. Warning indicators
    warnings = []
    if ram_percent > 85.0:
        warnings.append({"level": "warning", "code": "HIGH_RAM", "message": f"High RAM usage: {ram_percent}%"})
    if cpu_percent > 85.0:
        warnings.append({"level": "warning", "code": "HIGH_CPU", "message": f"High CPU load: {cpu_percent}%"})
    if avail_disk_gb < 10.0 or disk_pct > 90.0:
        warnings.append({"level": "danger", "code": "LOW_DISK", "message": f"Low available disk space: {avail_disk_gb} GB remaining"})
    if upload_queue_len > 50:
        warnings.append({"level": "info", "code": "QUEUE_BACKLOG", "message": f"Upload queue has {upload_queue_len} pending tasks"})

    metrics = {
        "status": "ok",
        "cpu_percent": cpu_percent,
        "ram": {
            "percent": ram_percent,
            "total_mb": ram_total_mb,
            "used_mb": ram_used_mb,
            "free_mb": round(ram_total_mb - ram_used_mb, 1)
        },
        "disk": {
            "total_gb": total_disk_gb,
            "used_gb": used_disk_gb,
            "available_gb": avail_disk_gb,
            "percent": disk_pct
        },
        "storage": {
            "database_mb": db_size_mb,
            "photos_dir_mb": photos_size_mb,
            "thumbnails_mb": thumbs_size_mb
        },
        "queues": {
            "upload_queue_len": upload_queue_len,
            "active_tasks": active_tasks,
            "active_zip_jobs": active_zips,
            "analytics_backlog": _event_queue.qsize()
        },
        "warnings": warnings,
        "sampled_at": now_ist().strftime("%Y-%m-%d %H:%M:%S")
    }

    with _system_metrics_lock:
        _system_metrics_cache = (now_ts, metrics)

    return metrics


# ==============================================================================
# CSV Report Exporter
# ==============================================================================

def generate_csv_report(range_type: str = "30d", from_date_str: Optional[str] = None, to_date_str: Optional[str] = None, report_type: str = "daily") -> str:
    """Generates formatted CSV report ready for download."""
    data = get_analytics_dashboard(range_type, from_date_str, to_date_str, refresh=True)
    lines: List[str] = []

    if report_type == "daily":
        lines.append("Date,Day Label,Total Visits,Unique Visitors,Total Searches,Successful Searches,Zero Results,Failed Searches,Success Rate (%),Photos Viewed,Downloads")
        for item in data.get("chart_series", []):
            lines.append(f"{item['date']},{item['label']},{item['visits']},{item['unique_visitors']},{item['total_searches']},{item['successful_searches']},{item['zero_results']},{item['failed_searches']},{item['success_rate']}%,{item['photos_viewed']},{item['downloads']}")
    
    elif report_type == "downloads":
        lines.append("Job ID,Type,Photos Count,Size (MB),Duration (s),Status,Date & Time")
        for j in data.get("download_analytics", {}).get("download_history", []):
            lines.append(f'"{j["job_id"]}","{j["job_type"]}",{j["photo_count"]},{j["size_mb"]},{j["duration_s"]},"{j["status"]}","{j["started_at"]}"')
    
    elif report_type == "top_photos":
        lines.append("Rank,Filename,Album,Views,Downloads,Captured Date,Path")
        for idx, p in enumerate(data.get("photo_engagement", {}).get("top_photos", []), start=1):
            lines.append(f'{idx},"{p["filename"]}","{p["album_name"]}",{p["views"]},{p["downloads"]},"{p["captured_at"]}","{p["rel_path"]}"')
    
    return "\n".join(lines)
