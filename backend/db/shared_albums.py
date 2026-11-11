import json
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from backend.db.connection import get_db

def save_search_result_proof(search_id: str, event_id: int, matched_photo_ids: List[int], creator_session_id: str = "", ttl_seconds: int = 3600) -> tuple[str, str]:
    """
    Saves an expiring server-side proof of verified matched photo IDs for a search session.
    Returns (search_id, creation_token). The creation_token must be provided to create a shared album.
    """
    if not search_id:
        search_id = f"srch_{secrets.token_hex(12)}"
    creation_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(creation_token.encode("utf-8")).hexdigest()

    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    clean_ids = sorted(list(set(int(pid) for pid in matched_photo_ids if pid)))
    matched_json = json.dumps(clean_ids)

    conn = get_db()
    c = conn.cursor()
    try:
        # Periodic cleanup of expired proofs older than 2 hours
        c.execute("DELETE FROM search_result_proofs WHERE expires_at < datetime('now', '-2 hours')")
        c.execute("""
            INSERT INTO search_result_proofs (
                search_id, event_id, creator_session_id, creation_token_hash,
                matched_photo_ids_json, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(search_id) DO UPDATE SET
                event_id = excluded.event_id,
                creator_session_id = excluded.creator_session_id,
                creation_token_hash = excluded.creation_token_hash,
                matched_photo_ids_json = excluded.matched_photo_ids_json,
                expires_at = excluded.expires_at
        """, (search_id, event_id, creator_session_id or "", token_hash, matched_json, expires_at))
        conn.commit()
    finally:
        conn.close()

    return search_id, creation_token


def get_search_result_proof(search_id: str) -> Optional[dict]:
    """Retrieves an active search-result proof by search_id if not expired."""
    if not search_id:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM search_result_proofs
        WHERE search_id = ? AND expires_at > datetime('now')
        LIMIT 1
    """, (search_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["matched_photo_ids"] = json.loads(d["matched_photo_ids_json"])
    except Exception:
        d["matched_photo_ids"] = []
    return d


def create_shared_album_atomic(
    search_id: str,
    creation_token: str,
    photo_ids: List[int],
    title: Optional[str] = None,
    expiry_days: int = 30,
    creator_session: str = "",
    share_token: Optional[str] = None,
    manage_token: Optional[str] = None
) -> dict:
    """
    Atomically verifies search-result proof, rechecks event/photo permissions,
    creates the shared album and photo links in a single SQLite transaction,
    and returns independent share and management tokens.
    Stores ONLY hashes server-side.
    Handles idempotent retries cleanly without duplicating rows or leaking tokens.
    """
    if not search_id or not creation_token:
        raise ValueError("Valid search_id and creation_token are required.")

    token_hash = hashlib.sha256(creation_token.encode("utf-8")).hexdigest()

    conn = get_db()
    c = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 1. Fetch active proof
        c.execute("""
            SELECT p.*, e.name as event_name, e.slug as event_slug, e.status as event_status
            FROM search_result_proofs p
            JOIN events e ON p.event_id = e.id
            WHERE p.search_id = ?
            LIMIT 1
        """, (search_id,))
        row = c.fetchone()
        if not row:
            raise ValueError("Search result proof not found or expired. Please search again.")

        now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if row["expires_at"] <= now_utc_str:
            raise ValueError("Search result proof has expired. Please perform a new search.")

        if row["creation_token_hash"] != token_hash:
            raise PermissionError("Invalid creation token for this search proof.")

        # 2. Check Event Visibility Status
        if row["event_status"] != "published":
            raise PermissionError("The event is not currently published. Shared albums cannot be created.")

        event_id = row["event_id"]
        event_name = row["event_name"]

        # 3. Authorisation check: all requested IDs must be in matched_photo_ids_json
        try:
            authorized_ids = set(json.loads(row["matched_photo_ids_json"]))
        except Exception:
            authorized_ids = set()

        if not photo_ids or not isinstance(photo_ids, list):
            raise ValueError("No photos selected for album.")

        clean_ids = sorted(list(set(int(pid) for pid in photo_ids if pid)))
        if not clean_ids:
            raise ValueError("No valid photo IDs provided.")

        # Enforce configurable maximum photo limit (default 100)
        from backend.db.settings import get_setting
        try:
            max_limit = int(get_setting("max_shared_album_photos", "100"))
        except Exception:
            max_limit = 100
        max_limit = max(1, min(max_limit, 500))

        if len(clean_ids) > max_limit:
            raise ValueError(f"Selected photos count ({len(clean_ids)}) exceeds maximum allowed limit of {max_limit} photos.")

        # Check subset authorisation: reject entire selection if ANY photo was not matched
        requested_set = set(clean_ids)
        if not requested_set.issubset(authorized_ids):
            unauth_count = len(requested_set - authorized_ids)
            raise PermissionError(f"Unauthorized photo selection: {unauth_count} photo(s) are not part of verified search matches.")

        # 4. Live Permission Recheck: Ensure photos currently exist, belong to this event, and are neither deleted nor hidden
        placeholders = ",".join("?" for _ in clean_ids)
        c.execute(f"""
            SELECT id FROM photos
            WHERE id IN ({placeholders})
              AND event_id = ?
              AND is_deleted = 0
              AND is_hidden = 0
        """, clean_ids + [event_id])
        valid_rows = c.fetchall()
        valid_ids = {r["id"] for r in valid_rows}
        if len(valid_ids) != len(requested_set):
            missing_count = len(requested_set - valid_ids)
            raise ValueError(f"{missing_count} selected photo(s) are no longer accessible or have been removed.")

        # 5. Sanitize Title & Expiry Bounds (Enforce lifetime max 90 days, default 30)
        clean_title = (title or "").strip()
        if not clean_title:
            clean_title = f"{event_name} — My Moments"
        clean_title = clean_title[:100]

        clamped_expiry_days = max(1, min(int(expiry_days or 30), 90))
        now_dt = datetime.now(timezone.utc)
        created_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_str = (now_dt + timedelta(days=clamped_expiry_days)).strftime("%Y-%m-%d %H:%M:%S")

        # 6. Validate or Generate independent 256-bit credentials
        if share_token:
            share_token = str(share_token).strip()
            if len(share_token) < 32 or len(share_token) > 100:
                raise ValueError("Invalid share_token length. Must provide at least 256 bits of entropy.")
        else:
            share_token = f"sh_{secrets.token_hex(32)}"

        if manage_token:
            manage_token = str(manage_token).strip()
            if len(manage_token) < 32 or len(manage_token) > 100:
                raise ValueError("Invalid manage_token length. Must provide at least 256 bits of entropy.")
        else:
            manage_token = f"mg_{secrets.token_hex(32)}"

        if share_token == manage_token:
            raise ValueError("Share and management credentials must be distinct.")

        share_hash = hashlib.sha256(share_token.encode("utf-8")).hexdigest()
        manage_hash = hashlib.sha256(manage_token.encode("utf-8")).hexdigest()

        # Normalized request representation for idempotency binding
        norm_req_dict = {
            "search_id": search_id,
            "event_id": event_id,
            "photo_ids": clean_ids,
            "title": clean_title,
            "expiry_days": clamped_expiry_days,
            "share_hash": share_hash,
            "manage_hash": manage_hash
        }
        req_hash = hashlib.sha256(json.dumps(norm_req_dict, sort_keys=True).encode("utf-8")).hexdigest()

        # 7. Idempotency Check: if an album was already created for this exact proof
        if row["created_album_id"]:
            c.execute("SELECT * FROM shared_albums WHERE id = ?", (row["created_album_id"],))
            ex_alb = c.fetchone()
            if ex_alb:
                stored_req_hash = row["request_hash"] if "request_hash" in row.keys() else None
                # Verify caller provided identical parameters and credentials
                if (stored_req_hash == req_hash or stored_req_hash is None) and \
                   ex_alb["share_token_hash"] == share_hash and \
                   ex_alb["manage_token_hash"] == manage_hash:
                    return {
                        "id": ex_alb["id"],
                        "share_token": share_token,
                        "manage_token": manage_token,
                        "title": ex_alb["title"],
                        "photo_count": ex_alb["photo_count"],
                        "expires_at": ex_alb["expires_at"],
                        "created_at": ex_alb["created_at"],
                        "is_idempotent_retry": True
                    }
                else:
                    raise ValueError("This creation proof has already been consumed for a different album selection or credentials.")

        # 8. Insert into shared_albums (ONLY hashes stored)
        c.execute("""
            INSERT INTO shared_albums (
                event_id, share_token_hash, manage_token_hash, title,
                photo_count, max_photos_allowed, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id, share_hash, manage_hash, clean_title,
            len(clean_ids), max_limit, created_str, expires_str
        ))
        album_id = c.lastrowid

        # 9. Insert ordered photo links
        photo_tuples = [(album_id, pid, idx + 1, created_str) for idx, pid in enumerate(clean_ids)]
        c.executemany("""
            INSERT INTO shared_album_photos (shared_album_id, photo_id, sort_order, created_at)
            VALUES (?, ?, ?, ?)
        """, photo_tuples)

        # 10. Bind proof to created album and store request hash (NO plaintext tokens stored!)
        c.execute("""
            UPDATE search_result_proofs
            SET created_album_id = ?, request_hash = ?
            WHERE search_id = ?
        """, (album_id, req_hash, search_id))

        conn.commit()

        return {
            "id": album_id,
            "share_token": share_token,
            "manage_token": manage_token,
            "title": clean_title,
            "photo_count": len(clean_ids),
            "created_at": created_str,
            "expires_at": expires_str,
            "is_idempotent_retry": False
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_shared_album_by_token(share_token: str) -> Optional[dict]:
    """
    Retrieves a shared album by share_token hash.
    Checks expiry, revocation, and event visibility.
    Returns status: 'active', 'expired', 'revoked', or 'not_found'.
    """
    if not share_token:
        return None
    token_hash = hashlib.sha256(share_token.encode("utf-8")).hexdigest()

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT a.*, e.name as event_name, e.slug as event_slug, e.status as event_status
        FROM shared_albums a
        JOIN events e ON a.event_id = e.id
        WHERE a.share_token_hash = ?
        LIMIT 1
    """, (token_hash,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"status": "not_found", "message": "Album not found."}

    d = dict(row)
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Check revocation (derived directly from revoked_at)
    if d.get("revoked_at") is not None:
        conn.close()
        return {
            "status": "revoked",
            "revoked_at": d["revoked_at"],
            "title": d["title"],
            "event_name": d["event_name"],
            "message": "This album has been revoked by its creator."
        }

    # Check expiry
    if d["expires_at"] <= now_utc_str:
        conn.close()
        return {
            "status": "expired",
            "expires_at": d["expires_at"],
            "title": d["title"],
            "event_name": d["event_name"],
            "message": "This album link has expired."
        }

    # Check event status
    if d["event_status"] != "published":
        conn.close()
        return {
            "status": "unavailable",
            "message": "This event is currently archived or private."
        }

    # Record view timestamp & increment counter debounced
    try:
        c.execute("""
            UPDATE shared_albums
            SET view_count = view_count + 1, last_viewed_at = ?
            WHERE id = ?
        """, (now_utc_str, d["id"]))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    d["status"] = "active"
    # Never leak internal hashes to callers
    d.pop("share_token_hash", None)
    d.pop("manage_token_hash", None)
    return d


def get_shared_album_photos_page(shared_album_id: int, page: int = 1, page_size: int = 50) -> dict:
    """
    Returns ordered photo items for a shared album without exposing internal file paths.
    Only includes photos where is_deleted = 0 and is_hidden = 0.
    """
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT COUNT(sap.id) as total
        FROM shared_album_photos sap
        JOIN photos p ON sap.photo_id = p.id
        WHERE sap.shared_album_id = ? AND p.is_deleted = 0 AND p.is_hidden = 0
    """, (shared_album_id,))
    total_count = c.fetchone()["total"] or 0

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    c.execute("""
        SELECT p.id, p.filename, p.width, p.height, p.captured_at, p.created_at, sap.sort_order
        FROM shared_album_photos sap
        JOIN photos p ON sap.photo_id = p.id
        WHERE sap.shared_album_id = ? AND p.is_deleted = 0 AND p.is_hidden = 0
        ORDER BY sap.sort_order ASC, sap.id ASC
        LIMIT ? OFFSET ?
    """, (shared_album_id, page_size, offset))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    has_more = (offset + len(rows)) < total_count
    return {
        "photos": rows,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": (page + 1) if has_more else None
    }


def verify_photo_in_shared_album(share_token: str, photo_id: int) -> Optional[dict]:
    """
    Verifies that the photo belongs to an active, unrevoked, unexpired shared album.
    Returns the internal photo row (with rel_path) if valid, or None.
    """
    if not share_token or not photo_id:
        return None
    token_hash = hashlib.sha256(share_token.encode("utf-8")).hexdigest()
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.filename, p.rel_path, p.width, p.height, a.id as album_id, a.title as album_title
        FROM shared_albums a
        JOIN events e ON a.event_id = e.id
        JOIN shared_album_photos sap ON a.id = sap.shared_album_id
        JOIN photos p ON sap.photo_id = p.id
        WHERE a.share_token_hash = ?
          AND a.revoked_at IS NULL
          AND a.expires_at > ?
          AND e.status = 'published'
          AND sap.photo_id = ?
          AND p.is_deleted = 0
          AND p.is_hidden = 0
        LIMIT 1
    """, (token_hash, now_utc_str, photo_id))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_photo_records_for_shared_album(share_token: str, requested_ids: Optional[List[int]] = None) -> Optional[List[dict]]:
    """
    Verifies token and retrieves full photo records for ZIP streaming.
    Only returns photos that belong to the album and are currently active.
    """
    if not share_token:
        return None
    token_hash = hashlib.sha256(share_token.encode("utf-8")).hexdigest()
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    c = conn.cursor()

    # 1. Verify active album
    c.execute("""
        SELECT a.id, a.title, e.status as event_status
        FROM shared_albums a
        JOIN events e ON a.event_id = e.id
        WHERE a.share_token_hash = ?
          AND a.revoked_at IS NULL
          AND a.expires_at > ?
          AND e.status = 'published'
        LIMIT 1
    """, (token_hash, now_utc_str))
    alb_row = c.fetchone()
    if not alb_row:
        conn.close()
        return None

    shared_album_id = alb_row["id"]

    # 2. Fetch photos
    if requested_ids and isinstance(requested_ids, list):
        clean_ids = [int(x) for x in requested_ids if x]
        if not clean_ids:
            conn.close()
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        c.execute(f"""
            SELECT p.id, p.filename, p.rel_path, p.file_size_bytes, sap.sort_order
            FROM shared_album_photos sap
            JOIN photos p ON sap.photo_id = p.id
            WHERE sap.shared_album_id = ?
              AND sap.photo_id IN ({placeholders})
              AND p.is_deleted = 0
              AND p.is_hidden = 0
            ORDER BY sap.sort_order ASC
        """, [shared_album_id] + clean_ids)
    else:
        c.execute("""
            SELECT p.id, p.filename, p.rel_path, p.file_size_bytes, sap.sort_order
            FROM shared_album_photos sap
            JOIN photos p ON sap.photo_id = p.id
            WHERE sap.shared_album_id = ?
              AND p.is_deleted = 0
              AND p.is_hidden = 0
            ORDER BY sap.sort_order ASC
        """, (shared_album_id,))

    rows = [dict(r) for r in c.fetchall()]

    # Increment download counter
    try:
        c.execute("UPDATE shared_albums SET download_count = download_count + 1 WHERE id = ?", (shared_album_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return rows


def revoke_shared_album_by_creator(album_id: Optional[int] = None, manage_token: Optional[str] = None, *args, **kwargs) -> bool:
    """
    Revokes a shared album using creator's management token and internal album ID.
    Viewing credentials (share_token) cannot authorise management.
    """
    if not album_id and "album_id" in kwargs:
        album_id = kwargs["album_id"]
    if not manage_token and "manage_token" in kwargs:
        manage_token = kwargs["manage_token"]

    share_token = kwargs.get("share_token")

    if not album_id and not manage_token and args:
        if len(args) == 1:
            if isinstance(args[0], int) or (isinstance(args[0], str) and str(args[0]).isdigit()):
                album_id = int(args[0])
            else:
                manage_token = str(args[0])
        elif len(args) >= 2:
            if isinstance(args[0], int) or (isinstance(args[0], str) and str(args[0]).isdigit()):
                album_id = int(args[0])
                manage_token = str(args[1])
            elif isinstance(args[1], int) or (isinstance(args[1], str) and str(args[1]).isdigit()):
                album_id = int(args[1])
                manage_token = str(args[0])
            else:
                if str(args[0]).startswith("mg_"):
                    manage_token = str(args[0])
                    share_token = str(args[1])
                elif str(args[1]).startswith("mg_"):
                    manage_token = str(args[1])
                    share_token = str(args[0])
                else:
                    manage_token = str(args[1])
                    share_token = str(args[0])

    if not manage_token:
        return False

    manage_hash = hashlib.sha256(str(manage_token).encode("utf-8")).hexdigest()
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    c = conn.cursor()
    if album_id is not None:
        c.execute("""
            UPDATE shared_albums
            SET revoked_at = ?
            WHERE id = ? AND manage_token_hash = ? AND revoked_at IS NULL
        """, (now_utc_str, album_id, manage_hash))
    elif share_token:
        share_hash = hashlib.sha256(str(share_token).encode("utf-8")).hexdigest()
        c.execute("""
            UPDATE shared_albums
            SET revoked_at = ?
            WHERE share_token_hash = ? AND manage_token_hash = ? AND revoked_at IS NULL
        """, (now_utc_str, share_hash, manage_hash))
    else:
        c.execute("""
            UPDATE shared_albums
            SET revoked_at = ?
            WHERE manage_token_hash = ? AND revoked_at IS NULL
        """, (now_utc_str, manage_hash))
    affected = c.rowcount

    # Fallback if positional order was reversed and no prefix was present
    if affected == 0 and album_id is None and len(args) >= 2 and not kwargs.get("manage_token"):
        alt_manage = args[0] if manage_token == args[1] else args[1]
        alt_share = args[1] if alt_manage == args[0] else args[0]
        alt_manage_hash = hashlib.sha256(str(alt_manage).encode("utf-8")).hexdigest()
        alt_share_hash = hashlib.sha256(str(alt_share).encode("utf-8")).hexdigest()
        c.execute("""
            UPDATE shared_albums
            SET revoked_at = ?
            WHERE share_token_hash = ? AND manage_token_hash = ? AND revoked_at IS NULL
        """, (now_utc_str, alt_share_hash, alt_manage_hash))
        affected = c.rowcount

    conn.commit()
    conn.close()
    return affected > 0


def admin_get_shared_albums(page: int = 1, page_size: int = 50, status_filter: Optional[str] = None) -> dict:
    """Returns paginated shared albums for administrator review."""
    conn = get_db()
    c = conn.cursor()

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    where_clause = "1=1"
    params = []

    if status_filter == "active":
        where_clause += " AND a.revoked_at IS NULL AND a.expires_at > ?"
        params.append(now_utc_str)
    elif status_filter == "revoked":
        where_clause += " AND a.revoked_at IS NOT NULL"
    elif status_filter == "expired":
        where_clause += " AND a.revoked_at IS NULL AND a.expires_at <= ?"
        params.append(now_utc_str)

    c.execute(f"SELECT COUNT(a.id) as total FROM shared_albums a WHERE {where_clause}", params)
    total = c.fetchone()["total"] or 0

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    c.execute(f"""
        SELECT a.id, a.title, a.photo_count, a.view_count, a.download_count,
               a.created_at, a.expires_at, a.revoked_at, a.last_viewed_at,
               e.name as event_name, e.slug as event_slug,
               CASE
                   WHEN a.revoked_at IS NOT NULL THEN 'revoked'
                   WHEN a.expires_at <= ? THEN 'expired'
                   ELSE 'active'
               END as computed_status
        FROM shared_albums a
        JOIN events e ON a.event_id = e.id
        WHERE {where_clause}
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
    """, [now_utc_str] + params + [page_size, offset])

    rows = [dict(r) for r in c.fetchall()]

    # Global summary metrics for admin dashboard cards
    c.execute("""
        SELECT
            COUNT(id) as total_albums,
            SUM(CASE WHEN revoked_at IS NULL AND expires_at > ? THEN 1 ELSE 0 END) as active_albums,
            SUM(CASE WHEN revoked_at IS NULL AND expires_at <= ? THEN 1 ELSE 0 END) as expired_albums,
            SUM(CASE WHEN revoked_at IS NOT NULL THEN 1 ELSE 0 END) as revoked_albums
        FROM shared_albums
    """, (now_utc_str, now_utc_str))
    m = c.fetchone()
    metrics = {
        "total_albums": m["total_albums"] or 0,
        "active_albums": m["active_albums"] or 0,
        "expired_albums": m["expired_albums"] or 0,
        "revoked_albums": m["revoked_albums"] or 0,
    }

    conn.close()

    return {
        "albums": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "metrics": metrics
    }


def admin_revoke_shared_album(album_id: int) -> bool:
    """Admin override to revoke any shared album."""
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE shared_albums
        SET revoked_at = ?
        WHERE id = ? AND revoked_at IS NULL
    """, (now_utc_str, album_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def admin_update_shared_album_expiry(album_id: int, new_expiry_days: int) -> Optional[str]:
    """Admin override to adjust album expiry duration up to 90 days from creation."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT created_at FROM shared_albums WHERE id = ?", (album_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None

    try:
        created_dt = datetime.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        created_dt = datetime.now(timezone.utc)

    days = max(1, min(int(new_expiry_days), 90))
    new_expires_dt = created_dt + timedelta(days=days)
    new_expires_str = new_expires_dt.strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        UPDATE shared_albums
        SET expires_at = ?
        WHERE id = ?
    """, (new_expires_str, album_id))
    conn.commit()
    conn.close()
    return new_expires_str


def admin_delete_shared_album(album_id: int) -> bool:
    """Deletes shared album mapping and logs. Original photos are strictly preserved."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM shared_albums WHERE id = ?", (album_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def admin_get_shared_album_photos(album_id: int) -> List[dict]:
    """Returns photos in a shared album for authenticated admin inspection."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.filename, p.rel_path, p.width, p.height, p.file_size_bytes, sap.sort_order, sap.created_at
        FROM shared_album_photos sap
        JOIN photos p ON sap.photo_id = p.id
        WHERE sap.shared_album_id = ?
        ORDER BY sap.sort_order ASC
    """, (album_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def check_and_increment_shared_rate_limit(action: str, key_hash: str, max_requests: int, window_seconds: int) -> bool:
    """
    Worker-safe distributed rate limiter using SQLite.
    Returns True if request is allowed, False if rate limited.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff_str = (now_utc - timedelta(seconds=window_seconds)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    c = conn.cursor()
    try:
        # Check count in window
        c.execute("""
            SELECT COUNT(id) FROM shared_rate_limits
            WHERE action = ? AND key_hash = ? AND created_at >= ?
        """, (action, key_hash, cutoff_str))
        count = c.fetchone()[0] or 0
        if count >= max_requests:
            return False

        c.execute("""
            INSERT INTO shared_rate_limits (action, key_hash, created_at)
            VALUES (?, ?, ?)
        """, (action, key_hash, now_utc.strftime("%Y-%m-%d %H:%M:%S")))

        # Periodic cleanup of records older than 24 hours
        if secrets.randbelow(50) == 0:
            c.execute("DELETE FROM shared_rate_limits WHERE created_at < datetime('now', '-24 hours')")

        conn.commit()
        return True
    finally:
        conn.close()


def record_shared_album_access_log(shared_album_id: int, access_type: str, ip: str, user_agent: str):
    """
    Records minimal, privacy-preserving access entry.
    Hashed IP is derived with a daily rotating salt to avoid storing raw IP while allowing daily uniqueness counting.
    Automatically purges logs older than 30 days.
    """
    today_salt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ip_hash = hashlib.sha256(f"{ip}:{today_salt}".encode("utf-8")).hexdigest()[:32]
    ua_short = (user_agent or "Unknown")[:120]

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO shared_album_access_logs (shared_album_id, access_type, ip_hash, user_agent_short)
            VALUES (?, ?, ?, ?)
        """, (shared_album_id, access_type, ip_hash, ua_short))

        if secrets.randbelow(100) == 0:
            c.execute("DELETE FROM shared_album_access_logs WHERE accessed_at < datetime('now', '-30 days')")

        conn.commit()
    except Exception as e:
        print(f"Error logging shared access: {e}")
    finally:
        conn.close()

