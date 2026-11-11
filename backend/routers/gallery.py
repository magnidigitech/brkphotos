import time
from fastapi import APIRouter, HTTPException

from backend.config import _public_event_cache, _public_event_cache_time
from backend.db.connection import get_db
from backend.services.media import reload_drive_map, drive_map

router = APIRouter(tags=["gallery"])

@router.get("/api/public/gallery")
def public_gallery():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_event_details(slug)

@router.get("/api/public/albums/{album_slug}")
def public_album_direct(album_slug: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_album_photos(slug, album_slug)

@router.get("/api/public/events/{slug}")
def public_event_details(slug: str):
    # Check in-memory metadata cache (30s TTL)
    now_t = time.time()
    if slug in _public_event_cache and (now_t - _public_event_cache_time.get(slug, 0) < 30):
        return _public_event_cache[slug]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE slug = ? AND status != 'archived'", (slug,))
    event = cursor.fetchone()
    if not event:
        # Fallback to the primary event
        cursor.execute("SELECT * FROM events WHERE status != 'archived' ORDER BY id ASC LIMIT 1")
        event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "No gallery found")

    # Efficient single-query album metadata with consolidated cover image resolution (eliminates N+1 queries)
    cursor.execute("""
        SELECT a.*, COUNT(p.id) as photo_count,
               COALESCE(a.cover_image_path, (
                   SELECT p2.rel_path FROM photos p2
                   WHERE p2.album_id = a.id AND p2.is_deleted = 0 AND p2.is_hidden = 0
                   ORDER BY p2.is_featured DESC, p2.id DESC LIMIT 1
               )) as effective_cover_image
        FROM albums a
        LEFT JOIN photos p ON a.id = p.album_id AND p.is_deleted = 0 AND p.is_hidden = 0
        WHERE a.event_id = ? AND a.is_published = 1 AND a.is_deleted = 0
        GROUP BY a.id
        ORDER BY a.sort_order ASC, a.name ASC
    """, (event["id"],))
    albums = []
    for row in cursor.fetchall():
        d = dict(row)
        if not d.get("cover_image_path"):
            d["cover_image_path"] = d.get("effective_cover_image")
        d.pop("effective_cover_image", None)
        albums.append(d)

    event_dict = dict(event)
    if not event_dict["cover_image_path"]:
        cursor.execute("""
            SELECT rel_path FROM photos
            WHERE event_id = ? AND is_deleted = 0 AND is_hidden = 0
            ORDER BY is_featured DESC, id DESC LIMIT 1
        """, (event["id"],))
        e_cov = cursor.fetchone()
        if e_cov:
            event_dict["cover_image_path"] = e_cov["rel_path"]

    cursor.execute("SELECT COUNT(id) as total FROM photos WHERE event_id = ? AND is_deleted = 0", (event["id"],))
    photo_count = cursor.fetchone()["total"]
    conn.close()

    result = {
        "event": event_dict,
        "albums": albums,
        "photo_count": photo_count
    }
    _public_event_cache[slug] = result
    _public_event_cache_time[slug] = now_t
    return result

@router.get("/api/public/events/{slug}/albums/{album_slug}")
def public_album_photos(slug: str, album_slug: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.name, a.slug, e.id as event_id
        FROM albums a
        JOIN events e ON a.event_id = e.id
        WHERE (e.slug = ? OR ? IN ('brk-sir', 'brksir', 'wedding', 'default', '')) AND a.slug = ? AND a.is_published = 1 AND a.is_deleted = 0
    """, (slug, slug, album_slug))
    album = cursor.fetchone()
    if not album:
        # Fallback search album by slug regardless of event
        cursor.execute("""
            SELECT id, name, slug, event_id
            FROM albums
            WHERE slug = ? AND is_published = 1 AND is_deleted = 0
            ORDER BY id ASC LIMIT 1
        """, (album_slug,))
        album = cursor.fetchone()
    if not album:
        conn.close()
        raise HTTPException(404, "Album not found")
        
    cursor.execute("""
        SELECT id, filename, rel_path, drive_file_id, is_featured, created_at, captured_at
        FROM photos
        WHERE album_id = ? AND is_deleted = 0 AND is_hidden = 0
        ORDER BY is_featured DESC, COALESCE(captured_at, created_at) DESC, id DESC
    """, (album["id"],))
    photos = [dict(row) for row in cursor.fetchall()]
    conn.close()

    reload_drive_map()
    for p in photos:
        if not p.get("drive_file_id"):
            p["drive_file_id"] = drive_map.get(p["filename"]) or drive_map.get(p["rel_path"])
    
    return {"album": dict(album), "photos": photos}

