import re
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

from backend.config import PHOTOS, DATA, invalidate_public_event_cache, current_task, append_log
from backend.dependencies import get_auth_token
from backend.db.connection import get_db
from backend.services.face_search import get_event_index
from backend.services.indexing import sync_event_folders_and_photos

router = APIRouter(tags=["admin_events"])

@router.get("/api/admin/overview")
def admin_overview(token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(id) as total FROM events WHERE status != 'archived'")
    total_events = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(id) as active FROM events WHERE status = 'published'")
    active_events = cursor.fetchone()["active"]
    
    cursor.execute("SELECT COUNT(id) as total_photos FROM photos WHERE is_deleted = 0")
    total_photos = cursor.fetchone()["total_photos"]
    
    cursor.execute("SELECT COUNT(id) as total_albums FROM albums")
    total_albums = cursor.fetchone()["total_albums"]

    cursor.execute("SELECT COUNT(id) as total_people FROM person_profiles")
    total_people = cursor.fetchone()["total_people"]
    
    cursor.execute("""
        SELECT e.*,
               (SELECT COUNT(p.id) FROM photos p WHERE p.event_id = e.id AND p.is_deleted = 0) as photo_count,
               (SELECT COUNT(a.id) FROM albums a WHERE a.event_id = e.id AND (a.is_deleted = 0 OR a.is_deleted IS NULL)) as album_count
        FROM events e
        WHERE e.status != 'archived'
        ORDER BY e.created_at DESC
    """)
    events = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {
        "metrics": {
            "total_events": total_events,
            "active_events": active_events,
            "total_photos": total_photos,
            "total_albums": total_albums,
            "identified_people": total_people
        },
        "events": events,
        "current_task": current_task
    }


@router.post("/api/admin/events")
def admin_create_event(payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", payload.get("slug", "").strip().lower()).strip("-")
    if not name or not slug:
        raise HTTPException(400, "Name and valid slug are required.")
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO events (slug, name, event_type, event_date, location, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            slug,
            name,
            payload.get("event_type", "Wedding"),
            payload.get("event_date", ""),
            payload.get("location", ""),
            payload.get("description", ""),
            payload.get("status", "published")
        ))
        eid = cursor.lastrowid
        
        # Create default album
        cursor.execute("""
            INSERT INTO albums (event_id, slug, name, sort_order)
            VALUES (?, 'highlights', 'Highlights', 1)
        """, (eid,))
        
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"Error creating event: {e}")
        
    conn.close()
    
    # Create event storage folders
    (PHOTOS / "events" / slug).mkdir(parents=True, exist_ok=True)
    (DATA / "events" / slug).mkdir(parents=True, exist_ok=True)
    
    return {"status": "ok", "slug": slug}

_last_sync_folders_time = {}

@router.get("/api/admin/events/{slug}")
def admin_event_workspace(slug: str, sync: bool = False, token: str = Depends(get_auth_token)):
    if sync:
        sync_event_folders_and_photos(slug)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "Event not found")
        
    cursor.execute("""
        SELECT a.*, COUNT(p.id) as photo_count
        FROM albums a
        LEFT JOIN photos p ON a.id = p.album_id AND p.is_deleted = 0
        WHERE a.event_id = ? AND a.is_deleted = 0
        GROUP BY a.id
        ORDER BY a.sort_order ASC, a.name ASC
    """, (event["id"],))
    albums = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("""
        SELECT id, filename, rel_path, album_id, is_featured, file_size_bytes, created_at
        FROM photos
        WHERE event_id = ? AND is_deleted = 0
        ORDER BY id DESC LIMIT 100
    """, (event["id"],))
    recent_photos = [dict(row) for row in cursor.fetchall()]
    
    # Get indexed faces count
    embs, names, _ = get_event_index(slug)
    conn.close()
    
    return {
        "event": dict(event),
        "albums": albums,
        "recent_photos": recent_photos,
        "indexed_faces": len(embs),
        "current_task": current_task
    }

@router.get("/api/admin/albums")
def admin_get_all_albums(token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.*, COUNT(p.id) as photo_count
        FROM albums a
        LEFT JOIN photos p ON a.id = p.album_id AND p.is_deleted = 0
        WHERE a.is_deleted = 0
        GROUP BY a.id
        ORDER BY a.sort_order ASC, a.name ASC
    """)
    albums = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"albums": albums}

@router.post("/api/admin/albums")
def admin_create_album_direct(payload: dict, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return admin_create_album(slug, payload, token)

@router.post("/api/admin/events/{slug}/albums")
def admin_create_album(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    album_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", payload.get("slug", "").strip().lower()).strip("-")
    if not name or not album_slug:
        raise HTTPException(400, "Album name and slug are required.")
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')", (slug, slug))
    event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "Event not found")
        
    try:
        cursor.execute("""
            INSERT INTO albums (event_id, slug, name, sort_order)
            VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM albums WHERE event_id = ?))
        """, (event["id"], album_slug, name, event["id"]))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"Error: {e}")
        
    conn.close()
    
    # Create folder on disk
    if slug in {"default", "wedding", "brk-sir", "brksir"}:
        (PHOTOS / album_slug).mkdir(parents=True, exist_ok=True)
    else:
        (PHOTOS / "events" / slug / album_slug).mkdir(parents=True, exist_ok=True)
        
    return {"status": "ok", "album_slug": album_slug}

@router.put("/api/admin/events/{slug}")
@router.post("/api/admin/events/{slug}/update")
def admin_update_event(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "Event not found")

    new_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", payload.get("slug", slug).strip().lower()).strip("-") or slug
    name = payload.get("name", "").strip() or event["name"]
    location = payload.get("location", event["location"] or "")
    description = payload.get("description", event["description"] or "")
    event_date = payload.get("event_date", event["event_date"] or "")

    # Prevent renaming active slug if a task is running into it
    if current_task.get("status") == "running" and current_task.get("event") == slug and new_slug != slug:
        conn.close()
        raise HTTPException(409, "Cannot rename event slug while a background task is running on it. Please wait for completion.")

    if new_slug != slug:
        cursor.execute("SELECT id FROM events WHERE slug = ? AND id != ?", (new_slug, event["id"]))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(400, f"Event slug '{new_slug}' is already in use.")

        # Rename directories on disk
        old_photos_dir = PHOTOS / "events" / slug
        new_photos_dir = PHOTOS / "events" / new_slug
        if old_photos_dir.exists():
            try: old_photos_dir.rename(new_photos_dir)
            except Exception as e: append_log(f"Warning renaming photos dir: {e}")

        old_data_dir = DATA / "events" / slug
        new_data_dir = DATA / "events" / new_slug
        if old_data_dir.exists():
            try: old_data_dir.rename(new_data_dir)
            except Exception as e: append_log(f"Warning renaming data dir: {e}")

        # Update photo rel_paths
        cursor.execute("UPDATE photos SET rel_path = replace(rel_path, ?, ?) WHERE event_id = ?",
                       (f"events/{slug}/", f"events/{new_slug}/", event["id"]))

    cursor.execute("""
        UPDATE events
        SET slug = ?, name = ?, location = ?, description = ?, event_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (new_slug, name, location, description, event_date, event["id"]))
    conn.commit()
    conn.close()
    return {"status": "ok", "slug": new_slug, "name": name}

@router.delete("/api/admin/events/{slug}")
@router.post("/api/admin/events/{slug}/delete")
def admin_delete_event(slug: str, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, slug FROM events WHERE slug = ?", (slug,))
    ev = cursor.fetchone()
    if not ev:
        conn.close()
        raise HTTPException(404, "Event not found")

    if current_task.get("status") == "running" and current_task.get("event") == slug:
        conn.close()
        raise HTTPException(409, "Cannot delete event while a background task is running on it.")

    # Soft-delete: move to trash
    cursor.execute("UPDATE events SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ev["id"],))
    cursor.execute("UPDATE albums SET is_deleted = 1, is_published = 0 WHERE event_id = ?", (ev["id"],))
    cursor.execute("UPDATE photos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE event_id = ?", (ev["id"],))
    conn.commit()
    conn.close()

    append_log(f"Moved event '{ev['name']}' ({slug}) and its contents to Trash.")
    return {"status": "ok", "deleted_event": slug, "message": f"Event '{ev['name']}' moved to Trash."}

@router.put("/api/admin/events/{slug}/albums/{album_id}")
@router.post("/api/admin/events/{slug}/albums/{album_id}/update")
def admin_update_album(slug: str, album_id: int, payload: dict, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT a.*, e.id as eid FROM albums a JOIN events e ON a.event_id = e.id WHERE a.id = ? AND e.slug = ?", (album_id, slug))
    album = cursor.fetchone()
    if not album:
        conn.close()
        raise HTTPException(404, "Album not found")

    old_slug = album["slug"]
    new_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", payload.get("slug", old_slug).strip().lower()).strip("-") or old_slug
    name = payload.get("name", "").strip() or album["name"]

    if new_slug != old_slug:
        cursor.execute("SELECT id FROM albums WHERE event_id = ? AND slug = ? AND id != ?", (album["eid"], new_slug, album_id))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(400, f"Album slug '{new_slug}' already exists in this event.")

        # Rename directory on disk
        if slug in {"default", "wedding", "brk-sir", "brksir"}:
            old_dir = PHOTOS / old_slug
            new_dir = PHOTOS / new_slug
            old_rel = old_slug + "/"
            new_rel = new_slug + "/"
        else:
            old_dir = PHOTOS / "events" / slug / old_slug
            new_dir = PHOTOS / "events" / slug / new_slug
            old_rel = f"events/{slug}/{old_slug}/"
            new_rel = f"events/{slug}/{new_slug}/"

        if old_dir.exists():
            try: old_dir.rename(new_dir)
            except Exception as e: append_log(f"Warning renaming folder on disk: {e}")

        # Update photo rel_paths
        cursor.execute("UPDATE photos SET rel_path = replace(rel_path, ?, ?) WHERE album_id = ?", (old_rel, new_rel, album_id))

    cursor.execute("""
        UPDATE albums
        SET name = ?,
            slug = ?,
            is_published = COALESCE(?, is_published),
            sort_order = COALESCE(?, sort_order),
            cover_image_path = COALESCE(?, cover_image_path)
        WHERE id = ?
    """, (
        name,
        new_slug,
        payload.get("is_published"),
        payload.get("sort_order"),
        payload.get("cover_image_path"),
        album_id
    ))
    conn.commit()
    conn.close()
    return {"status": "ok", "album_slug": new_slug, "name": name}

@router.delete("/api/admin/events/{slug}/albums/{album_id}")
@router.post("/api/admin/events/{slug}/albums/{album_id}/delete")
def admin_delete_album(slug: str, album_id: int, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, slug, name FROM albums WHERE id = ? AND event_id = (SELECT id FROM events WHERE slug = ?)", (album_id, slug))
    album = cursor.fetchone()
    if not album:
        conn.close()
        raise HTTPException(404, "Album not found")

    album_slug = album["slug"]

    # Soft-delete album and its photos to Trash
    cursor.execute("UPDATE albums SET is_deleted = 1, is_published = 0 WHERE id = ?", (album_id,))
    cursor.execute("UPDATE photos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE album_id = ?", (album_id,))
    conn.commit()
    conn.close()

    append_log(f"Moved folder '{album['name']}' ({album_slug}) in event '{slug}' to Trash.")
    return {"status": "ok", "deleted_album": album_slug, "message": f"Folder '{album['name']}' moved to Trash."}


@router.get("/api/admin/events/{slug}/search")
def admin_search_albums_and_photos(slug: str, q: str = "", token: str = Depends(get_auth_token)):
    q = q.strip()
    if not q:
        return {"albums": [], "photos": []}
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ?", (slug,))
    ev = cursor.fetchone()
    if not ev:
        conn.close()
        return {"albums": [], "photos": []}
    event_id = ev["id"]

    query_like = f"%{q}%"
    
    # 1. Search Albums
    cursor.execute("""
        SELECT a.id, a.name, a.slug, a.cover_image_path,
               COUNT(p.id) as photo_count
        FROM albums a
        LEFT JOIN photos p ON a.id = p.album_id AND p.is_deleted = 0
        WHERE a.event_id = ? AND a.is_deleted = 0 AND (a.name LIKE ? OR a.slug LIKE ?)
        GROUP BY a.id
        ORDER BY a.name ASC
        LIMIT 20
    """, (event_id, query_like, query_like))
    matched_albums = [dict(r) for r in cursor.fetchall()]

    # 2. Search Photos by filename or path
    cursor.execute("""
        SELECT p.id, p.album_id, p.filename, p.rel_path, p.is_featured, p.is_hidden,
               a.name as album_name, a.slug as album_slug
        FROM photos p
        JOIN albums a ON p.album_id = a.id
        WHERE p.event_id = ? AND p.is_deleted = 0 AND (p.filename LIKE ? OR p.rel_path LIKE ?)
        ORDER BY p.is_featured DESC, COALESCE(p.captured_at, p.created_at) DESC, p.id DESC
        LIMIT 40
    """, (event_id, query_like, query_like))
    matched_photos = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        "query": q,
        "albums": matched_albums,
        "photos": matched_photos
    }

