import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request

from backend.config import PHOTOS, DATA, invalidate_public_event_cache, append_log
from backend.dependencies import get_auth_token
from backend.db.connection import get_db
from backend.db.reviews import get_match_reviews, update_match_feedback
from backend.db.reviews import get_duplicate_groups, resolve_duplicate_group
from backend.services.indexing import sync_event_folders_and_photos
from backend.services.media import resolve_photo_path
from ai_matching import scan_and_record_duplicates

router = APIRouter(tags=["admin_photos"])

@router.get("/api/admin/events/{slug}/photos")
def admin_event_all_photos(slug: str, album_id: int = None, quality_filter: Optional[str] = None, token: str = Depends(get_auth_token)):
    sync_event_folders_and_photos(slug)
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT p.*, a.name as album_name, a.slug as album_slug
        FROM photos p
        JOIN events e ON p.event_id = e.id
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE e.slug = ? AND (a.is_deleted = 0 OR a.is_deleted IS NULL) AND p.is_deleted = 0
    """
    params = [slug]
    if album_id:
        query += " AND p.album_id = ?"
        params.append(album_id)

    if quality_filter:
        qf = quality_filter.lower().strip()
        if qf == "blurry":
            query += " AND (p.quality_flags LIKE '%blurry%' OR (p.blur_score IS NOT NULL AND p.blur_score < 60.0))"
        elif qf in ("underexposed", "dark"):
            query += " AND (p.quality_flags LIKE '%underexposed%' OR (p.exposure_score IS NOT NULL AND p.exposure_score < 45.0))"
        elif qf == "overexposed":
            query += " AND (p.quality_flags LIKE '%overexposed%' OR (p.exposure_score IS NOT NULL AND p.exposure_score > 215.0))"
        elif qf == "low_res_face":
            query += " AND p.quality_flags LIKE '%low_res_face%'"
        elif qf in ("issues", "flagged"):
            query += " AND (p.quality_grade IN ('low', 'acceptable') OR (p.quality_flags IS NOT NULL AND p.quality_flags NOT IN ('', '[]')))"

    query += " ORDER BY p.is_featured DESC, COALESCE(p.captured_at, p.created_at) DESC, p.id DESC"
    cursor.execute(query, params)
    photos = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"photos": photos}

@router.post("/api/admin/events/{slug}/photos/bulk-move")
def admin_bulk_move_photos(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    photo_ids = payload.get("photo_ids", [])
    target_album_id = payload.get("target_album_id")
    if not photo_ids:
        raise HTTPException(400, "No photos selected.")
        
    conn = get_db()
    cursor = conn.cursor()
    if target_album_id:
        cursor.execute("SELECT id FROM albums WHERE id = ? AND event_id = (SELECT id FROM events WHERE slug = ?)", (target_album_id, slug))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(400, "Invalid target album.")
            
    placeholders = ",".join("?" for _ in photo_ids)
    cursor.execute(f"""
        UPDATE photos SET album_id = ? 
        WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)
    """, [target_album_id] + photo_ids + [slug])
    conn.commit()
    conn.close()
    return {"status": "ok", "moved_count": len(photo_ids)}

@router.post("/api/admin/events/{slug}/photos/bulk-action")
def admin_bulk_photo_action(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    photo_ids = payload.get("photo_ids", [])
    action = payload.get("action", "")
    if not photo_ids:
        raise HTTPException(400, "No photos selected.")
        
    conn = get_db()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in photo_ids)
    
    if action == "hide":
        cursor.execute(f"UPDATE photos SET is_hidden = 1 WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)", photo_ids + [slug])
    elif action == "unhide":
        cursor.execute(f"UPDATE photos SET is_hidden = 0 WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)", photo_ids + [slug])
    elif action == "feature":
        cursor.execute(f"UPDATE photos SET is_featured = 1 WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)", photo_ids + [slug])
    elif action == "unfeature":
        cursor.execute(f"UPDATE photos SET is_featured = 0 WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)", photo_ids + [slug])
    elif action == "delete":
        cursor.execute(f"UPDATE photos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders}) AND event_id = (SELECT id FROM events WHERE slug = ?)", photo_ids + [slug])
    else:
        conn.close()
        raise HTTPException(400, "Invalid action.")
        
    conn.commit()
    conn.close()
    return {"status": "ok", "count": len(photo_ids)}

@router.post("/api/admin/events/{slug}/albums/{album_id}/set-cover")
def admin_set_album_cover(slug: str, album_id: int, payload: dict, token: str = Depends(get_auth_token)):
    rel_path = payload.get("rel_path", "").strip()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE albums SET cover_image_path = ?
        WHERE id = ? AND event_id = (SELECT id FROM events WHERE slug = ?)
    """, (rel_path, album_id, slug))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@router.post("/api/admin/events/{slug}/photos/{photo_id}/toggle-feature")
def admin_toggle_photo_featured(slug: str, photo_id: int, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_featured FROM photos WHERE id = ? AND event_id = (SELECT id FROM events WHERE slug = ?)", (photo_id, slug))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Photo not found")
    new_state = 0 if row["is_featured"] else 1
    cursor.execute("UPDATE photos SET is_featured = ? WHERE id = ?", (new_state, photo_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "photo_id": photo_id, "is_featured": new_state}

# --- Trash Management APIs ---

@router.get("/api/admin/trash")
def admin_get_trash(token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, slug, name, event_type, created_at, updated_at
        FROM events
        WHERE status = 'archived'
        ORDER BY updated_at DESC
    """)
    trashed_events = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT a.id, a.slug, a.name, a.event_id, e.name as event_name, e.slug as event_slug, a.created_at
        FROM albums a
        JOIN events e ON a.event_id = e.id
        WHERE a.is_deleted = 1
        ORDER BY a.id DESC
    """)
    trashed_albums = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT p.id, p.filename, p.rel_path, p.album_id, p.event_id, e.name as event_name, e.slug as event_slug,
               COALESCE(a.name, 'Unassigned') as album_name, p.deleted_at
        FROM photos p
        JOIN events e ON p.event_id = e.id
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE p.is_deleted = 1
        ORDER BY p.deleted_at DESC, p.id DESC LIMIT 400
    """)
    trashed_photos = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {
        "events": trashed_events,
        "albums": trashed_albums,
        "photos": trashed_photos
    }

@router.post("/api/admin/trash/restore")
def admin_restore_trash(payload: dict, token: str = Depends(get_auth_token)):
    item_type = payload.get("type")  # 'event' | 'album' | 'photo'
    item_id = payload.get("id")
    photo_ids = payload.get("photo_ids", [])
    conn = get_db()
    cursor = conn.cursor()

    if item_type == "event":
        cursor.execute("UPDATE events SET status = 'published', updated_at = CURRENT_TIMESTAMP WHERE id = ? OR slug = ?", (item_id, str(item_id)))
        # Restore associated albums and photos
        cursor.execute("UPDATE albums SET is_deleted = 0, is_published = 1 WHERE event_id = (SELECT id FROM events WHERE id = ? OR slug = ?)", (item_id, str(item_id)))
        cursor.execute("UPDATE photos SET is_deleted = 0 WHERE event_id = (SELECT id FROM events WHERE id = ? OR slug = ?)", (item_id, str(item_id)))
    elif item_type == "album":
        cursor.execute("UPDATE albums SET is_deleted = 0, is_published = 1 WHERE id = ?", (item_id,))
        cursor.execute("UPDATE photos SET is_deleted = 0 WHERE album_id = ?", (item_id,))
    elif item_type == "photo":
        ids = photo_ids or ([item_id] if item_id else [])
        if ids:
            cursor.execute(f"UPDATE photos SET is_deleted = 0 WHERE id IN ({','.join('?' for _ in ids)})", ids)
    else:
        conn.close()
        raise HTTPException(400, "Invalid item type.")

    conn.commit()
    conn.close()
    append_log(f"Restored {item_type} (id: {item_id or photo_ids}) from Trash.")
    return {"status": "ok", "restored": item_type}

@router.post("/api/admin/trash/permanent-delete")
def admin_permanent_delete_trash(payload: dict, token: str = Depends(get_auth_token)):
    item_type = payload.get("type")  # 'event' | 'album' | 'photo' | 'all'
    item_id = payload.get("id")
    photo_ids = payload.get("photo_ids", [])
    conn = get_db()
    cursor = conn.cursor()

    if item_type == "event":
        cursor.execute("SELECT id, slug, name FROM events WHERE (id = ? OR slug = ?) AND status = 'archived'", (item_id, str(item_id)))
        ev = cursor.fetchone()
        if ev:
            eid = ev["id"]
            slug = ev["slug"]
            cursor.execute("DELETE FROM face_detections WHERE event_id = ?", (eid,))
            cursor.execute("DELETE FROM photos WHERE event_id = ?", (eid,))
            cursor.execute("DELETE FROM albums WHERE event_id = ?", (eid,))
            cursor.execute("DELETE FROM person_profiles WHERE event_id = ?", (eid,))
            cursor.execute("DELETE FROM events WHERE id = ?", (eid,))
            ev_dir = PHOTOS / "events" / slug
            if ev_dir.exists():
                shutil.rmtree(ev_dir, ignore_errors=True)
            data_ev_dir = DATA / "events" / slug
            if data_ev_dir.exists():
                shutil.rmtree(data_ev_dir, ignore_errors=True)
            append_log(f"Permanently purged event '{ev['name']}' ({slug}) and removed disk files.")

    elif item_type == "album":
        cursor.execute("SELECT a.id, a.slug, a.name, e.slug as event_slug FROM albums a JOIN events e ON a.event_id = e.id WHERE a.id = ? AND a.is_deleted = 1", (item_id,))
        alb = cursor.fetchone()
        if alb:
            aid = alb["id"]
            aslug = alb["slug"]
            eslug = alb["event_slug"]
            cursor.execute("SELECT id, rel_path FROM photos WHERE album_id = ?", (aid,))
            p_rows = cursor.fetchall()
            pids = [r["id"] for r in p_rows]
            if pids:
                cursor.execute(f"DELETE FROM face_detections WHERE photo_id IN ({','.join('?' for _ in pids)})", pids)
                cursor.execute(f"DELETE FROM photos WHERE id IN ({','.join('?' for _ in pids)})", pids)
            cursor.execute("DELETE FROM albums WHERE id = ?", (aid,))
            alb_dir = PHOTOS / "events" / eslug / aslug if eslug not in {"default", "wedding", "brk-sir", "brksir"} else PHOTOS / aslug
            if alb_dir.exists():
                shutil.rmtree(alb_dir, ignore_errors=True)
            append_log(f"Permanently purged album '{alb['name']}' ({aslug}) and removed folder from disk.")

    elif item_type == "photo":
        ids = photo_ids or ([item_id] if item_id else [])
        if ids:
            cursor.execute(f"SELECT id, rel_path FROM photos WHERE id IN ({','.join('?' for _ in ids)}) AND is_deleted = 1", ids)
            rows = cursor.fetchall()
            for r in rows:
                rel = r["rel_path"]
                fpath = PHOTOS / rel
                if fpath.exists():
                    try: fpath.unlink()
                    except Exception: pass
                tpath = DATA / "thumbs" / (rel.replace("/", "__") + ".jpg")
                if tpath.exists():
                    try: tpath.unlink()
                    except Exception: pass
            cursor.execute(f"DELETE FROM face_detections WHERE photo_id IN ({','.join('?' for _ in ids)})", ids)
            cursor.execute(f"DELETE FROM photos WHERE id IN ({','.join('?' for _ in ids)})", ids)
            append_log(f"Permanently purged {len(rows)} photos and their files from disk.")

    elif item_type == "all":
        cursor.execute("SELECT id, rel_path FROM photos WHERE is_deleted = 1")
        for r in cursor.fetchall():
            fpath = PHOTOS / r["rel_path"]
            if fpath.exists():
                try: fpath.unlink()
                except Exception: pass
            tpath = DATA / "thumbs" / (r["rel_path"].replace("/", "__") + ".jpg")
            if tpath.exists():
                try: tpath.unlink()
                except Exception: pass
        cursor.execute("DELETE FROM photos WHERE is_deleted = 1")

        cursor.execute("SELECT a.id, a.slug, e.slug as eslug FROM albums a JOIN events e ON a.event_id = e.id WHERE a.is_deleted = 1")
        for a in cursor.fetchall():
            adir = PHOTOS / "events" / a["eslug"] / a["slug"] if a["eslug"] not in {"default", "wedding", "brk-sir", "brksir"} else PHOTOS / a["slug"]
            if adir.exists():
                shutil.rmtree(adir, ignore_errors=True)
        cursor.execute("DELETE FROM albums WHERE is_deleted = 1")

        cursor.execute("SELECT id, slug FROM events WHERE status = 'archived'")
        for ev in cursor.fetchall():
            edir = PHOTOS / "events" / ev["slug"]
            if edir.exists():
                shutil.rmtree(edir, ignore_errors=True)
            ddir = DATA / "events" / ev["slug"]
            if ddir.exists():
                shutil.rmtree(ddir, ignore_errors=True)
            cursor.execute("DELETE FROM events WHERE id = ?", (ev["id"],))
        append_log("Permanently purged all Trash items from database and VPS disk.")

    conn.commit()
    conn.close()
    return {"status": "ok", "message": "Permanently deleted from disk."}


@router.get("/api/admin/events/{slug}/duplicates")
@router.get("/api/admin/photos/duplicates")
def admin_get_duplicates(slug: str = "brk-sir", status: str = "pending", token: str = Depends(get_auth_token)):
    """Returns detected duplicate groups with dimensions, sizes, and recommended primary photo."""
    groups = get_duplicate_groups(slug, status=status)
    return {"status": "ok", "count": len(groups), "groups": groups}


@router.post("/api/admin/events/{slug}/duplicates/resolve")
@router.post("/api/admin/photos/duplicates/resolve")
def admin_resolve_duplicates(slug: str = "brk-sir", payload: dict = None, token: str = Depends(get_auth_token)):
    """Resolves duplicate group by deleting selected duplicates or ignoring."""
    if not payload:
        raise HTTPException(400, "Missing request body.")
    group_id = payload.get("group_id")
    action = payload.get("action", "delete_duplicates")
    photo_ids = payload.get("photo_ids", [])
    if not group_id:
        raise HTTPException(400, "group_id is required.")
    return resolve_duplicate_group(group_id, action, photo_ids)


@router.post("/api/admin/events/{slug}/duplicates/scan")
@router.post("/api/admin/photos/duplicates/scan")
def admin_scan_duplicates(slug: str = "brk-sir", token: str = Depends(get_auth_token)):
    """On-demand scan to detect and record duplicate photos without deleting anything."""
    summary = scan_and_record_duplicates(slug)
    return {"status": "ok", "summary": summary}


# ---------------------------------------------------------------------------
# Match Confidence Review & Human Feedback Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/admin/matching/reviews")
def admin_get_match_reviews_endpoint(
    event_slug: str = "brk-sir",
    status: str = "all",
    tier: str = "all",
    limit: int = 100,
    token: str = Depends(get_auth_token)
):
    """Returns query crop vs matched crop inspection pairs for confidence diagnostics."""
    reviews = get_match_reviews(event_slug=event_slug, status=status, tier=tier, limit=limit)
    return {"status": "ok", "count": len(reviews), "reviews": reviews}


@router.post("/api/admin/matching/feedback")
def admin_post_match_feedback_endpoint(
    payload: dict,
    request: Request,
    token: str = Depends(get_auth_token)
):
    """Allows administrators to mark matches as correct or incorrect."""
    review_id = payload.get("review_id")
    feedback = payload.get("feedback")
    notes = payload.get("notes")
    if not review_id or feedback not in ("correct", "incorrect", "pending"):
        raise HTTPException(400, "Valid review_id and feedback ('correct' | 'incorrect') required.")
    client_ip = request.client.host if request.client else ""
    success = update_match_feedback(int(review_id), feedback, notes=notes, reviewer_ip=client_ip)
    return {"status": "ok", "updated": success}


# ---------------------------------------------------------------------------
# System Settings & AI Thresholds Configuration
# ---------------------------------------------------------------------------

