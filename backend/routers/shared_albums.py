import os
import re
import time
import zipfile
import secrets
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse

from backend.config import BASE, DATA, thumb_semaphore, zip_semaphore
from backend.dependencies import get_client_ip, get_auth_token
from backend.services.media import resolve_photo_path
from backend.db.auth import record_security_event
from backend.db.shared_albums import (
    check_and_increment_shared_rate_limit,
    create_shared_album_atomic,
    get_shared_album_by_token,
    get_shared_album_photos_page,
    verify_photo_in_shared_album,
    get_all_photo_records_for_shared_album,
    revoke_shared_album_by_creator,
    record_shared_album_access_log,
    admin_get_shared_albums,
    admin_get_shared_album_photos,
    admin_revoke_shared_album,
    admin_update_shared_album_expiry,
    admin_delete_shared_album
)

router = APIRouter(tags=["shared_albums"])

@router.post("/api/shared-albums")
@router.post("/api/events/{slug}/shared-albums")
async def create_shared_album_endpoint(request: Request, slug: Optional[str] = None):
    """
    Creates a new private, shareable event album from verified search results.
    Enforces rate limits, validates creation token against server-side proof,
    and returns independent share and management credentials.
    """
    ip = get_client_ip(request)

    # Distributed worker-safe rate limiting: 10 creation attempts per hour per IP
    if not check_and_increment_shared_rate_limit("create_album", ip, max_requests=10, window_seconds=3600):
        raise HTTPException(429, "Too many album creation attempts. Please wait an hour before creating more albums.")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload.")

    search_id = str(body.get("search_id") or "").strip()
    creation_token = str(body.get("creation_token") or "").strip()
    photo_ids = body.get("photo_ids", [])
    title = body.get("title")
    expiry_days = body.get("expiry_days", 30)
    client_share_token = body.get("share_token")
    client_manage_token = body.get("manage_token")

    if not search_id or not creation_token:
        raise HTTPException(400, "Both search_id and creation_token are required.")

    if not photo_ids or not isinstance(photo_ids, list):
        raise HTTPException(400, "A non-empty list of selected photo_ids is required.")

    try:
        sess = request.cookies.get("brk_session_id") or request.headers.get("X-Session-Id") or ""
        result = create_shared_album_atomic(
            search_id=search_id,
            creation_token=creation_token,
            photo_ids=photo_ids,
            title=title,
            expiry_days=expiry_days,
            creator_session=sess,
            share_token=client_share_token,
            manage_token=client_manage_token
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe))
    except ValueError as ve:
        err_msg = str(ve)
        if "already been consumed" in err_msg or "different" in err_msg:
            raise HTTPException(409, err_msg)
        raise HTTPException(400, err_msg)
    except Exception as e:
        print(f"Error in create_shared_album_endpoint: {e}")
        raise HTTPException(500, "Failed to create shared album. Please try again.")

    resp_data = {
        "status": "ok",
        "id": result["id"],
        "album_id": result["id"],
        "share_token": result["share_token"],
        "manage_token": result["manage_token"],
        "share_url": f"/s/{result['share_token']}",
        "title": result["title"],
        "photo_count": result["photo_count"],
        "created_at": result["created_at"],
        "expires_at": result["expires_at"],
        "is_retry": result.get("is_idempotent_retry", False),
        "is_idempotent_retry": result.get("is_idempotent_retry", False)
    }

    resp = JSONResponse(resp_data, status_code=200 if result.get("is_idempotent_retry") else 201)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

@router.get("/s/{token}")
def shared_album_page(token: str, request: Request):
    """
    Dedicated recipient viewer landing page for /s/{token}.
    Provides zero-leakage preview metadata (no guest biometric or photo previews in OpenGraph tags)
    and strictly enforces no-store caching and no-referrer headers.
    """
    if not token or not re.match(r'^[a-zA-Z0-9_\-]{16,128}$', token):
        raise HTTPException(404, "Invalid shared album link format.")

    alb = get_shared_album_by_token(token)
    title = "Shared Album — BRK Photos"
    headline = "Personalized Moments"
    event_label = "BRK Photos Celebration"
    is_active = True
    status_msg = ""

    if not alb or alb.get("status") == "not_found":
        is_active = False
        status_msg = "This album could not be found. Please double-check the link."
    elif alb.get("status") == "revoked":
        is_active = False
        status_msg = "This album was revoked by its creator and is no longer accessible."
    elif alb.get("status") == "expired":
        is_active = False
        status_msg = f"This album expired on {alb.get('expires_at')}. Please request an updated link."
    elif alb.get("status") == "unavailable":
        is_active = False
        status_msg = "This event is currently private or archived."
    else:
        title = f"{alb.get('title', 'My Moments')} — BRK Photos"
        headline = alb.get("title", "My Moments")
        event_label = alb.get("event_name", "BRK Photos")

    template_file = BASE / "templates" / "shared_album.html"
    if template_file.exists():
        template_text = template_file.read_text(encoding="utf-8")
    else:
        template_text = "<html><body><h1>Shared Album</h1><p>{{ status_msg }}</p></body></html>"

    is_active_js = "true" if is_active else "false"
    html = template_text
    html = html.replace("{{ title }}", title)
    html = html.replace("{{ headline }}", headline)
    html = html.replace("{{ event_label }}", event_label)
    html = html.replace("{{ status_msg }}", status_msg)
    html = html.replace("{{ token }}", token)
    html = html.replace("{{ is_active_js }}", is_active_js)

    status_code = 200
    if not is_active:
        if alb and alb.get("status") in ("revoked", "expired"):
            status_code = 410
        elif alb and alb.get("status") == "unavailable":
            status_code = 403
        else:
            status_code = 404
    resp = HTMLResponse(html, status_code=status_code)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    return resp

@router.get("/api/shared/{token}")
def get_shared_album_metadata_endpoint(token: str, request: Request):
    """
    Returns sanitized metadata for an active shared album.
    Rate limits probe attempts for invalid tokens.
    """
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")

    alb = get_shared_album_by_token(token)
    if not alb or alb.get("status") == "not_found":
        check_and_increment_shared_rate_limit("probe_invalid_token", ip, max_requests=60, window_seconds=60)
        raise HTTPException(404, "Shared album not found. Please verify your link.")

    status = alb.get("status")
    if status == "revoked":
        raise HTTPException(410, "This shared album was revoked by its creator and is no longer accessible.")
    if status == "expired":
        raise HTTPException(410, f"This shared album expired on {alb.get('expires_at')}.")
    if status == "unavailable":
        raise HTTPException(403, "The associated event is currently private or archived.")

    # Record access log
    record_shared_album_access_log(alb["id"], "view", ip, ua)

    resp = JSONResponse({"status": "ok", "album": alb})
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

@router.get("/api/shared/{token}/photos")
def get_shared_album_photos_endpoint(token: str, request: Request, page: int = 1, page_size: int = 50):
    """
    Returns ordered photos in a shared album with token-scoped media endpoints.
    Does not expose raw internal filesystem paths.
    """
    alb = get_shared_album_by_token(token)
    if not alb or alb.get("status") != "active":
        status = alb.get("status") if alb else "not_found"
        code = 410 if status in ("revoked", "expired") else (403 if status == "unavailable" else 404)
        raise HTTPException(code, f"Shared album is not active ({status}).")

    data = get_shared_album_photos_page(alb["id"], page=page, page_size=page_size)

    # Attach token-scoped URLs
    formatted_photos = []
    for p in data["photos"]:
        pid = p["id"]
        formatted_photos.append({
            "id": pid,
            "filename": p["filename"],
            "width": p["width"],
            "height": p["height"],
            "captured_at": p["captured_at"],
            "created_at": p["created_at"],
            "sort_order": p["sort_order"],
            "thumb_url": f"/api/shared/{token}/thumb/{pid}",
            "preview_url": f"/api/shared/{token}/preview/{pid}",
            "download_hd_url": f"/api/shared/{token}/download/hd/{pid}",
            "download_4k_url": f"/api/shared/{token}/download/4k/{pid}"
        })

    data["photos"] = formatted_photos
    resp = JSONResponse({"status": "ok", **data})
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

@router.get("/api/shared/{token}/thumb/{photo_id}")
def get_shared_photo_thumb_endpoint(token: str, photo_id: int, request: Request):
    """
    Delivers 480px thumbnail strictly verified against album membership and active event status.
    Uses private, no-store caching to enforce immediate revocation.
    """
    photo = verify_photo_in_shared_album(token, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found in this shared album or album is not active.")

    name = photo["rel_path"]
    photo_file = resolve_photo_path(name)

    # Check cached thumbnail
    direct_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if direct_thumb.exists():
        return FileResponse(direct_thumb, headers={"Cache-Control": "private, no-store"})

    if photo_file and photo_file.exists():
        with thumb_semaphore:
            cache_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
            if cache_thumb.exists():
                return FileResponse(cache_thumb, headers={"Cache-Control": "private, no-store"})
            try:
                cache_thumb.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(photo_file) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((480, 480), Image.Resampling.LANCZOS)
                    img.save(cache_thumb, "JPEG", quality=80, optimize=True)
                return FileResponse(cache_thumb, headers={"Cache-Control": "private, no-store"})
            except Exception:
                return FileResponse(photo_file, headers={"Cache-Control": "private, no-store"})

    raise HTTPException(404, "Thumbnail image unavailable.")

@router.get("/api/shared/{token}/preview/{photo_id}")
def get_shared_photo_preview_endpoint(token: str, photo_id: int, request: Request):
    """
    Delivers 1600px preview verified against album membership and active event status.
    """
    photo = verify_photo_in_shared_album(token, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found in this shared album or album is not active.")

    name = photo["rel_path"]
    cache_preview = DATA / "previews" / (name.replace("/", "__") + ".jpg")
    if cache_preview.exists():
        return FileResponse(cache_preview, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

    target = resolve_photo_path(name)
    if target and target.exists():
        try:
            cache_preview.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(target) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                img.save(cache_preview, "JPEG", quality=86, optimize=True)
            return FileResponse(cache_preview, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
        except Exception:
            return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

    raise HTTPException(404, "Preview image unavailable.")

@router.get("/api/shared/{token}/download/{quality}/{photo_id}")
def get_shared_photo_download_endpoint(token: str, quality: str, photo_id: int, request: Request):
    """
    Streams HD or full-res original photo attachment verified against album membership.
    """
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")

    photo = verify_photo_in_shared_album(token, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found in this shared album or album is not active.")

    record_shared_album_access_log(photo["album_id"], f"download_{quality}", ip, ua)

    name = photo["rel_path"]
    fname = photo["filename"]
    target = resolve_photo_path(name)

    if not target or not target.exists():
        # Fallback to preview or high-res thumbnail
        target = DATA / "previews" / (name.replace("/", "__") + ".jpg")
        if not target.exists():
            target = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")

    if not target or not target.exists():
        raise HTTPException(404, "Photo file not found on server.")

    return FileResponse(
        target,
        filename=fname,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer"
        }
    )

@router.post("/api/shared/{token}/download-zip")
def download_shared_album_zip_endpoint(token: str, request: Request, background_tasks: BackgroundTasks):
    """
    Streams a ZIP file of all (or selected) photos in this shared album.
    Protected by album membership verification and zip_semaphore concurrency bounds.
    """
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")

    # Distributed rate limit: max 10 ZIP generations per hour per IP/token
    if not check_and_increment_shared_rate_limit("zip_download", f"{ip}:{token[:16]}", max_requests=10, window_seconds=3600):
        raise HTTPException(429, "Too many ZIP download requests. Please wait a few minutes before trying again.")

    photo_records = get_all_photo_records_for_shared_album(token)
    if photo_records is None:
        raise HTTPException(404, "Shared album not found or no longer active.")
    if not photo_records:
        raise HTTPException(400, "No active photos in this shared album to download.")

    job_id = f"shared_zip_{int(time.time())}_{secrets.token_hex(4)}"
    tmp_dir = DATA / "tmp_zips"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_zip = tmp_dir / f"{job_id}.zip"

    # Acquire zip concurrency semaphore
    acquired = zip_semaphore.acquire(timeout=5.0)
    if not acquired:
        raise HTTPException(503, "The server is currently generating other photo archives. Please try again in a few moments.")

    try:
        with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_STORED) as zf:
            added = 0
            seen_names = {}
            for p in photo_records:
                target = resolve_photo_path(p["rel_path"])
                if not target or not target.exists():
                    target = DATA / "previews" / (p["rel_path"].replace("/", "__") + ".jpg")
                if not target or not target.exists():
                    target = DATA / "thumbs" / (p["rel_path"].replace("/", "__") + ".jpg")

                if target and target.exists():
                    base_name = p["filename"]
                    if base_name in seen_names:
                        seen_names[base_name] += 1
                        ext = Path(base_name).suffix
                        stem = Path(base_name).stem
                        archive_name = f"{stem}_{seen_names[base_name]}{ext}"
                    else:
                        seen_names[base_name] = 1
                        archive_name = base_name

                    zf.write(target, arcname=archive_name)
                    added += 1

        if added == 0:
            if tmp_zip.exists():
                tmp_zip.unlink()
            raise HTTPException(404, "No photo files could be retrieved for archive.")
    finally:
        zip_semaphore.release()

    def cleanup():
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except Exception:
            pass

    background_tasks.add_task(cleanup)

    return FileResponse(
        tmp_zip,
        filename=f"my_event_album_{token[:8]}.zip",
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="my_event_album_{token[:8]}.zip"',
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer"
        }
    )

@router.post("/api/shared-albums/{album_id}/revoke")
@router.delete("/api/shared-albums/{album_id}")
def revoke_shared_album_by_creator_endpoint(album_id: int, request: Request):
    """
    Revokes a shared album identified by its internal ID.
    Receives the independent management credential through header 'X-Manage-Token'.
    The management credential NEVER enters the request URL or path.
    Viewing credentials cannot authorise this action.
    """
    manage_token = request.headers.get("X-Manage-Token", "").strip()
    if not manage_token:
        raise HTTPException(401, "Management credential required in X-Manage-Token header.")

    success = revoke_shared_album_by_creator(album_id=album_id, manage_token=manage_token)
    if not success:
        raise HTTPException(403, "Invalid management credentials for this album, or album already revoked.")

    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    record_security_event("shared_album_revoked", f"Creator revoked shared album #{album_id}", ip=ip, user_agent=ua)

    return {"status": "ok", "message": f"Shared album #{album_id} revoked successfully."}

@router.get("/api/admin/shared-albums")
def admin_list_shared_albums_endpoint(page: int = 1, page_size: int = 50, status: Optional[str] = None, token: str = Depends(get_auth_token)):
    """Administrator listing of all created shared albums."""
    return admin_get_shared_albums(page=page, page_size=page_size, status_filter=status)

@router.get("/api/admin/shared-albums/{album_id}/photos")
def admin_get_shared_album_photos_endpoint(album_id: int, token: str = Depends(get_auth_token)):
    """Authenticated administrator endpoint to inspect photos in a shared album."""
    photos = admin_get_shared_album_photos(album_id)
    for p in photos:
        p["thumb_url"] = f"/thumb/{p['rel_path']}"
        p["preview_url"] = f"/preview/{p['rel_path']}"
    return {"status": "ok", "photos": photos, "total_count": len(photos)}

@router.post("/api/admin/shared-albums/{album_id}/revoke")
def admin_revoke_shared_album_endpoint(album_id: int, request: Request, token: str = Depends(get_auth_token)):
    """Admin override to revoke any shared album immediately."""
    success = admin_revoke_shared_album(album_id)
    if not success:
        raise HTTPException(404, "Shared album not found or already revoked.")
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    record_security_event("admin_shared_album_revoked", f"Admin revoked shared album #{album_id}", ip=ip, user_agent=ua)
    return {"status": "ok", "message": f"Shared album #{album_id} has been revoked."}

@router.put("/api/admin/shared-albums/{album_id}/expiry")
def admin_update_expiry_shared_album_endpoint(album_id: int, payload: dict, request: Request, token: str = Depends(get_auth_token)):
    """Admin adjustment of shared album expiry duration (1 to 90 days)."""
    days = payload.get("new_expiry_days")
    if not days or int(days) < 1 or int(days) > 90:
        raise HTTPException(400, "new_expiry_days must be between 1 and 90 days.")
    new_expires_str = admin_update_shared_album_expiry(album_id, int(days))
    if not new_expires_str:
        raise HTTPException(404, "Shared album not found.")
    return {"status": "ok", "new_expires_at": new_expires_str}

@router.delete("/api/admin/shared-albums/{album_id}")
def admin_delete_shared_album_endpoint(album_id: int, request: Request, token: str = Depends(get_auth_token)):
    """Admin deletion of shared album record and mappings. Original photos are strictly preserved."""
    success = admin_delete_shared_album(album_id)
    if not success:
        raise HTTPException(404, "Shared album record not found.")
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    record_security_event("admin_shared_album_deleted", f"Admin deleted shared album record #{album_id}", ip=ip, user_agent=ua)
    return {"status": "ok", "message": f"Shared album record #{album_id} deleted."}

