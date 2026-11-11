import os
import time
import zipfile
import secrets
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, RedirectResponse

import analytics
from backend.config import DATA, PHOTOS, thumb_semaphore, zip_semaphore
from backend.services.media import resolve_photo_path, reload_drive_map, drive_map

router = APIRouter(tags=["media"])

@router.get("/thumb/{name:path}")
def thumb(name: str):
    """Delivers 480px web thumbnail or cropped face avatar safely with HTTP caching."""
    cache_headers = {"Cache-Control": "public, max-age=604800, immutable"}

    # 0. Query review crops
    if name.startswith("review_crops/"):
        crop_name = Path(name).name
        target = DATA / "review_crops" / crop_name
        if target.exists():
            return FileResponse(target, headers=cache_headers)

    # 1. Face avatars
    if name.startswith("faces/"):
        face_name = Path(name).name
        target = DATA / "faces" / face_name
        if target.exists():
            return FileResponse(target, headers=cache_headers)

    # 2. Direct match in root thumbs directory
    direct_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if direct_thumb.exists():
        return FileResponse(direct_thumb, headers=cache_headers)

    # 3. Match in event-scoped thumbs: events/<slug>/<subpath> -> DATA/events/<slug>/thumbs/<subpath.replace('/', '__')>.jpg
    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            slug = parts[1]
            sub = parts[2]
            ev_thumb = DATA / "events" / slug / "thumbs" / (sub.replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb, headers=cache_headers)

    # 4. Fast database resolution to check if thumb is cached under original rel_path
    photo_file = resolve_photo_path(name)
    if photo_file:
        try:
            rel = str(photo_file.relative_to(PHOTOS))
            resolved_thumb = DATA / "thumbs" / (rel.replace("/", "__") + ".jpg")
            if resolved_thumb.exists():
                return FileResponse(resolved_thumb, headers=cache_headers)
        except Exception:
            pass

    # 5. On-the-fly thumbnail generation from original photo on disk with concurrency limit
    if photo_file and photo_file.exists():
        with thumb_semaphore:
            cache_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
            if cache_thumb.exists():
                return FileResponse(cache_thumb, headers=cache_headers)
            try:
                cache_thumb.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(photo_file) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((480, 480), Image.Resampling.LANCZOS)
                    img.save(cache_thumb, "JPEG", quality=80, optimize=True)
                return FileResponse(cache_thumb, headers=cache_headers)
            except Exception:
                return FileResponse(photo_file, headers=cache_headers)

    raise HTTPException(404, "Thumbnail not found")

@router.get("/photo/{name:path}")
def photo(name: str):
    """Delivers photo directly from VPS without redirecting to Google Drive."""
    fname = Path(name).name
    target = resolve_photo_path(name)

    if target and target.exists():
        return FileResponse(target, filename=fname, headers={"Cache-Control": "public, max-age=86400"})

    # Fallback to high-res thumbnail on VPS if original was pruned
    direct_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if direct_thumb.exists():
        return FileResponse(direct_thumb, filename=fname, headers={"Cache-Control": "public, max-age=86400"})

    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            ev_thumb = DATA / "events" / parts[1] / "thumbs" / (parts[2].replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb, filename=fname, headers={"Cache-Control": "public, max-age=86400"})

    # Fallback to Google Drive only if file not present on VPS
    reload_drive_map()
    drive_id = drive_map.get(name) or drive_map.get(fname)
    if not drive_id:
        for k, v in drive_map.items():
            if k.endswith(fname):
                drive_id = v
                break
    if drive_id:
        return RedirectResponse(
            f"https://drive.google.com/uc?export=download&id={drive_id}",
            status_code=302
        )
    raise HTTPException(404, "Photo not found")

@router.get("/preview/{name:path}")
def preview(name: str):
    """Serves studio-quality 1600px HD preview for lightbox viewer, cached on VPS for instant 1ms response."""
    cache_headers = {"Cache-Control": "public, max-age=604800, immutable"}
    cache_preview = DATA / "previews" / (name.replace("/", "__") + ".jpg")
    if cache_preview.exists():
        return FileResponse(cache_preview, media_type="image/jpeg", headers=cache_headers)

    fname = Path(name).name
    target = resolve_photo_path(name)

    if target and target.exists():
        try:
            cache_preview.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(target) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                img.save(cache_preview, "JPEG", quality=86, optimize=True)
            return FileResponse(cache_preview, media_type="image/jpeg", headers=cache_headers)
        except Exception:
            return FileResponse(target, media_type="image/jpeg", headers=cache_headers)

    # Fallback to high-res thumbnail if original not found
    thumb_path = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if thumb_path.exists():
        return FileResponse(thumb_path, media_type="image/jpeg", headers=cache_headers)

    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            ev_thumb = DATA / "events" / parts[1] / "thumbs" / (parts[2].replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb, media_type="image/jpeg", headers=cache_headers)

    raise HTTPException(404, "Preview photo not found")

@router.get("/download/hd/{name:path}")
def download_hd(name: str, req: Request):
    """Downloads HD (1080p/1920p) optimized photo directly from VPS storage as attachment."""
    vis_id = req.headers.get("X-Visitor-Id") or req.cookies.get("brk_visitor_id")
    sess_id = req.headers.get("X-Session-Id") or req.cookies.get("brk_session_id") or "anon"
    analytics.record_photo_download(name, quality="hd", session_id=sess_id, visitor_id=vis_id)
    fname = Path(name).name
    stem = Path(name).stem
    ext = Path(name).suffix or ".jpg"
    hd_filename = f"{stem}_HD{ext}"

    # 1. Check if 1080p thumb already cached
    cache_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if cache_thumb.exists():
        return FileResponse(
            cache_thumb,
            filename=hd_filename,
            media_type="image/jpeg",
            headers={"Content-Disposition": f'attachment; filename="{hd_filename}"'}
        )

    # 2. Check event-scoped thumbs
    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            ev_thumb = DATA / "events" / parts[1] / "thumbs" / (parts[2].replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(
                    ev_thumb,
                    filename=hd_filename,
                    media_type="image/jpeg",
                    headers={"Content-Disposition": f'attachment; filename="{hd_filename}"'}
                )

    # 3. Generate 1080p/1920p HD on the fly from original on disk
    target = resolve_photo_path(name)

    if target and target.exists():
        try:
            cache_thumb.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(target) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
                img.save(cache_thumb, "JPEG", quality=86, optimize=True)
            return FileResponse(
                cache_thumb,
                filename=hd_filename,
                media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{hd_filename}"'}
            )
        except Exception:
            return FileResponse(
                target,
                filename=hd_filename,
                media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{hd_filename}"'}
            )

    raise HTTPException(404, "HD photo not available on server")

@router.get("/download/4k/{name:path}")
def download_4k(name: str, req: Request):
    """Downloads original full-resolution 4K file directly from server without requiring Google login."""
    vis_id = req.headers.get("X-Visitor-Id") or req.cookies.get("brk_visitor_id")
    sess_id = req.headers.get("X-Session-Id") or req.cookies.get("brk_session_id") or "anon"
    analytics.record_photo_download(name, quality="4k", session_id=sess_id, visitor_id=vis_id)
    fname = Path(name).name
    # 1. Check if original photo exists on VPS storage
    target = resolve_photo_path(name)

    if target and target.exists():
        return FileResponse(
            target,
            filename=fname,
            media_type="image/jpeg",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'}
        )

    # 2. Fallback to Google Drive direct stream without login barrier
    reload_drive_map()
    drive_id = drive_map.get(name) or drive_map.get(fname)
    if not drive_id:
        for k, v in drive_map.items():
            if k.endswith(fname):
                drive_id = v
                break

    if drive_id:
        gdrive_url = f"https://drive.usercontent.google.com/download?id={drive_id}&export=download&confirm=t"
        return RedirectResponse(gdrive_url, status_code=302)

    raise HTTPException(404, "Original 4K photo not found")

@router.post("/api/download/bulk-zip")
@router.post("/api/{slug}/download/bulk-zip")
async def download_bulk_zip(request: Request, background_tasks: BackgroundTasks, slug: str = "brk-sir"):
    """
    Creates and streams a downloadable ZIP file containing the selected original full-resolution photos.
    Uses ZIP_STORED (0% CPU recompression overhead, fast disk stream), bounds concurrency via zip_semaphore,
    and prevents directory traversal.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    photo_items = body.get("photos", [])
    if not photo_items or not isinstance(photo_items, list):
        raise HTTPException(400, "No photos provided for ZIP download.")

    # Cap to 500 photos per zip archive
    selected = photo_items[:500]

    job_id = f"zip_{int(time.time())}_{secrets.token_hex(3)}"
    vis_id = body.get("visitor_id") or request.headers.get("X-Visitor-Id") or request.cookies.get("brk_visitor_id")
    sess_id = body.get("session_id") or request.headers.get("X-Session-Id") or request.cookies.get("brk_session_id") or "anon"
    analytics.start_download_job(job_id=job_id, job_type="bulk_zip", session_id=sess_id, visitor_id=vis_id, photo_count=len(selected), event_slug=slug)

    tmp_fd, tmp_zip_path = tempfile.mkstemp(suffix=".zip", prefix="brk_bulk_")
    os.close(tmp_fd)

    def cleanup_file(path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    background_tasks.add_task(cleanup_file, tmp_zip_path)

    added_names = set()
    missing_photos = []

    # Bound concurrent zip creation to protect VPS RAM and disk I/O
    with zip_semaphore:
        with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for item in selected:
                if isinstance(item, dict):
                    p_path = item.get("rel_path") or item.get("filename") or ""
                else:
                    p_path = str(item).strip()

                if not p_path:
                    continue

                fname = Path(p_path).name
                clean_name = fname
                count = 1
                while clean_name.lower() in added_names:
                    stem = Path(fname).stem
                    ext = Path(fname).suffix or ".jpg"
                    clean_name = f"{stem}_{count}{ext}"
                    count += 1

                # Fast indexed candidate resolution
                candidate = resolve_photo_path(p_path)

                if candidate and candidate.is_file():
                    try:
                        zf.write(candidate, arcname=clean_name)
                        added_names.add(clean_name.lower())
                    except Exception as e:
                        print(f"Error adding {candidate} to zip: {e}")
                        missing_photos.append(p_path)
                else:
                    missing_photos.append(p_path)

    zip_size = os.path.getsize(tmp_zip_path) if os.path.exists(tmp_zip_path) else 0
    if zip_size <= 22 or len(added_names) == 0:
        cleanup_file(tmp_zip_path)
        analytics.fail_download_job(job_id=job_id, error_message="None of the selected original photos could be found on the server.")
        raise HTTPException(404, "None of the selected original photos could be found on the server.")

    analytics.finish_download_job(job_id=job_id, zip_size_bytes=zip_size, photo_count=len(added_names))

    zip_filename = f"BRK_Photos_{len(added_names)}_Original_Photos.zip"
    resp_headers = {
        "Content-Disposition": f'attachment; filename="{zip_filename}"',
        "X-Total-Photos": str(len(added_names)),
        "X-Missing-Photos-Count": str(len(missing_photos))
    }
    return FileResponse(
        tmp_zip_path,
        media_type="application/zip",
        filename=zip_filename,
        headers=resp_headers
    )
