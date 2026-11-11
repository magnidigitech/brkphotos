import time
import uuid
import base64
import secrets
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, File, UploadFile, Form, Request

import analytics
from backend.db.connection import get_db
from backend.db.shared_albums import save_search_result_proof
from backend.services.face_search import (
    get_event_index,
    get_search_session,
    store_search_session,
    query_matched_photos,
    perform_face_search,
    get_face_app,
    face_lock
)

router = APIRouter(tags=["search"])

@router.post("/api/search")
def default_search(
    selfie: UploadFile = File(...),
    face_index: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(50),
    filter_date: Optional[str] = Form(None),
    filter_album: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    visitor_id: Optional[str] = Form(None),
    device_type: Optional[str] = Form(None)
):
    """Search for matching photos using face recognition with server-side pagination and date summary."""
    return perform_face_search(
        selfie, "brk-sir", face_index=face_index, page=page, page_size=page_size,
        filter_date=filter_date, filter_album=filter_album, session_id=session_id,
        visitor_id=visitor_id, device_type=device_type
    )

@router.post("/api/{slug}/search")
def event_search(
    slug: str,
    selfie: UploadFile = File(...),
    face_index: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(50),
    filter_date: Optional[str] = Form(None),
    filter_album: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    visitor_id: Optional[str] = Form(None),
    device_type: Optional[str] = Form(None)
):
    """Search for matching photos using face recognition with server-side pagination and date summary."""
    return perform_face_search(
        selfie, slug, face_index=face_index, page=page, page_size=page_size,
        filter_date=filter_date, filter_album=filter_album, session_id=session_id,
        visitor_id=visitor_id, device_type=device_type
    )

@router.post("/api/events/{slug}/check-profile-matches")
@router.post("/api/check-profile-matches")
async def check_profile_matches_endpoint(slug: str = "brk-sir", request: Request = None):
    """
    Lightweight dynamic check for returning users with saved face embedding or thumbnail.
    Returns latest total matched photo count and photos list without re-taking selfie.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    embedding = body.get("embedding")
    face_thumb = body.get("face_thumb")
    last_count = int(body.get("last_count", 0) or 0)
    page = int(body.get("page", 1) or 1)
    page_size = int(body.get("page_size", 50) or 50)

    embs, names, _ = get_event_index(slug)
    if len(embs) == 0:
        return {"status": "ok", "count": 0, "total_count": 0, "page": page, "photos": [], "has_more": False, "has_new": False, "new_count": 0}

    session_id = body.get("session_id")
    visitor_id = body.get("visitor_id")
    search_id = body.get("search_id")

    query_emb = get_search_session(session_id) or get_search_session(visitor_id) or get_search_session(search_id)

    if query_emb is None:
        if embedding and isinstance(embedding, list) and len(embedding) == 512:
            query_emb = np.array(embedding, dtype=np.float32)
            norm = np.linalg.norm(query_emb)
            if norm > 0:
                query_emb = query_emb / norm
        elif face_thumb and isinstance(face_thumb, str) and face_thumb.startswith("data:image"):
            try:
                head, b64 = face_thumb.split(",", 1)
                raw_bytes = base64.b64decode(b64)
                nparr = np.frombuffer(raw_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                face_app = get_face_app()
                with face_lock:
                    faces = face_app.get(img)
                if faces:
                    target_f = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                    query_emb = target_f.normed_embedding
                elif img is not None and img.size > 0:
                    h_c, w_c = img.shape[:2]
                    scale = max(640.0 / max(h_c, w_c), 2.5)
                    upscaled = cv2.resize(img, (int(w_c * scale), int(h_c * scale)), interpolation=cv2.INTER_CUBIC)
                    with face_lock:
                        up_faces = face_app.get(upscaled)
                    if up_faces:
                        target_f = max(up_faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                        query_emb = target_f.normed_embedding
            except Exception as e:
                print("Error decoding face_thumb in match check:", e)

        if query_emb is not None:
            if session_id:
                store_search_session(session_id, query_emb)
            if visitor_id:
                store_search_session(visitor_id, query_emb)

    if query_emb is None:
        return {"status": "error", "message": "No face embedding available to query", "count": 0, "total_count": 0, "photos": [], "has_more": False, "has_new": False, "new_count": 0}

    filter_date = body.get("filter_date")
    filter_album = body.get("filter_album")
    search_start = time.time()
    res = query_matched_photos(embs, names, query_emb, slug, filter_date, filter_album, page, page_size)
    total_count = res["total_count"]
    has_new = total_count > last_count if last_count > 0 else False
    new_count = max(0, total_count - last_count) if last_count > 0 else 0

    if page == 1:
        search_id = search_id or str(uuid.uuid4())[:12]
        sess_id = body.get("session_id") or "anon"
        vis_id = body.get("visitor_id")
        duration_ms = round((time.time() - search_start) * 1000, 1)
        if total_count > 0:
            analytics.record_search_completed(search_id=search_id, session_id=sess_id, matches_count=total_count, duration_ms=duration_ms, visitor_id=vis_id, event_slug=slug)
        else:
            analytics.record_search_no_results(search_id=search_id, session_id=sess_id, duration_ms=duration_ms, visitor_id=vis_id, event_slug=slug)

    creation_token = None
    all_matched_ids = [p["id"] for p in res.get("all_photos", []) if p.get("id")]
    if all_matched_ids:
        try:
            conn_ev = get_db()
            c_ev = conn_ev.cursor()
            c_ev.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir') LIMIT 1", (slug, slug))
            erow = c_ev.fetchone()
            conn_ev.close()
            eid = erow["id"] if erow else 1
            proof_sid = search_id or f"srch_{secrets.token_hex(12)}"
            _, creation_token = save_search_result_proof(
                search_id=proof_sid,
                event_id=eid,
                matched_photo_ids=all_matched_ids,
                creator_session_id=session_id or visitor_id or "",
                ttl_seconds=3600
            )
            search_id = proof_sid
        except Exception as e:
            print(f"Notice: could not record check-profile proof: {e}")

    return {
        "status": "ok",
        "search_id": search_id,
        "creation_token": creation_token,
        "count": total_count,
        "total_count": total_count,
        "unfiltered_total_count": res["unfiltered_total_count"],
        "page": page,
        "page_size": page_size if page_size > 0 else total_count,
        "has_more": res["has_more"],
        "next_page": res["next_page"],
        "photos": res["photos"],
        "date_summary": res["date_summary"],
        "embedding": None,  # Biometric privacy: raw facial embeddings are never exposed to public clients
        "filter_date": filter_date,
        "filter_album": filter_album,
        "has_new": has_new,
        "new_count": new_count
    }
