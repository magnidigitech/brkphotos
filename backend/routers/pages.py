from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from backend.config import BASE

router = APIRouter(tags=["pages"])

@router.get("/admin")
def admin_page():
    return FileResponse(
        BASE / "static" / "admin.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

@router.get("/")
def root_index():
    """Serves the primary photos gallery directly at the root landing page."""
    return FileResponse(BASE / "static" / "event.html")

@router.get("/wedding")
def legacy_wedding_redirect():
    """Redirects legacy /wedding handle directly to root landing page."""
    return RedirectResponse(url="/", status_code=301)

@router.get("/sitemap.xml")
def sitemap_xml():
    """Serves the SEO sitemap for search engine indexing."""
    f = BASE / "static" / "sitemap.xml"
    if f.exists():
        return FileResponse(f, media_type="application/xml")
    raise HTTPException(404, "Sitemap not found")

@router.get("/robots.txt")
def robots_txt():
    """Serves search engine crawling guidelines."""
    f = BASE / "static" / "robots.txt"
    if f.exists():
        return FileResponse(f, media_type="text/plain")
    raise HTTPException(404, "Robots not found")

@router.get("/favicon.ico")
def get_favicon():
    """Serves site favicon."""
    f = BASE / "static" / "favicon.ico"
    if f.exists():
        return FileResponse(f, media_type="image/x-icon")
    f_png = BASE / "static" / "images" / "favicon.png"
    if f_png.exists():
        return FileResponse(f_png, media_type="image/png")
    raise HTTPException(404, "Favicon not found")

@router.get("/manifest.json")
def get_manifest():
    """Serves PWA Web App Manifest."""
    f = BASE / "static" / "manifest.json"
    if f.exists():
        return FileResponse(f, media_type="application/manifest+json")
    raise HTTPException(404, "Manifest not found")

@router.get("/sw.js")
def get_sw():
    """Serves PWA Service Worker with root scope permissions."""
    f = BASE / "static" / "sw.js"
    if f.exists():
        return FileResponse(f, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})
    raise HTTPException(404, "Service Worker not found")

@router.get("/images/{filename}")
def serve_header_image(filename: str):
    """Direct route to serve header parallax and logo images."""
    img_path = BASE / "static" / "images" / filename
    if img_path.exists() and img_path.is_file():
        return FileResponse(img_path)
    # Case-insensitive fallback
    for f in (BASE / "static" / "images").glob("*"):
        if f.name.lower() == filename.lower():
            return FileResponse(f)
    raise HTTPException(404, "Image not found")

@router.get("/{slug}")
def event_landing_page(slug: str):
    """Serves static files or redirects legacy handles to root landing page."""
    # Check if static file exists
    static_file = BASE / "static" / slug
    if static_file.exists() and static_file.is_file():
        return FileResponse(static_file)
    # Redirect legacy slugs directly to root landing page
    return RedirectResponse(url="/", status_code=301)

