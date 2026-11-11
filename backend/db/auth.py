import sqlite3
import json
import time
import hashlib
import logging
from typing import Optional, List, Union
from backend.db.connection import get_db

logger = logging.getLogger("uvicorn.error")

def get_admin_user(identifier = 1) -> Optional[dict]:
    conn = get_db()
    try:
        c = conn.cursor()
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            c.execute("SELECT * FROM admin_users WHERE id = ?", (int(identifier),))
        else:
            c.execute("SELECT * FROM admin_users WHERE username = ?", (str(identifier),))
        row = c.fetchone()
        if not row:
            return None
        d = dict(row)
        if "totp_secret_enc" in d and "totp_secret" not in d:
            d["totp_secret"] = d["totp_secret_enc"]
        return d
    finally:
        conn.close()

def update_admin_password(user_id: int, new_password_or_hash: str) -> bool:
    import auth_security
    if not new_password_or_hash.startswith("pbkdf2_sha256$"):
        pwd_hash = auth_security.hash_password(new_password_or_hash)
    else:
        pwd_hash = new_password_or_hash
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("UPDATE admin_users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pwd_hash, user_id))
        return True
    finally:
        conn.close()

def set_totp_state(user_id: int, enabled: bool, secret_enc: Optional[str] = None) -> bool:
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            if secret_enc is not None:
                c.execute("""
                    UPDATE admin_users
                    SET totp_enabled = ?, totp_secret_enc = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (1 if enabled else 0, secret_enc, user_id))
            else:
                c.execute("""
                    UPDATE admin_users
                    SET totp_enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (1 if enabled else 0, user_id))
        return True
    finally:
        conn.close()

def save_recovery_codes(user_id: int, codes: List[str]) -> bool:
    import auth_security
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("DELETE FROM admin_recovery_codes WHERE user_id = ?", (user_id,))
            for code in codes:
                if len(code) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in code):
                    h = code.lower()
                else:
                    h = auth_security.hash_recovery_code(code)
                c.execute("INSERT INTO admin_recovery_codes (user_id, code_hash, is_used) VALUES (?, ?, 0)", (user_id, h))
        return True
    finally:
        conn.close()

def verify_and_burn_recovery_code(user_id: int, code: str) -> bool:
    import auth_security
    code_hash = auth_security.hash_recovery_code(code)
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("""
                SELECT id FROM admin_recovery_codes
                WHERE user_id = ? AND code_hash = ? AND is_used = 0
                LIMIT 1
            """, (user_id, code_hash))
            row = c.fetchone()
            if not row:
                return False
            rec_id = row["id"]
            c.execute("UPDATE admin_recovery_codes SET is_used = 1, used_at = CURRENT_TIMESTAMP WHERE id = ?", (rec_id,))
        return True
    finally:
        conn.close()

def get_recovery_codes_status(user_id: int = 1) -> dict:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(id) as total, SUM(CASE WHEN is_used = 0 THEN 1 ELSE 0 END) as unused FROM admin_recovery_codes WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        total = (row["total"] or 0) if row else 0
        unused = (row["unused"] or 0) if row else 0
        return {
            "total": total,
            "unused": unused,
            "total_count": total,
            "unused_count": unused,
            "used": total - unused
        }
    finally:
        conn.close()

def save_passkey(user_id: int, credential_id: str, public_key: str, device_name: str, transports: str = "", aaguid: str = "", **kwargs) -> bool:
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO admin_passkeys (user_id, credential_id, public_key, device_name, transports, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, credential_id, public_key, device_name, transports))
        return True
    finally:
        conn.close()

def get_user_passkeys(user_id: int = 1) -> List[dict]:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, credential_id, device_name, transports, sign_count, created_at, last_used_at
            FROM admin_passkeys
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,))
        return [dict(r) for r in c.fetchall()]
    finally:
        conn.close()

def get_passkey_by_credential_id(credential_id: str) -> Optional[dict]:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM admin_passkeys WHERE credential_id = ? LIMIT 1", (credential_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_passkey_last_used(identifier: Union[int, str], sign_count: int = 0) -> bool:
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            if isinstance(identifier, int) or (isinstance(identifier, str) and str(identifier).isdigit()):
                c.execute("UPDATE admin_passkeys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?", (int(identifier),))
            else:
                c.execute("UPDATE admin_passkeys SET last_used_at = CURRENT_TIMESTAMP WHERE credential_id = ?", (str(identifier),))
        return True
    finally:
        conn.close()

def delete_passkey(arg1: Union[int, str], arg2: Optional[Union[int, str]] = None) -> bool:
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            if arg2 is not None:
                c.execute("""
                    DELETE FROM admin_passkeys
                    WHERE (id = ? AND user_id = ?)
                       OR (id = ? AND user_id = ?)
                       OR credential_id = ?
                       OR credential_id = ?
                """, (arg1, arg2, arg2, arg1, str(arg1), str(arg2)))
            else:
                c.execute("DELETE FROM admin_passkeys WHERE id = ? OR credential_id = ?", (arg1, str(arg1)))
            rows = c.rowcount
        return rows > 0
    finally:
        conn.close()

def rename_passkey(arg1: int, arg2: Union[int, str], arg3: Optional[str] = None) -> bool:
    if isinstance(arg2, str):
        passkey_id = int(arg1)
        new_name = arg2
    else:
        passkey_id = int(arg1) if isinstance(arg3, str) else int(arg2)
        new_name = str(arg3 or "")
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("UPDATE admin_passkeys SET device_name = ? WHERE id = ?", (new_name[:64], passkey_id))
            rows = c.rowcount
        return rows > 0
    finally:
        conn.close()

def save_auth_challenge(arg1, arg2=None, arg3=None, user_id: int = 1, data: dict = None, ttl_seconds: int = 300, expires_in_seconds: int = 300) -> bool:
    conn = get_db()
    try:
        now = time.time()
        ttl = expires_in_seconds if expires_in_seconds != 300 else ttl_seconds
        expires_at = now + ttl

        if arg2 in ("2fa_login", "passkey_login", "passkey_register"):
            challenge_id = str(arg1)
            challenge_type = str(arg2)
            challenge_val = str(arg3) if arg3 else ""
        elif arg1 in ("2fa_login", "passkey_login", "passkey_register"):
            challenge_type = str(arg1)
            challenge_id = str(arg2)
            challenge_val = str(arg3) if arg3 else ""
        else:
            challenge_id = str(arg1)
            challenge_type = str(arg2 or "generic")
            challenge_val = str(arg3) if arg3 else ""

        data_payload = data if isinstance(data, dict) else {}
        if challenge_val:
            data_payload["challenge"] = challenge_val

        data_str = json.dumps(data_payload)
        with conn:
            c = conn.cursor()
            c.execute("DELETE FROM auth_challenges WHERE expires_at < ?", (now,))
            c.execute("""
                INSERT INTO auth_challenges (challenge_type, challenge_id, user_id, data_json, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (challenge_type, challenge_id, user_id, data_str, expires_at))
        return True
    finally:
        conn.close()

def consume_auth_challenge(challenge_id: str, expected_type: Optional[str] = None) -> Optional[dict]:
    conn = get_db()
    try:
        now = time.time()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM auth_challenges
            WHERE challenge_id = ? AND expires_at >= ?
            LIMIT 1
        """, (challenge_id, now))
        row = c.fetchone()
        if not row:
            return None
        d = dict(row)
        if expected_type and d.get("challenge_type") and d["challenge_type"] != expected_type:
            return None
        with conn:
            c.execute("DELETE FROM auth_challenges WHERE challenge_id = ?", (challenge_id,))
        if d.get("data_json"):
            try:
                extra = json.loads(d["data_json"])
                if isinstance(extra, dict):
                    d.update(extra)
            except Exception:
                pass
        return d
    finally:
        conn.close()

def create_admin_session(token_or_hash: str, user_id: int = 1, ip: str = "", ip_address: str = "", ua: str = "", user_agent: str = "", duration_days: int = 7, expires_days: int = 7) -> bool:
    if len(token_or_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in token_or_hash):
        t_hash = token_or_hash.lower()
    else:
        t_hash = hashlib.sha256(token_or_hash.encode("utf-8")).hexdigest()

    client_ip = ip_address or ip
    client_ua = user_agent or ua
    days = expires_days if expires_days != 7 else duration_days

    conn = get_db()
    try:
        import datetime
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
        expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO admin_sessions (token_hash, user_id, ip_address, user_agent, expires_at, is_revoked)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (t_hash, user_id, client_ip, client_ua[:250], expires_str))
        return True
    finally:
        conn.close()

def validate_admin_session(token_or_hash: str) -> Optional[dict]:
    if len(token_or_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in token_or_hash):
        t_hash = token_or_hash.lower()
    else:
        t_hash = hashlib.sha256(token_or_hash.encode("utf-8")).hexdigest()

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT s.*, u.username, u.totp_enabled
            FROM admin_sessions s
            JOIN admin_users u ON s.user_id = u.id
            WHERE s.token_hash = ? AND s.expires_at > CURRENT_TIMESTAMP AND (s.is_revoked = 0 OR s.is_revoked IS NULL)
            LIMIT 1
        """, (t_hash,))
        row = c.fetchone()
        if row:
            try:
                with conn:
                    c.execute("UPDATE admin_sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            except Exception:
                pass
            return dict(row)
        return None
    except sqlite3.OperationalError as op_err:
        logger.error(f"validate_admin_session schema error: {op_err}")
        raise
    finally:
        conn.close()

def revoke_admin_session(token_or_hash: str) -> bool:
    if len(token_or_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in token_or_hash):
        t_hash = token_or_hash.lower()
    else:
        t_hash = hashlib.sha256(token_or_hash.encode("utf-8")).hexdigest()

    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            c.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (t_hash,))
        return True
    finally:
        conn.close()

def revoke_all_admin_sessions(user_id: int = 1, keep_token_hash: Optional[str] = None) -> int:
    conn = get_db()
    try:
        with conn:
            c = conn.cursor()
            if keep_token_hash:
                c.execute("DELETE FROM admin_sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep_token_hash))
            else:
                c.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))
            count = c.rowcount
        return count
    finally:
        conn.close()

def get_active_admin_sessions(user_id: int = 1) -> List[dict]:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, token_hash, ip_address, user_agent, created_at, expires_at, last_active_at
            FROM admin_sessions
            WHERE user_id = ? AND expires_at > CURRENT_TIMESTAMP AND (is_revoked = 0 OR is_revoked IS NULL)
            ORDER BY last_active_at DESC
        """, (user_id,))
        return [dict(r) for r in c.fetchall()]
    except sqlite3.OperationalError as op_err:
        logger.error(f"get_active_admin_sessions schema error: {op_err}")
        raise
    finally:
        conn.close()

def record_security_event(event_type: str, details_or_ip: str = "", ua: str = "", status: str = "success", details: str = "", ip: str = "", user_agent: str = ""):
    conn = get_db()
    try:
        if ip:
            client_ip = ip
            event_details = details or details_or_ip
        else:
            client_ip = details_or_ip
            event_details = details
        client_ua = user_agent or ua
        with conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO security_logs (event_type, ip_address, user_agent, status, details)
                VALUES (?, ?, ?, ?, ?)
            """, (event_type, client_ip, client_ua[:250], status, str(event_details)[:500]))
    except Exception as e:
        logger.error(f"Error logging security event: {type(e).__name__}")
    finally:
        conn.close()

def get_security_events(limit: int = 50) -> List[dict]:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, event_type, ip_address, status, details, created_at
            FROM security_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in c.fetchall()]
    except Exception as e:
        logger.error(f"get_security_events database exception: {type(e).__name__}")
        return []
    finally:
        conn.close()

def count_recent_failed_logins(ip: str, minutes: int = 5, window_minutes: Optional[int] = None) -> int:
    eff_minutes = window_minutes if window_minutes is not None else minutes
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(id)
            FROM security_logs
            WHERE event_type = 'login_failed' AND ip_address = ?
              AND created_at >= datetime('now', '-' || ? || ' minutes')
        """, (ip, eff_minutes))
        row = c.fetchone()
        return (row[0] or 0) if row else 0
    except Exception as e:
        logger.error(f"count_recent_failed_logins database exception: {type(e).__name__}")
        return 0
    finally:
        conn.close()
