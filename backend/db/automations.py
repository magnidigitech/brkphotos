import json
from datetime import datetime, timedelta
from backend.db.connection import IST, now_ist, get_db

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
