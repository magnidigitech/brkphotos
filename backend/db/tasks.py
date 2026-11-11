import threading
import time
from datetime import datetime
from backend.db.connection import get_db

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

