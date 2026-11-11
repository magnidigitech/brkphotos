from typing import Optional
from fastapi import APIRouter, Request, Depends, Response

import analytics
from backend.dependencies import get_auth_token
from backend.db.connection import now_ist

router = APIRouter(tags=["admin_analytics"])

@router.post("/api/analytics/event")
async def public_record_analytics_event(request: Request):
    """
    Public endpoint for client-side event tracking (portal_visit, photo_opened, share_clicked).
    Non-blocking, privacy-preserving, rate-conscious.
    """
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    event_type = data.get("event_type")
    if not event_type or not isinstance(event_type, str):
        return {"status": "error", "message": "Missing event_type"}

    # Whitelist allowed client-side event types to prevent pollution
    allowed_types = {"portal_visit", "photo_opened", "share_clicked"}
    if event_type not in allowed_types:
        return {"status": "ignored"}

    session_id = data.get("session_id") or "anon"
    visitor_id = data.get("visitor_id") or ""
    device_type = data.get("device_type") or "desktop"
    photo_path = data.get("photo_path")
    album_name = data.get("album_name")
    event_slug = data.get("event_slug") or "brk-sir"
    client_ip = request.client.host if request.client else ""

    analytics.record_event_async(
        event_type=event_type,
        session_id=session_id,
        visitor_id=visitor_id,
        device_type=device_type,
        photo_path=photo_path,
        album_name=album_name,
        event_slug=event_slug,
        ip_hash=analytics.hash_ip(client_ip),
        metadata=data.get("metadata")
    )
    return {"status": "ok"}


@router.get("/api/admin/analytics/dashboard")
def admin_analytics_dashboard_endpoint(
    range: str = "7d",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    refresh: bool = False,
    token: str = Depends(get_auth_token)
):
    """Returns comprehensive analytics dashboard metrics for the chosen date range."""
    return analytics.get_analytics_dashboard(range_type=range, from_date_str=from_date, to_date_str=to_date, refresh=refresh)


@router.get("/api/admin/analytics/system")
def admin_analytics_system_endpoint(
    refresh: bool = False,
    token: str = Depends(get_auth_token)
):
    """Returns live server resource metrics, queue lengths, and warning indicators."""
    return analytics.get_system_metrics(refresh=refresh)


@router.get("/api/admin/analytics/downloads")
def admin_analytics_downloads_endpoint(
    range: str = "7d",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    token: str = Depends(get_auth_token)
):
    """Returns download jobs history and statistics."""
    dash = analytics.get_analytics_dashboard(range_type=range, from_date_str=from_date, to_date_str=to_date)
    return dash.get("download_analytics", {})


@router.get("/api/admin/analytics/export")
def admin_analytics_export_endpoint(
    range: str = "30d",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    type: str = "daily",
    token: str = Depends(get_auth_token)
):
    """Generates and exports CSV reports for analytics."""
    csv_content = analytics.generate_csv_report(range_type=range, from_date_str=from_date, to_date_str=to_date, report_type=type)
    filename = f"brk_analytics_{type}_{range}_{now_ist().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/api/admin/analytics")
def admin_analytics_metrics(token: str = Depends(get_auth_token)):
    """Legacy backward-compatible summary endpoint using real live analytics data."""
    dash = analytics.get_analytics_dashboard("today")
    ov = dash["overview"]
    lib = dash["library_analytics"]
    return {
        "page_views": ov["unique_visitors"] or ov["total_searches"],
        "selfie_searches": ov["total_searches"],
        "face_matches_found": ov["successful_searches"],
        "gdrive_raw_downloads": ov["zip_downloads"] + ov["original_downloads"],
        "active_galleries": 1,
        "indexed_photos": lib["indexed_photos"],
        "identified_people": lib["people_clusters"]
    }

# ---------------------------------------------------------------------------
# AI Duplicate Detection & Review Endpoints
# ---------------------------------------------------------------------------

