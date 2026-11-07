"""
Database layer for MagniWedding multi-event architecture.
Uses SQLite in WAL mode for microsecond reads and concurrent safety.
"""
import sqlite3
import os
import sys
import re
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
DATA_DIR = STORAGE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS = STORAGE / "photos"
DB_PATH = DATA_DIR / "magniwedding.db"

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    return datetime.now(IST)

def parse_date_string(text: str) -> Optional[str]:
    """Extracts an ISO date YYYY-MM-DD from various folder, album, or filename strings."""
    if not text:
        return None
    # 1. Matches DD-MM-YYYY or DD-MM-YY (e.g. 29-10-2026, 29-10-26, 01-10-26)
    m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d\d|\d\d)\b', text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        return f"{year:04d}-{month:02d}-{day:02d}"
    
    # 2. Matches YYYY-MM-DD (e.g. 2026-10-29)
    m = re.search(r'\b(20\d\d)[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12][0-9]|3[01])\b', text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    
    # 3. Matches DD MON (e.g. 01 JUN, 15 OCT 2026)
    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
    m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[\s_-]+([a-zA-Z]{3,9})(?:[\s_-]+(20\d\d|\d\d))?\b', text)
    if m:
        day = int(m.group(1))
        mon_str = m.group(2).lower()[:3]
        if mon_str in months:
            month = months[mon_str]
            yr = int(m.group(3)) if m.group(3) else 2026
            if yr < 100:
                yr += 2000
            return f"{yr:04d}-{month:02d}-{day:02d}"
    return None

def extract_photo_capture_date(file_path: Optional[Path] = None, rel_path: str = "", album_hint: str = "") -> str:
    """
    Extracts the most accurate ISO timestamp (YYYY-MM-DD HH:MM:SS) for a photograph:
    1. EXIF DateTimeOriginal / DateTimeDigitized from file
    2. Reliable date in album name or slug (e.g. 29-10-2026)
    3. Date in rel_path or filename
    4. Fallback to file creation timestamp or now
    """
    # 1. EXIF metadata
    if file_path and file_path.exists():
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                exif = img.getexif()
                if exif:
                    for tag_id in (36867, 36868, 306): # DateTimeOriginal, DateTimeDigitized, DateTime
                        val = exif.get(tag_id)
                        if val and isinstance(val, str) and len(val) >= 10:
                            parts = val.strip().split()
                            date_part = parts[0].replace(":", "-")
                            time_part = parts[1] if len(parts) > 1 else "12:00:00"
                            return f"{date_part} {time_part}"
        except Exception:
            pass

    # 2. Date in album hint (e.g. 29-10-2026, 28-10-26, 01-10-26)
    d = parse_date_string(album_hint)
    if d:
        return f"{d} 12:00:00"

    # 3. Date in rel_path or filename
    d = parse_date_string(rel_path)
    if d:
        return f"{d} 12:00:00"

    # 4. Fallback to mtime or current time
    if file_path and file_path.exists():
        try:
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            return mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return now_ist().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA cache_size = -64000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            event_type TEXT DEFAULT 'Wedding',
            event_date TEXT,
            location TEXT,
            cover_image_path TEXT,
            description TEXT,
            status TEXT DEFAULT 'published',
            passcode TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            cover_image_path TEXT,
            sort_order INTEGER DEFAULT 0,
            is_published BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_id, slug)
        );

        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
            filename TEXT NOT NULL,
            rel_path TEXT UNIQUE NOT NULL,
            drive_file_id TEXT,
            file_size_bytes INTEGER DEFAULT 0,
            width INTEGER,
            height INTEGER,
            is_featured BOOLEAN DEFAULT 0,
            is_hidden BOOLEAN DEFAULT 0,
            is_deleted BOOLEAN DEFAULT 0,
            deleted_at TIMESTAMP,
            captured_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS person_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name TEXT DEFAULT 'Unnamed Guest',
            avatar_thumb_path TEXT,
            is_hidden BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS face_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            person_id INTEGER REFERENCES person_profiles(id) ON DELETE SET NULL,
            bbox_json TEXT,
            crop_path TEXT,
            embedding_offset INTEGER,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            task_type TEXT NOT NULL,
            event_slug TEXT NOT NULL,
            target_folder TEXT,
            source_url TEXT,
            status TEXT DEFAULT 'running',
            progress_pct INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            processed_files INTEGER DEFAULT 0,
            storage_saved_mb REAL DEFAULT 0,
            current_step TEXT,
            log_text TEXT DEFAULT '',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            task_name TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'direct_upload',
            event_slug TEXT NOT NULL,
            target_album_slug TEXT NOT NULL,
            target_album_name TEXT,
            source_info TEXT,
            staged_dir TEXT,
            file_count INTEGER DEFAULT 0,
            processed_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            progress_pct INTEGER DEFAULT 0,
            current_step TEXT DEFAULT 'Waiting in queue...',
            error_message TEXT,
            log_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS automations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_slug TEXT NOT NULL DEFAULT 'brk-sir',
            gdrive_url TEXT NOT NULL,
            target_album_slug TEXT DEFAULT 'auto',
            formats_json TEXT DEFAULT '["jpg","png","webp"]',
            trigger_date TEXT NOT NULL,
            trigger_time TEXT NOT NULL,
            repeat_interval TEXT NOT NULL DEFAULT 'none',
            is_active BOOLEAN NOT NULL DEFAULT 1,
            last_triggered_at TIMESTAMP,
            last_status TEXT,
            last_task_id TEXT,
            next_trigger_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS automation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id INTEGER REFERENCES automations(id) ON DELETE CASCADE,
            automation_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'queued',
            files_count INTEGER DEFAULT 0,
            result_message TEXT,
            error_details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug);
        CREATE INDEX IF NOT EXISTS idx_albums_event ON albums(event_id);
        CREATE INDEX IF NOT EXISTS idx_photos_event_album ON photos(event_id, album_id);
        CREATE INDEX IF NOT EXISTS idx_photos_deleted ON photos(is_deleted);
        CREATE INDEX IF NOT EXISTS idx_task_history_id ON task_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_task_history_status ON task_history(status, id);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status, id);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_event ON upload_queue(event_slug, status);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_task_id ON upload_queue(task_id);
        CREATE INDEX IF NOT EXISTS idx_automations_active ON automations(is_active, next_trigger_at);
        CREATE INDEX IF NOT EXISTS idx_automations_event ON automations(event_slug, is_active);
        CREATE INDEX IF NOT EXISTS idx_automation_history_task ON automation_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_automation_history_auto ON automation_history(automation_id, id);

        -- Analytics & Telemetry Layer
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visitor_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            search_id TEXT,
            photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
            photo_path TEXT,
            album_name TEXT,
            device_type TEXT DEFAULT 'desktop',
            duration_ms REAL DEFAULT 0,
            matches_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_category TEXT,
            metadata_json TEXT,
            ip_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS download_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            job_type TEXT NOT NULL DEFAULT 'zip',
            session_id TEXT,
            visitor_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            photo_count INTEGER DEFAULT 0,
            zip_size_bytes INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'processing',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS duplicate_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            match_type TEXT NOT NULL DEFAULT 'exact',
            similarity_score REAL DEFAULT 1.0,
            primary_photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            duplicate_photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(primary_photo_id, duplicate_photo_id)
        );

        CREATE TABLE IF NOT EXISTS match_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            query_crop_path TEXT,
            matched_photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
            matched_rel_path TEXT,
            similarity_score REAL,
            confidence_tier TEXT,
            feedback TEXT DEFAULT 'pending',
            notes TEXT,
            reviewer_ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS indexing_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            event_slug TEXT,
            photo_rel_path TEXT,
            error_stage TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_dup_group_id ON duplicate_groups(group_id);
        CREATE INDEX IF NOT EXISTS idx_dup_status ON duplicate_groups(status);
        CREATE INDEX IF NOT EXISTS idx_match_reviews_feedback ON match_reviews(feedback);
        CREATE INDEX IF NOT EXISTS idx_match_reviews_tier ON match_reviews(confidence_tier);
        CREATE INDEX IF NOT EXISTS idx_indexing_errors_task ON indexing_errors(task_id);

        CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_type_created ON analytics_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_session ON analytics_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_analytics_visitor ON analytics_events(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_analytics_photo_path ON analytics_events(photo_path);
        CREATE INDEX IF NOT EXISTS idx_analytics_status ON analytics_events(status);
        CREATE INDEX IF NOT EXISTS idx_download_jobs_started ON download_jobs(started_at);
        CREATE INDEX IF NOT EXISTS idx_download_jobs_status ON download_jobs(status);
        """)

        # One-time schema migrations
        try:
            conn.execute("ALTER TABLE albums ADD COLUMN is_deleted BOOLEAN DEFAULT 0;")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM photos WHERE id NOT IN (SELECT MIN(id) FROM photos GROUP BY rel_path);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_event_rel ON photos(event_id, rel_path);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_rel_unique ON photos(rel_path);")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE photos ADD COLUMN captured_at TIMESTAMP;")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_captured ON photos(captured_at);")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE face_detections ADD COLUMN crop_path TEXT;")
            conn.commit()
        except Exception:
            pass

        # AI Matching & Photo Quality migrations
        for col_def in [
            "sha256_hash TEXT",
            "phash TEXT",
            "blur_score REAL",
            "exposure_score REAL",
            "quality_flags TEXT",
            "quality_grade TEXT"
        ]:
            try:
                conn.execute(f"ALTER TABLE photos ADD COLUMN {col_def};")
                conn.commit()
            except Exception:
                pass

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_quality_grade ON photos(quality_grade);")
            conn.commit()
        except Exception:
            pass

        # Default system settings
        default_settings = [
            ("match_threshold_high", "0.60"),
            ("match_threshold_medium", "0.48"),
            ("match_threshold_min", "0.40"),
            ("default_event_slug", "brk-sir"),
            ("dedup_dhash_threshold", "4")
        ]
        for k, v in default_settings:
            try:
                conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))
            except Exception:
                pass
        conn.commit()
    conn.close()
    backfill_photo_dates()

def backfill_photo_dates():
    """Extracts and populates captured_at for any photos missing date metadata."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT p.id, p.rel_path, p.filename, p.created_at, a.name as album_name, a.slug as album_slug
            FROM photos p
            LEFT JOIN albums a ON p.album_id = a.id
            WHERE p.captured_at IS NULL OR p.captured_at = ''
        """)
        rows = c.fetchall()
        if not rows:
            conn.close()
            return 0
        updated = 0
        for r in rows:
            d = extract_photo_capture_date(
                file_path=PHOTOS / r["rel_path"] if (PHOTOS / r["rel_path"]).exists() else None,
                rel_path=r["rel_path"],
                album_hint=r["album_name"] or r["album_slug"] or ""
            )
            c.execute("UPDATE photos SET captured_at = ? WHERE id = ?", (d, r["id"]))
            updated += 1
        conn.commit()
        conn.close()
        return updated
    except Exception:
        conn.close()
        return 0

# In-memory log buffer for high-frequency task logs to avoid SQLite disk write churn
_task_log_buffers = {}
_task_log_lock = threading.Lock()
_last_flush_times = {}

def buffer_task_log(task_id: str, line: str):
    if not line or not task_id:
        return
    clean = line.strip()
    if not clean:
        return
    formatted = clean if clean.startswith("[") else f"[{clean}]"
    with _task_log_lock:
        if task_id not in _task_log_buffers:
            _task_log_buffers[task_id] = []
        _task_log_buffers[task_id].append(formatted)

def flush_task_logs(task_id: str, force: bool = False) -> str:
    with _task_log_lock:
        lines = _task_log_buffers.get(task_id, [])
        if not lines:
            return ""
        now = time.time()
        last = _last_flush_times.get(task_id, 0)
        if force or (now - last >= 3.0) or len(lines) >= 25:
            text = "\n".join(lines) + "\n"
            _task_log_buffers[task_id] = []
            _last_flush_times[task_id] = now
            return text
        return ""

def create_task_record(task_id: str, task_type: str, event_slug: str, target_folder: str = "", source_url: str = ""):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO task_history (task_id, task_type, event_slug, target_folder, source_url, status, progress_pct, current_step)
        VALUES (?, ?, ?, ?, ?, 'running', 0, 'Starting task...')
    """, (task_id, task_type, event_slug, target_folder, source_url))
    conn.commit()
    conn.close()

def update_task_progress(task_id: str, progress_pct: int = None, current_step: str = None,
                         processed_files: int = None, total_files: int = None,
                         storage_saved_mb: float = None, status: str = None, log_line: str = None):
    if log_line:
        buffer_task_log(task_id, log_line)

    force_flush = (status is not None) or (progress_pct is not None and progress_pct in {0, 10, 50, 80, 100})
    flushed_text = flush_task_logs(task_id, force=force_flush)

    has_meta_change = any(x is not None for x in (progress_pct, current_step, processed_files, total_files, storage_saved_mb, status))
    if not has_meta_change and not flushed_text:
        return

    conn = get_db()
    c = conn.cursor()
    fields = []
    vals = []
    if progress_pct is not None:
        fields.append("progress_pct = ?")
        vals.append(progress_pct)
    if current_step is not None:
        fields.append("current_step = ?")
        vals.append(current_step)
    if processed_files is not None:
        fields.append("processed_files = ?")
        vals.append(processed_files)
    if total_files is not None:
        fields.append("total_files = ?")
        vals.append(total_files)
    if storage_saved_mb is not None:
        fields.append("storage_saved_mb = ?")
        vals.append(storage_saved_mb)
    if status is not None:
        fields.append("status = ?")
        vals.append(status)
        if status in {"completed", "failed", "cancelled"}:
            fields.append("completed_at = CURRENT_TIMESTAMP")
    if flushed_text:
        fields.append("log_text = COALESCE(log_text, '') || ?")
        vals.append(flushed_text)

    if fields:
        vals_history = list(vals) + [task_id]
        c.execute(f"UPDATE task_history SET {', '.join(fields)} WHERE task_id = ?", vals_history)

        # Also update upload_queue if this task is present in upload_queue
        q_fields = []
        q_vals = []
        if progress_pct is not None:
            q_fields.append("progress_pct = ?")
            q_vals.append(progress_pct)
        if current_step is not None:
            q_fields.append("current_step = ?")
            q_vals.append(current_step)
        if processed_files is not None:
            q_fields.append("processed_count = ?")
            q_vals.append(processed_files)
        if total_files is not None:
            q_fields.append("file_count = ?")
            q_vals.append(total_files)
        if status is not None:
            q_fields.append("status = ?")
            q_vals.append(status)
            if status in {"completed", "failed", "cancelled"}:
                q_fields.append("completed_at = CURRENT_TIMESTAMP")
        if flushed_text:
            q_fields.append("log_text = COALESCE(log_text, '') || ?")
            q_vals.append(flushed_text)
        if q_fields:
            q_vals.append(task_id)
            c.execute(f"UPDATE upload_queue SET {', '.join(q_fields)} WHERE task_id = ?", q_vals)

        conn.commit()
    conn.close()

def stop_running_tasks(reason="Task stopped by admin."):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE task_history 
        SET status = 'cancelled', 
            current_step = ?, 
            completed_at = CURRENT_TIMESTAMP,
            log_text = COALESCE(log_text, '') || ?
        WHERE status = 'running'
    """, (reason, f"[{reason}]\n"))
    c.execute("""
        UPDATE upload_queue 
        SET status = 'cancelled', 
            current_step = ?, 
            completed_at = CURRENT_TIMESTAMP,
            log_text = COALESCE(log_text, '') || ?
        WHERE status = 'running'
    """, (reason, f"[{reason}]\n"))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected

def get_latest_task():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, task_id, task_type, event_slug, target_folder, source_url, status,
               progress_pct, total_files, processed_files, storage_saved_mb, current_step,
               started_at, completed_at
        FROM task_history ORDER BY id DESC LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def list_task_history(limit=30):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, task_id, task_type, event_slug, target_folder, source_url, status,
               progress_pct, total_files, processed_files, storage_saved_mb, current_step,
               started_at, completed_at
        FROM task_history ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_task_by_id(task_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM task_history WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    with _task_log_lock:
        pending = _task_log_buffers.get(task_id, [])
        if pending:
            d["log_text"] = (d["log_text"] or "") + "\n".join(pending) + "\n"
    return d

# --- Upload Queue Operations (FIFO Sequential Processing) ---

def enqueue_upload_task(task_id: str, task_name: str, task_type: str, event_slug: str,
                        target_album_slug: str, target_album_name: str = "",
                        source_info: str = "", staged_dir: str = "", file_count: int = 0):
    conn = get_db()
    c = conn.cursor()
    time_str = datetime.now().strftime('%H:%M:%S')
    c.execute("""
        INSERT OR REPLACE INTO upload_queue (
            task_id, task_name, task_type, event_slug, target_album_slug, target_album_name,
            source_info, staged_dir, file_count, status, progress_pct, current_step, log_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, 'Waiting in queue...', ?)
    """, (
        task_id, task_name, task_type, event_slug, target_album_slug, target_album_name,
        source_info, staged_dir, file_count, f"[{time_str}] Task #{task_id} added to queue ({file_count} files).\n"
    ))
    conn.commit()
    conn.close()

def claim_next_queued_task():
    """Atomically fetch and lock the next queued task in FIFO order (smallest ID)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")
    c.execute("""
        SELECT * FROM upload_queue 
        WHERE status = 'queued' 
        ORDER BY id ASC 
        LIMIT 1
    """)
    row = c.fetchone()
    if not row:
        conn.commit()
        conn.close()
        return None
    task = dict(row)
    c.execute("""
        UPDATE upload_queue 
        SET status = 'running', 
            started_at = CURRENT_TIMESTAMP,
            current_step = 'Starting processing...'
        WHERE id = ? AND status = 'queued'
    """, (task["id"],))
    conn.commit()
    conn.close()
    return task

def update_queue_task(task_id: str, status: str = None, progress_pct: int = None,
                      current_step: str = None, processed_count: int = None,
                      file_count: int = None, error_message: str = None, log_line: str = None):
    conn = get_db()
    c = conn.cursor()
    fields = []
    vals = []
    if status is not None:
        fields.append("status = ?")
        vals.append(status)
        if status in {"completed", "failed", "cancelled"}:
            fields.append("completed_at = CURRENT_TIMESTAMP")
    if progress_pct is not None:
        fields.append("progress_pct = ?")
        vals.append(progress_pct)
    if current_step is not None:
        fields.append("current_step = ?")
        vals.append(current_step)
    if processed_count is not None:
        fields.append("processed_count = ?")
        vals.append(processed_count)
    if file_count is not None:
        fields.append("file_count = ?")
        vals.append(file_count)
    if error_message is not None:
        fields.append("error_message = ?")
        vals.append(error_message)
    if log_line:
        clean_line = log_line if log_line.strip().startswith("[") else f"[{log_line.strip()}]"
        fields.append("log_text = COALESCE(log_text, '') || ?")
        vals.append(f"{clean_line}\n")
    if fields:
        vals.append(task_id)
        c.execute(f"UPDATE upload_queue SET {', '.join(fields)} WHERE task_id = ?", vals)
        conn.commit()
    conn.close()

def get_queue_tasks(limit=30, event_slug=None):
    """Fetches lightweight task summaries without large log_text payload to prevent table bloat."""
    conn = get_db()
    c = conn.cursor()
    cols = """id, task_id, task_name, task_type, event_slug, target_album_slug, target_album_name,
              source_info, staged_dir, file_count, processed_count, status, progress_pct,
              current_step, error_message, created_at, started_at, completed_at"""
    if event_slug and event_slug not in {"all", "*"}:
        c.execute(f"""
            SELECT {cols} FROM upload_queue 
            WHERE event_slug = ?
            ORDER BY 
                CASE status 
                    WHEN 'running' THEN 1 
                    WHEN 'queued' THEN 2 
                    ELSE 3 
                END ASC,
                CASE WHEN status = 'queued' THEN id ELSE -id END ASC
            LIMIT ?
        """, (event_slug, limit))
    else:
        c.execute(f"""
            SELECT {cols} FROM upload_queue 
            ORDER BY 
                CASE status 
                    WHEN 'running' THEN 1 
                    WHEN 'queued' THEN 2 
                    ELSE 3 
                END ASC,
                CASE WHEN status = 'queued' THEN id ELSE -id END ASC
            LIMIT ?
        """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_queue_task_by_id(task_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM upload_queue WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    with _task_log_lock:
        pending = _task_log_buffers.get(task_id, [])
        if pending:
            d["log_text"] = (d["log_text"] or "") + "\n".join(pending) + "\n"
    return d

def get_queue_task_logs(task_id: str):
    """Specifically fetches the execution log text for a single queue task on demand."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT task_id, task_name, status, current_step, log_text FROM upload_queue WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    with _task_log_lock:
        pending = _task_log_buffers.get(task_id, [])
        if pending:
            d["log_text"] = (d["log_text"] or "") + "\n".join(pending) + "\n"
    return d

def cancel_queue_task(task_id: str):
    conn = get_db()
    c = conn.cursor()
    time_str = datetime.now().strftime('%H:%M:%S')
    c.execute("""
        UPDATE upload_queue 
        SET status = 'cancelled', 
            current_step = 'Cancelled by administrator', 
            completed_at = CURRENT_TIMESTAMP,
            log_text = COALESCE(log_text, '') || ?
        WHERE task_id = ? AND status IN ('queued', 'running')
    """, (f"[{time_str}] Task cancelled by administrator.\n", task_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def retry_queue_task(task_id: str):
    conn = get_db()
    c = conn.cursor()
    time_str = datetime.now().strftime('%H:%M:%S')
    c.execute("""
        UPDATE upload_queue 
        SET status = 'queued', 
            progress_pct = 0, 
            current_step = 'Queued for retry...', 
            error_message = NULL, 
            started_at = NULL, 
            completed_at = NULL,
            log_text = COALESCE(log_text, '') || ?
        WHERE task_id = ? AND status IN ('failed', 'cancelled')
    """, (f"[{time_str}] Task #{task_id} reset to queue for retry.\n", task_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def clear_completed_queue_tasks(event_slug=None):
    conn = get_db()
    c = conn.cursor()
    if event_slug:
        c.execute("DELETE FROM upload_queue WHERE status IN ('completed', 'cancelled') AND event_slug = ?", (event_slug,))
    else:
        c.execute("DELETE FROM upload_queue WHERE status IN ('completed', 'cancelled')")
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected

def get_queue_stats(event_slug=None):
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
            SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) as queued,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled
        FROM upload_queue
    """
    if event_slug:
        query += " WHERE event_slug = ?"
        c.execute(query, (event_slug,))
    else:
        c.execute(query)
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "total": row["total"] or 0,
            "running": row["running"] or 0,
            "queued": row["queued"] or 0,
            "completed": row["completed"] or 0,
            "failed": row["failed"] or 0,
            "cancelled": row["cancelled"] or 0
        }
    return {"total": 0, "running": 0, "queued": 0, "completed": 0, "failed": 0, "cancelled": 0}

def reset_stale_running_queue_tasks():
    """Reset any tasks stuck in 'running' back to 'queued' on startup so they survive restart."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE upload_queue 
        SET status = 'queued', current_step = 'Resumed after restart' 
        WHERE status = 'running'
    """)
    conn.commit()
    conn.close()

def migrate_existing_data():
    """Migrates existing single-event data into the BRK Photos platform structure seamlessly."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if default event exists
    cursor.execute("SELECT id, name, slug FROM events WHERE slug IN ('brk-sir', 'brksir', 'wedding', 'default')")
    row = cursor.fetchone()
    if not row:
        cursor.execute("""
            INSERT INTO events (slug, name, event_type, event_date, location, description, status)
            VALUES ('brk-sir', 'BRK Sir', 'Public Gathering', '2026-11-02', 'Andhra Pradesh', 'Official Photo Portal for Bhashyam Ramakrishna Garu, Member of Parliament, Rajya Sabha (BRK Sir)', 'published')
        """)
        event_id = cursor.lastrowid
    else:
        event_id = row["id"]
        cursor.execute("""
            UPDATE events 
            SET name = 'BRK Sir', 
                slug = 'brk-sir', 
                event_type = 'Public Gathering', 
                description = 'Official Photo Portal for Bhashyam Ramakrishna Garu, Member of Parliament, Rajya Sabha (BRK Sir)' 
            WHERE id = ?
        """, (event_id,))
        cursor.execute("UPDATE task_history SET event_slug = 'BRK Sir' WHERE event_slug IN ('wedding', 'default', 'brk-sir', 'brksir')")
        cursor.execute("UPDATE task_history SET log_text = REPLACE(REPLACE(log_text, 'wedding', 'BRK Sir'), 'The Wedding Celebration', 'Photo with BRK Sir') WHERE log_text LIKE '%wedding%'")
        conn.commit()
        
    # Check if default album exists
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND (slug = 'brk-photos' OR slug = 'ceremony')", (event_id,))
    album_row = cursor.fetchone()
    if not album_row:
        cursor.execute("""
            INSERT INTO albums (event_id, slug, name, sort_order, is_published)
            VALUES (?, 'brk-photos', 'BRK Photos', 1, 1)
        """, (event_id,))
        album_id = cursor.lastrowid
    else:
        album_id = album_row["id"]
        if "Wedding" in album_row["name"] or album_row["name"] == "Ceremony":
            cursor.execute("UPDATE albums SET name = 'BRK Photos', slug = 'brk-photos' WHERE id = ?", (album_id,))
            conn.commit()
        
    # Migrate any photos on disk
    photos_dir = STORAGE / "photos"
    data_dir = STORAGE / "data"
    drive_map_file = data_dir / "drive_map.json"
    drive_map = {}
    if drive_map_file.exists():
        import json
        try:
            drive_map = json.loads(drive_map_file.read_text())
        except Exception:
            pass

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    if photos_dir.exists():
        for p in photos_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in valid_exts:
                rel = str(p.relative_to(photos_dir))
                cursor.execute("SELECT id FROM photos WHERE rel_path = ?", (rel,))
                if not cursor.fetchone():
                    file_size = p.stat().st_size
                    drive_id = drive_map.get(rel)
                    cursor.execute("""
                        INSERT OR IGNORE INTO photos (event_id, album_id, filename, rel_path, drive_file_id, file_size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (event_id, album_id, p.name, rel, drive_id, file_size))
                    
    conn.commit()
    conn.close()

def cluster_and_sync_people(event_slug="brk-sir", threshold=0.48, cancel_check=None):
    """
    High-speed vectorized leader clustering and person profile synchronization.
    Runs in seconds using NumPy matrix operations and batch SQLite inserts.
    """
    try:
        import numpy as np
    except ImportError:
        return {"status": "error", "message": "NumPy not available"}

    import json
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')", (event_slug, event_slug))
    event_row = cursor.fetchone()
    if not event_row:
        conn.close()
        return {"status": "error", "message": "Event not found"}
        
    event_id = event_row["id"]
    
    if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
        data_dir = STORAGE / "data"
    else:
        data_dir = STORAGE / "data" / "events" / event_slug
        
    emb_file = data_dir / "embeddings.npy"
    names_file = data_dir / "names.json"
    
    if not emb_file.exists() or not names_file.exists():
        conn.close()
        return {"status": "ok", "people_count": 0, "message": "No embeddings found"}
        
    embs = np.load(emb_file)
    names = json.loads(names_file.read_text())
    
    if len(embs) == 0:
        conn.close()
        return {"status": "ok", "people_count": 0}
        
    # 1. Normalize embeddings for lightning-fast vectorized cosine similarity
    embs = np.array(embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    # 2. Vectorized leader clustering
    N = len(embs_norm)
    unassigned = np.ones(N, dtype=bool)
    initial_clusters = []

    iter_count = 0
    while np.any(unassigned):
        iter_count += 1
        if iter_count % 100 == 0 and cancel_check and cancel_check():
            conn.close()
            return {"status": "cancelled", "message": "Clustering cancelled by administrator"}
        unassigned_indices = np.where(unassigned)[0]
        leader_idx = unassigned_indices[0]
        sims = np.dot(embs_norm[unassigned_indices], embs_norm[leader_idx])
        matched = unassigned_indices[sims >= threshold]
        initial_clusters.append(matched.tolist())
        unassigned[matched] = False

    # 3. High-speed Pre-allocated Centroid Merging (merge clusters whose centroids are close >= 0.52)
    clusters = []
    centroids_matrix = np.empty((len(initial_clusters), 512), dtype=np.float32)
    num_centroids = 0

    for cl in initial_clusters:
        if cancel_check and cancel_check():
            conn.close()
            return {"status": "cancelled", "message": "Clustering cancelled by administrator"}
        c_emb = np.mean(embs_norm[cl], axis=0)
        norm_c = np.linalg.norm(c_emb)
        c_emb = c_emb / (norm_c if norm_c > 0 else 1.0)
        merged = False
        if num_centroids > 0:
            c_sims = np.dot(centroids_matrix[:num_centroids], c_emb)
            max_idx = int(np.argmax(c_sims))
            if c_sims[max_idx] >= 0.52:
                clusters[max_idx].extend(cl)
                new_c = np.mean(embs_norm[clusters[max_idx]], axis=0)
                norm_new = np.linalg.norm(new_c)
                centroids_matrix[max_idx] = new_c / (norm_new if norm_new > 0 else 1.0)
                merged = True
        if not merged:
            clusters.append(cl)
            centroids_matrix[num_centroids] = c_emb
            num_centroids += 1

    if cancel_check and cancel_check():
        conn.close()
        return {"status": "cancelled", "message": "Clustering cancelled by administrator"}

    # Sort clusters by number of unique photo appearances
    clusters.sort(key=lambda c: len(set(names[idx] for idx in c)), reverse=True)
    
    # 4. Preserve existing person profiles and custom names to prevent ID churn and broken avatars
    cursor.execute("SELECT id, name, avatar_thumb_path FROM person_profiles WHERE event_id = ? ORDER BY id ASC", (event_id,))
    existing_profiles = [dict(r) for r in cursor.fetchall()]

    # Clear old face_detections for clean re-linking
    cursor.execute("DELETE FROM face_detections WHERE event_id = ?", (event_id,))

    # Map photos rel_path, filename and relative subpath to photo id
    cursor.execute("SELECT id, rel_path, filename FROM photos WHERE event_id = ?", (event_id,))
    photo_map = {}
    for r in cursor.fetchall():
        photo_map[r["rel_path"]] = r["id"]
        photo_map[r["filename"]] = r["id"]
        parts = r["rel_path"].split("/")
        if len(parts) >= 2:
            photo_map["/".join(parts[1:])] = r["id"]
        if len(parts) >= 3:
            photo_map["/".join(parts[2:])] = r["id"]

    assigned_person_ids = []
    face_detections_batch = []

    for idx, cl in enumerate(clusters):
        sample_raw = names[cl[0]]
        if event_slug not in {"default", "wedding", "brk-sir", "brksir"} and not sample_raw.startswith("events/"):
            sample_rel = f"events/{event_slug}/{sample_raw}"
        else:
            sample_rel = sample_raw

        # If reusing existing profile at same rank
        if idx < len(existing_profiles):
            ep = existing_profiles[idx]
            person_id = ep["id"]
            name = ep["name"]
            avatar = ep["avatar_thumb_path"] if ep.get("avatar_thumb_path") and ep["avatar_thumb_path"].startswith("faces/") else sample_rel
            if idx == 0 and (not name or name.startswith("Person #")):
                name = "Bhashyam Ramakrishna Garu (BRK Sir)"
            cursor.execute("UPDATE person_profiles SET name = ?, avatar_thumb_path = ? WHERE id = ?", (name, avatar, person_id))
        else:
            default_name = "Bhashyam Ramakrishna Garu (BRK Sir)" if idx == 0 else f"Person #{idx + 1}"
            cursor.execute("""
                INSERT INTO person_profiles (event_id, name, avatar_thumb_path)
                VALUES (?, ?, ?)
            """, (event_id, default_name, sample_rel))
            person_id = cursor.lastrowid

        assigned_person_ids.append(person_id)

        # Batch face detections for high-speed insertion
        for face_idx in cl:
            photo_rel = names[face_idx]
            fname = Path(photo_rel).name
            p_id = photo_map.get(photo_rel) or photo_map.get(fname)
            if p_id:
                face_detections_batch.append((event_id, p_id, person_id, int(face_idx), 0.95))

    if face_detections_batch:
        cursor.executemany("""
            INSERT INTO face_detections (event_id, photo_id, person_id, embedding_offset, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, face_detections_batch)

    # Clean up excess unassigned default profiles if cluster count decreased (preserve named profiles)
    if len(existing_profiles) > len(clusters):
        excess_ids = [
            p["id"] for p in existing_profiles[len(clusters):]
            if not p["name"] or p["name"].startswith("Person #") or p["name"] == "Unnamed Guest"
        ]
        if excess_ids:
            cursor.execute(f"DELETE FROM person_profiles WHERE id IN ({','.join('?' for _ in excess_ids)})", excess_ids)

    conn.commit()
    conn.close()

    # Clustering and profile syncing completed in seconds without blocking avatar crops
    return {
        "status": "ok",
        "total_faces": len(embs),
        "people_count": len(clusters),
        "created": len(assigned_person_ids)
    }

def list_people_for_event(event_slug: str):
    """Returns all identified people for an event with thumbnail and photo count."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.avatar_thumb_path, p.is_hidden,
               COUNT(DISTINCT ph.filename) as photo_count
        FROM person_profiles p
        JOIN events e ON p.event_id = e.id
        LEFT JOIN face_detections fd ON p.id = fd.person_id
        LEFT JOIN photos ph ON fd.photo_id = ph.id AND ph.is_deleted = 0
        WHERE e.slug = ? AND p.is_hidden = 0
        GROUP BY p.id
        HAVING photo_count > 0
        ORDER BY photo_count DESC, p.id ASC
    """, (event_slug,))
    people = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return people

def get_person_photos(event_slug: str, person_id: int):
    """Returns all photos containing a specific person, deduplicated by filename, sorted latest date first."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ph.id, ph.filename, ph.rel_path, ph.drive_file_id, ph.is_featured,
               ph.captured_at, ph.created_at,
               a.name as album_name, a.slug as album_slug,
               e.name as event_name, e.slug as event_slug
        FROM photos ph
        JOIN face_detections fd ON ph.id = fd.photo_id
        JOIN events e ON ph.event_id = e.id
        LEFT JOIN albums a ON ph.album_id = a.id
        WHERE e.slug = ? AND fd.person_id = ? AND ph.is_deleted = 0 AND ph.is_hidden = 0
        GROUP BY ph.filename
        ORDER BY ph.is_featured DESC, COALESCE(ph.captured_at, ph.created_at) DESC, ph.id DESC
    """, (event_slug, person_id))
    photos = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return photos

# --- Automation Helper Functions ---

def compute_next_trigger(trigger_date: str, trigger_time: str, repeat_interval: str = "none") -> str:
    """Calculates next trigger ISO string in IST (Asia/Kolkata)."""
    try:
        combined_str = f"{trigger_date} {trigger_time}:00"
        dt = datetime.strptime(combined_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        now = now_ist()
        if dt > now:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        if repeat_interval == "daily":
            while dt <= now:
                dt += timedelta(days=1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        elif repeat_interval == "weekly":
            while dt <= now:
                dt += timedelta(weeks=1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{trigger_date} {trigger_time}:00"

def create_automation(name: str, event_slug: str, gdrive_url: str,
                      target_album_slug: str = "auto", formats_list: list = None,
                      trigger_date: str = "", trigger_time: str = "",
                      repeat_interval: str = "none", is_active: int = 1):
    conn = get_db()
    c = conn.cursor()
    formats_json = json.dumps(formats_list or ["jpg", "png", "webp"])
    next_trigger = compute_next_trigger(trigger_date, trigger_time, repeat_interval)
    c.execute("""
        INSERT INTO automations (
            name, event_slug, gdrive_url, target_album_slug, formats_json,
            trigger_date, trigger_time, repeat_interval, is_active, next_trigger_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, event_slug, gdrive_url, target_album_slug, formats_json,
          trigger_date, trigger_time, repeat_interval, is_active, next_trigger))
    auto_id = c.lastrowid
    conn.commit()
    conn.close()
    return auto_id

def get_automations(event_slug: str = None):
    conn = get_db()
    c = conn.cursor()
    if event_slug and event_slug not in {"all", "*"}:
        c.execute("""
            SELECT * FROM automations 
            WHERE event_slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')
            ORDER BY is_active DESC, next_trigger_at ASC, id DESC
        """, (event_slug, event_slug))
    else:
        c.execute("SELECT * FROM automations ORDER BY is_active DESC, next_trigger_at ASC, id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_automation_by_id(auto_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM automations WHERE id = ?", (auto_id,))
    r = c.fetchone()
    conn.close()
    return dict(r) if r else None

def update_automation(auto_id: int, name: str, gdrive_url: str,
                      target_album_slug: str, formats_list: list,
                      trigger_date: str, trigger_time: str,
                      repeat_interval: str, is_active: int):
    conn = get_db()
    c = conn.cursor()
    formats_json = json.dumps(formats_list or ["jpg", "png", "webp"])
    next_trigger = compute_next_trigger(trigger_date, trigger_time, repeat_interval)
    c.execute("""
        UPDATE automations
        SET name = ?, gdrive_url = ?, target_album_slug = ?, formats_json = ?,
            trigger_date = ?, trigger_time = ?, repeat_interval = ?,
            is_active = ?, next_trigger_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (name, gdrive_url, target_album_slug, formats_json,
          trigger_date, trigger_time, repeat_interval, is_active, next_trigger, auto_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def delete_automation(auto_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM automations WHERE id = ?", (auto_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def toggle_automation(auto_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE automations SET is_active = (1 - is_active), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (auto_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def record_automation_history(automation_id: int, automation_name: str, task_id: str,
                              status: str = "queued", files_count: int = 0,
                              result_message: str = "", error_details: str = None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO automation_history (
            automation_id, automation_name, task_id, status, files_count, result_message, error_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (automation_id, automation_name, task_id, status, files_count, result_message, error_details))
    conn.commit()
    conn.close()

def get_automation_history(automation_id: int = None, limit: int = 50):
    conn = get_db()
    c = conn.cursor()
    if automation_id:
        c.execute("""
            SELECT h.*, a.gdrive_url, a.target_album_slug
            FROM automation_history h
            LEFT JOIN automations a ON h.automation_id = a.id
            WHERE h.automation_id = ?
            ORDER BY h.id DESC
            LIMIT ?
        """, (automation_id, limit))
    else:
        c.execute("""
            SELECT h.*, a.gdrive_url, a.target_album_slug
            FROM automation_history h
            LEFT JOIN automations a ON h.automation_id = a.id
            ORDER BY h.id DESC
            LIMIT ?
        """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_due_automations(now_str: str):
    """Fetches active automations whose next_trigger_at is <= now_str in IST."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM automations
        WHERE is_active = 1 AND next_trigger_at IS NOT NULL AND next_trigger_at <= ?
        ORDER BY next_trigger_at ASC
    """, (now_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def is_automation_task_pending(gdrive_url: str, automation_name: str = "") -> bool:
    """Idempotency check: returns True if an active or queued task exists for this source or automation."""
    if not gdrive_url and not automation_name:
        return False
    conn = get_db()
    c = conn.cursor()
    if gdrive_url:
        c.execute("SELECT id FROM upload_queue WHERE status IN ('queued', 'running') AND source_info = ? LIMIT 1", (gdrive_url,))
    else:
        c.execute("SELECT id FROM upload_queue WHERE status IN ('queued', 'running') AND task_name LIKE ? LIMIT 1", (f"%{automation_name}%",))
    row = c.fetchone()
    conn.close()
    return row is not None

def advance_automation_schedule(auto_id: int, task_id: str, status: str = "queued"):
    """Advances schedule to the next interval or deactivates one-time automations."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM automations WHERE id = ?", (auto_id,))
    auto = c.fetchone()
    if not auto:
        conn.close()
        return
    repeat_interval = auto["repeat_interval"]
    now = now_ist()
    time_str = auto["trigger_time"]

    if repeat_interval == "daily":
        next_dt = now + timedelta(days=1)
        next_str = f"{next_dt.strftime('%Y-%m-%d')} {time_str}:00"
        c.execute("""
            UPDATE automations
            SET last_triggered_at = CURRENT_TIMESTAMP,
                last_status = ?,
                last_task_id = ?,
                trigger_date = ?,
                next_trigger_at = ?
            WHERE id = ?
        """, (status, task_id, next_dt.strftime('%Y-%m-%d'), next_str, auto_id))
    elif repeat_interval == "weekly":
        next_dt = now + timedelta(weeks=1)
        next_str = f"{next_dt.strftime('%Y-%m-%d')} {time_str}:00"
        c.execute("""
            UPDATE automations
            SET last_triggered_at = CURRENT_TIMESTAMP,
                last_status = ?,
                last_task_id = ?,
                trigger_date = ?,
                next_trigger_at = ?
            WHERE id = ?
        """, (status, task_id, next_dt.strftime('%Y-%m-%d'), next_str, auto_id))
    else:
        # One-time: mark inactive after execution
        c.execute("""
            UPDATE automations
            SET last_triggered_at = CURRENT_TIMESTAMP,
                last_status = ?,
                last_task_id = ?,
                is_active = 0,
                next_trigger_at = NULL
            WHERE id = ?
        """, (status, task_id, auto_id))
    conn.commit()
    conn.close()

    try:
        record_automation_history(
            automation_id=auto_id,
            automation_name=auto["name"],
            task_id=task_id,
            status=status,
            result_message=f"Triggered by scheduler and enqueued as {task_id}"
        )
    except Exception as e:
        print(f"Error auto-recording history: {e}")

# ---------------------------------------------------------------------------
# System Settings & AI Matching Configuration
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = None):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        return row["value"]
    return default

def set_setting(key: str, value: str, description: str = None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO system_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
    """, (key, str(value)))
    conn.commit()
    conn.close()

def get_all_settings() -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM system_settings")
    rows = c.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

# ---------------------------------------------------------------------------
# Indexing Errors & Diagnostics
# ---------------------------------------------------------------------------

def record_indexing_error(task_id: str, event_slug: str, photo_rel_path: str, error_stage: str, error_message: str):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO indexing_errors (task_id, event_slug, photo_rel_path, error_stage, error_message)
            VALUES (?, ?, ?, ?, ?)
        """, (task_id or "", event_slug or "brk-sir", photo_rel_path, error_stage, str(error_message)[:500]))
        conn.commit()
    except Exception as e:
        print(f"Error logging indexing error: {e}")
    finally:
        conn.close()

def get_indexing_errors(event_slug: str = None, limit: int = 100) -> list:
    conn = get_db()
    c = conn.cursor()
    if event_slug:
        c.execute("""
            SELECT id, task_id, event_slug, photo_rel_path, error_stage, error_message, created_at
            FROM indexing_errors
            WHERE event_slug = ?
            ORDER BY id DESC LIMIT ?
        """, (event_slug, limit))
    else:
        c.execute("""
            SELECT id, task_id, event_slug, photo_rel_path, error_stage, error_message, created_at
            FROM indexing_errors
            ORDER BY id DESC LIMIT ?
        """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

# ---------------------------------------------------------------------------
# Match Review & Human Feedback
# ---------------------------------------------------------------------------

def record_match_review(
    search_id: str,
    event_slug: str,
    query_crop_path: str,
    matched_photo_id: int,
    matched_rel_path: str,
    similarity_score: float,
    confidence_tier: str
):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO match_reviews (
                search_id, event_slug, query_crop_path, matched_photo_id,
                matched_rel_path, similarity_score, confidence_tier, feedback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (search_id, event_slug, query_crop_path, matched_photo_id, matched_rel_path, round(float(similarity_score), 4), confidence_tier))
        conn.commit()
    except Exception as e:
        print(f"Error recording match review: {e}")
    finally:
        conn.close()

def get_match_reviews(event_slug: str = "brk-sir", status: str = None, tier: str = None, limit: int = 100) -> list:
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT mr.*, p.filename, p.width, p.height, p.file_size_bytes, a.name as album_name
        FROM match_reviews mr
        LEFT JOIN photos p ON mr.matched_photo_id = p.id
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE (mr.event_slug = ? OR ? = '' OR mr.event_slug IS NULL)
    """
    params = [event_slug, event_slug]
    if status and status != "all":
        query += " AND mr.feedback = ?"
        params.append(status)
    if tier and tier != "all":
        query += " AND mr.confidence_tier = ?"
        params.append(tier)
    query += " ORDER BY mr.id DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def update_match_feedback(review_id: int, feedback: str, notes: str = None, reviewer_ip: str = None) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE match_reviews
        SET feedback = ?, notes = COALESCE(?, notes), reviewer_ip = COALESCE(?, reviewer_ip)
        WHERE id = ?
    """, (feedback, notes, reviewer_ip, review_id))
    rows_affected = c.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

# ---------------------------------------------------------------------------
# Duplicate Photo Management
# ---------------------------------------------------------------------------

def record_duplicate_pair(group_id: str, match_type: str, similarity_score: float, primary_photo_id: int, duplicate_photo_id: int):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR REPLACE INTO duplicate_groups (
                group_id, match_type, similarity_score, primary_photo_id, duplicate_photo_id, status
            ) VALUES (?, ?, ?, ?, ?, 'pending')
        """, (group_id, match_type, round(float(similarity_score), 4), primary_photo_id, duplicate_photo_id))
        conn.commit()
    except Exception as e:
        print(f"Error recording duplicate pair: {e}")
    finally:
        conn.close()

def get_duplicate_groups(event_slug: str = "brk-sir", status: str = "pending") -> list:
    conn = get_db()
    c = conn.cursor()
    status_filter = "AND dg.status = ?" if status != "all" else ""
    params = [event_slug, event_slug]
    if status != "all":
        params.append(status)

    c.execute(f"""
        SELECT DISTINCT dg.group_id, dg.match_type, dg.similarity_score, dg.status, dg.created_at
        FROM duplicate_groups dg
        JOIN photos p1 ON dg.primary_photo_id = p1.id
        JOIN events e ON p1.event_id = e.id
        WHERE (e.slug = ? OR ? = '') {status_filter}
        ORDER BY dg.id DESC
    """, params)
    group_rows = c.fetchall()

    result = []
    for gr in group_rows:
        gid = gr["group_id"]
        c.execute("""
            SELECT primary_photo_id, duplicate_photo_id, similarity_score, match_type
            FROM duplicate_groups
            WHERE group_id = ?
        """, (gid,))
        pairs = c.fetchall()
        photo_ids = set()
        for p in pairs:
            photo_ids.add(p["primary_photo_id"])
            photo_ids.add(p["duplicate_photo_id"])

        if not photo_ids:
            continue

        placeholders = ",".join("?" for _ in photo_ids)
        c.execute(f"""
            SELECT p.id, p.filename, p.rel_path, p.width, p.height, p.file_size_bytes,
                   p.blur_score, p.exposure_score, p.quality_grade, p.quality_flags,
                   p.captured_at, p.created_at, p.is_deleted,
                   a.name as album_name, a.slug as album_slug
            FROM photos p
            LEFT JOIN albums a ON p.album_id = a.id
            WHERE p.id IN ({placeholders}) AND p.is_deleted = 0
        """, list(photo_ids))
        photos = [dict(pr) for pr in c.fetchall()]
        if len(photos) < 2:
            continue

        def quality_rank(item):
            w = item.get("width") or 0
            h = item.get("height") or 0
            sz = item.get("file_size_bytes") or 0
            grade_weight = 0 if item.get("quality_grade") == "good" else (1 if item.get("quality_grade") == "acceptable" else 2)
            return (-(w * h), -sz, grade_weight)

        photos.sort(key=quality_rank)
        rec_id = photos[0]["id"]
        for p in photos:
            p["is_recommended"] = (p["id"] == rec_id)
            p["recommended_original"] = (p["id"] == rec_id)

        result.append({
            "group_id": gid,
            "match_type": gr["match_type"],
            "similarity_score": round(float(gr["similarity_score"]), 3),
            "status": gr["status"],
            "created_at": gr["created_at"],
            "recommended_photo_id": rec_id,
            "photos": photos
        })

    conn.close()
    return result

def resolve_duplicate_group(group_id: str, action: str, photo_ids: list = None) -> dict:
    conn = get_db()
    c = conn.cursor()
    if action == "ignore":
        c.execute("UPDATE duplicate_groups SET status = 'ignored' WHERE group_id = ?", (group_id,))
        conn.commit()
        conn.close()
        return {"status": "ok", "message": "Duplicate group ignored."}

    if action in ("delete_duplicates", "delete_selected"):
        if not photo_ids:
            c.execute("SELECT duplicate_photo_id FROM duplicate_groups WHERE group_id = ?", (group_id,))
            photo_ids = [r["duplicate_photo_id"] for r in c.fetchall()]

        if photo_ids:
            placeholders = ",".join("?" for _ in photo_ids)
            c.execute(f"""
                UPDATE photos
                SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
            """, photo_ids)
        c.execute("UPDATE duplicate_groups SET status = 'resolved' WHERE group_id = ?", (group_id,))
        conn.commit()
        conn.close()
        return {"status": "ok", "deleted_count": len(photo_ids or []), "message": f"{len(photo_ids or [])} duplicate(s) moved to Trash."}

    conn.close()
    return {"status": "error", "message": "Unknown action."}

if __name__ == "__main__":
    init_db()
    migrate_existing_data()
    print("Database initialized and existing data migrated successfully.")

