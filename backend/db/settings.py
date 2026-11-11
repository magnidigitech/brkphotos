from backend.db.connection import get_db

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
