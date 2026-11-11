import re
import json
import time
import uuid
import base64
import threading
from pathlib import Path
from typing import Optional, List, Tuple, Union
from datetime import datetime

import cv2
import numpy as np
from fastapi import UploadFile, HTTPException
from PIL import Image, ImageOps

from backend.config import (
    DATA, STORAGE, MODELS, THRESHOLD, MAX_UPLOAD,
    logger
)
from backend.db.connection import get_db, parse_date_string
from backend.db.settings import get_setting
from backend.db.reviews import record_match_review
from backend.db.shared_albums import save_search_result_proof
from ai_matching import classify_confidence
import analytics

_face_app = None
_face_app_lock = threading.Lock()
face_lock = _face_app_lock

def get_face_app():
    global _face_app
    if _face_app is None:
        with _face_app_lock:
            if _face_app is None:
                from insightface.app import FaceAnalysis
                app_instance = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
                app_instance.prepare(ctx_id=-1, det_size=(640, 640))
                _face_app = app_instance
    return _face_app

def set_face_app(app_instance):
    global _face_app
    with _face_app_lock:
        _face_app = app_instance

# Multi-event vector index cache: { slug: { "embs": ..., "names": ..., "known": ..., "mtime": ... } }
event_indices = {}
drive_map = {}
_drive_map_mtime = 0

def reload_drive_map():
    global drive_map, _drive_map_mtime
    map_file = DATA / "drive_map.json"
    if map_file.exists():
        try:
            mtime = map_file.stat().st_mtime
            if mtime != _drive_map_mtime:
                drive_map = json.loads(map_file.read_text())
                _drive_map_mtime = mtime
        except Exception:
            pass

reload_drive_map()

def get_event_index(slug: str = "brk-sir"):
    """Loads and caches face embeddings for albums and photos."""
    if slug in {"default", "wedding", "brk-sir", "brksir", ""}:
        data_dir = DATA
    else:
        data_dir = DATA / "events" / slug

    f = data_dir / "embeddings.npy"
    names_file = data_dir / "names.json"

    if not f.exists() or not names_file.exists():
        # Fall back to root DATA
        f = DATA / "embeddings.npy"
        names_file = DATA / "names.json"

    if not f.exists() or not names_file.exists():
        return np.zeros((0, 512), dtype=np.float32), [], set()

    mtime = f.stat().st_mtime
    cached = event_indices.get(slug)
    if not cached or cached["mtime"] != mtime:
        try:
            embs = np.load(f)
            names = json.loads(names_file.read_text())
            known = set(names)
            event_indices[slug] = {"embs": embs, "names": names, "known": known, "mtime": mtime}
        except Exception:
            return np.zeros((0, 512), dtype=np.float32), [], set()

    return event_indices[slug]["embs"], event_indices[slug]["names"], event_indices[slug]["known"]

def crop_face_thumb(img, bbox, target_size=(240, 240)):
    h_img, w_img = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    pad = int(max(bw, bh) * 0.3)
    cx1 = max(0, x1 - pad)
    cy1 = max(0, y1 - pad)
    cx2 = min(w_img, x2 + pad)
    cy2 = min(h_img, y2 + pad)
    crop = img[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return ""
    crop_resized = cv2.resize(crop, target_size, interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", crop_resized, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("utf-8")

# Ephemeral In-Memory Search Session Cache for Zero-Biometric Leakage Pagination
_search_session_cache = {}
_search_session_lock = threading.Lock()
SEARCH_SESSION_TTL = 10800  # 3 hours TTL

def store_search_session(session_key: str, query_emb):
    if not session_key or query_emb is None:
        return
    now = time.time()
    with _search_session_lock:
        if len(_search_session_cache) > 2000:
            stale_keys = [k for k, (_, ts) in _search_session_cache.items() if now - ts > SEARCH_SESSION_TTL]
            for k in stale_keys:
                _search_session_cache.pop(k, None)
        _search_session_cache[session_key] = (query_emb, now)

def get_search_session(session_key: str):
    if not session_key:
        return None
    with _search_session_lock:
        item = _search_session_cache.get(session_key)
        if item:
            emb, ts = item
            if time.time() - ts < SEARCH_SESSION_TTL:
                return emb
            else:
                _search_session_cache.pop(session_key, None)
    return None

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def normalize_base_stem(fname: str) -> str:
    if not fname:
        return ""
    stem = Path(fname).stem.lower().strip()
    stem = re.sub(r'[\s_]*\((?:[0-9]+|copy(?:\s+[0-9]+)?)\)$', '', stem, flags=re.I)
    stem = re.sub(r'[\s_-]+copy(?:[\s_-]*[0-9]+|\([0-9]+\))?$', '', stem, flags=re.I)
    return stem.strip()

def phash_hamming(h1: Optional[str], h2: Optional[str]) -> int:
    if not h1 or not h2 or len(h1) != 16 or len(h2) != 16:
        return 99
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except Exception:
        return 99

def parse_photo_date_info(item: dict) -> dict:
    """Parses date or album name from a photo record matching frontend logic."""
    album = (item.get("album_name") or item.get("album_slug") or "").strip()
    cap_dt = (item.get("captured_at") or "").strip()
    rel = (item.get("rel_path") or "").strip()
    fname = (item.get("filename") or "").strip()

    # 1. Match captured_at (EXIF date)
    if cap_dt:
        d = parse_date_string(cap_dt)
        if d:
            return {"is_date": True, "date_key": d, "raw_album": album or d}

    # 2. Extract date from album name or slug
    d = parse_date_string(album)
    if d:
        return {"is_date": True, "date_key": d, "raw_album": album}

    # 3. Extract date from rel_path folder component
    d = parse_date_string(rel)
    if d:
        return {"is_date": True, "date_key": d, "raw_album": album or d}

    # 4. Extract date from filename
    d = parse_date_string(fname)
    if d:
        return {"is_date": True, "date_key": d, "raw_album": album or d}

    fallback = album or item.get("event_name") or "Celebration Photos"
    return {"is_date": False, "date_key": None, "raw_album": fallback}

def aggregate_photo_dates(photos_list: list) -> dict:
    """Aggregates date counts and album counts across the full dataset of matched photos."""
    date_counts = {}
    other_albums = {}
    newest_date = None
    for p in photos_list:
        parsed = parse_photo_date_info(p)
        if parsed["is_date"] and parsed["date_key"]:
            dk = parsed["date_key"]
            date_counts[dk] = date_counts.get(dk, 0) + 1
            if newest_date is None or dk > newest_date:
                newest_date = dk
        else:
            alb = parsed["raw_album"]
            other_albums[alb] = other_albums.get(alb, 0) + 1
    return {
        "date_counts": date_counts,
        "other_albums": other_albums,
        "newest_date": newest_date,
        "total_matched": len(photos_list)
    }

def query_matched_photos(embs, names, query_emb, event_slug="brk-sir", filter_date=None, filter_album=None, page=1, page_size=50):
    if len(embs) == 0 or query_emb is None:
        return {
            "photos": [],
            "all_photos": [],
            "total_count": 0,
            "unfiltered_total_count": 0,
            "has_more": False,
            "next_page": None,
            "date_summary": {"date_counts": {}, "other_albums": {}, "newest_date": None, "total_matched": 0}
        }

    # Fetch calibrated thresholds from database settings or fallbacks
    try:
        min_thresh = float(get_setting("match_threshold_min", str(THRESHOLD)))
        high_thresh = float(get_setting("match_threshold_high", "0.60"))
        med_thresh = float(get_setting("match_threshold_medium", "0.48"))
    except Exception:
        min_thresh = THRESHOLD
        high_thresh = 0.60
        med_thresh = 0.48

    candidates = {}
    matched_faces_map = {}

    # Support both single face embedding (512D) and multi-face list for group photo discovery
    if isinstance(query_emb, (list, tuple)) and len(query_emb) > 0 and (isinstance(query_emb[0], (list, tuple, np.ndarray))):
        for f_idx, q_single in enumerate(query_emb):
            q_vec = np.array(q_single, dtype=np.float32)
            sims = embs @ q_vec
            for idx in np.where(sims >= min_thresh)[0]:
                n = names[idx]
                score = float(sims[idx])
                if n not in candidates or score > candidates[n]["score"]:
                    candidates[n] = {"rel_path": n, "score": score}
                matched_faces_map.setdefault(n, set()).add(f_idx)
    else:
        q_vec = np.array(query_emb, dtype=np.float32)
        sims = embs @ q_vec
        for idx in np.where(sims >= min_thresh)[0]:
            n = names[idx]
            score = float(sims[idx])
            if n not in candidates or score > candidates[n]["score"]:
                candidates[n] = {"rel_path": n, "score": score}
            matched_faces_map.setdefault(n, set()).add(0)

    ranked_candidates = sorted(candidates.values(), key=lambda x: -x["score"])
    reload_drive_map()

    photo_info = {}
    if ranked_candidates:
        conn = get_db()
        cursor = conn.cursor()
        chunk_size = 200
        for i in range(0, len(ranked_candidates), chunk_size):
            chunk = ranked_candidates[i:i + chunk_size]
            chunk_paths = [item["rel_path"] for item in chunk]
            chunk_fnames = [Path(p).name for p in chunk_paths]
            all_ids = list(set(chunk_paths + chunk_fnames))
            placeholders = ",".join("?" for _ in all_ids)
            cursor.execute(f"""
                SELECT p.id, p.filename, p.rel_path, p.sha256_hash, p.phash, p.file_size_bytes, p.width, p.height,
                       p.is_featured, p.drive_file_id, p.captured_at, p.created_at,
                       a.name as album_name, a.slug as album_slug, 
                       e.name as event_name, e.slug as event_slug
                FROM photos p
                JOIN events e ON p.event_id = e.id
                LEFT JOIN albums a ON p.album_id = a.id
                WHERE (p.rel_path IN ({placeholders}) OR p.filename IN ({placeholders}))
                  AND (e.slug = ? OR ? IN ('brk-sir', 'brksir', 'wedding', 'default', ''))
                  AND p.is_deleted = 0
            """, all_ids + all_ids + [event_slug, event_slug])
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("rel_path"):
                    photo_info[d["rel_path"]] = d
                if d.get("filename"):
                    photo_info.setdefault(d["filename"], d)
        conn.close()

    # Multi-criteria Deduplication & Clean Date Synthesis
    deduped_photos = []
    seen_sha = {}
    seen_stems = {}
    seen_phash = []
    seen_pids = set()

    for item in ranked_candidates:
        n = item["rel_path"]
        fname = Path(n).name
        score = item["score"]
        score_val = round(float(score), 3)

        info = photo_info.get(n) or photo_info.get(fname)
        if not info:
            continue

        p_id = info.get("id")
        if p_id and p_id in seen_pids:
            continue

        album_name = (info.get("album_name") or info.get("album_slug") or "").strip()
        cap_dt = (info.get("captured_at") or "").strip()
        clean_date = parse_date_string(cap_dt) or parse_date_string(album_name) or parse_date_string(n) or parse_date_string(fname)
        if clean_date:
            final_cap_dt = f"{clean_date} 12:00:00" if (not cap_dt or not cap_dt.startswith(clean_date)) else cap_dt
        else:
            final_cap_dt = ""

        conf_info = classify_confidence(score_val, high_thresh, med_thresh, min_thresh)
        photo_entry = {
            "id": p_id,
            "rel_path": info.get("rel_path") or n,
            "filename": info.get("filename") or fname,
            "drive_file_id": info.get("drive_file_id") or drive_map.get(n) or drive_map.get(fname),
            "similarity": score_val,
            "confidence": score_val,
            "confidence_tier": conf_info["tier"],
            "confidence_label": conf_info["label"],
            "matched_faces": sorted(list(matched_faces_map.get(n, [0]))),
            "is_featured": bool(info.get("is_featured", 0)),
            "captured_at": final_cap_dt,
            "created_at": info.get("created_at", ""),
            "album_name": album_name,
            "event_name": info.get("event_name", ""),
            "event_slug": info.get("event_slug", event_slug),
            "width": info.get("width") or 0,
            "height": info.get("height") or 0,
            "file_size_bytes": info.get("file_size_bytes") or 0,
            "sha256_hash": info.get("sha256_hash") or "",
            "phash": info.get("phash") or ""
        }

        stem = normalize_base_stem(photo_entry["filename"])
        sha = photo_entry["sha256_hash"]
        ph = photo_entry["phash"]

        dup_idx = None
        if sha and len(sha) == 64 and sha in seen_sha:
            dup_idx = seen_sha[sha]
        elif stem and stem in seen_stems:
            dup_idx = seen_stems[stem]
        elif ph and len(ph) == 16:
            for ex_ph, ex_idx in seen_phash:
                if phash_hamming(ph, ex_ph) <= 4:
                    dup_idx = ex_idx
                    break

        if dup_idx is not None:
            existing = deduped_photos[dup_idx]
            new_area = photo_entry["width"] * photo_entry["height"]
            ex_area = existing["width"] * existing["height"]
            new_size = photo_entry["file_size_bytes"]
            ex_size = existing["file_size_bytes"]

            is_better = (new_area > ex_area) or (new_area == ex_area and new_size > ex_size) or (new_area == ex_area and photo_entry["is_featured"] and not existing["is_featured"])
            best_date = photo_entry["captured_at"] or existing["captured_at"]
            best_faces = sorted(list(set(existing["matched_faces"] + photo_entry["matched_faces"])))

            if is_better:
                photo_entry["captured_at"] = best_date
                photo_entry["matched_faces"] = best_faces
                deduped_photos[dup_idx] = photo_entry
                if p_id: seen_pids.add(p_id)
            else:
                existing["captured_at"] = best_date
                existing["matched_faces"] = best_faces
        else:
            idx = len(deduped_photos)
            deduped_photos.append(photo_entry)
            if p_id: seen_pids.add(p_id)
            if sha and len(sha) == 64: seen_sha[sha] = idx
            if stem: seen_stems[stem] = idx
            if ph and len(ph) == 16: seen_phash.append((ph, idx))

    photos_data = deduped_photos
    date_summary = aggregate_photo_dates(photos_data)
    unfiltered_total_count = len(photos_data)

    if filter_date:
        photos_data = [p for p in photos_data if parse_photo_date_info(p).get("date_key") == filter_date]
    if filter_album:
        photos_data = [p for p in photos_data if parse_photo_date_info(p).get("raw_album") == filter_album]

    total_count = len(photos_data)

    def photo_sort_key(p):
        feat = 0 if p.get("is_featured") else 1
        parsed = parse_photo_date_info(p)
        if parsed.get("is_date") and parsed.get("date_key"):
            date_rank = 0
            date_val = parsed["date_key"]
        else:
            date_rank = 1
            date_val = ""
        return (feat, date_rank, date_val, p.get("filename", ""))

    photos_data.sort(key=photo_sort_key, reverse=False)

    # When sorting newest first, reverse within chronological groups
    photos_data.sort(key=lambda p: (
        0 if p.get("is_featured") else 1,
        0 if parse_photo_date_info(p).get("is_date") else 1,
        -(datetime.strptime(parse_photo_date_info(p)["date_key"], "%Y-%m-%d").timestamp() if parse_photo_date_info(p).get("is_date") else 0)
    ))

    if page_size > 0:
        start_idx = max(0, (page - 1) * page_size)
        end_idx = start_idx + page_size
        paged_photos = photos_data[start_idx:end_idx]
        has_more = end_idx < total_count
        next_page = page + 1 if has_more else None
    else:
        paged_photos = photos_data
        has_more = False
        next_page = None

    return {
        "photos": paged_photos,
        "all_photos": photos_data,
        "total_count": total_count,
        "unfiltered_total_count": unfiltered_total_count,
        "has_more": has_more,
        "next_page": next_page,
        "date_summary": date_summary
    }

def perform_face_search(
    selfie: UploadFile,
    event_slug: str = "brk-sir",
    face_index: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    filter_date: Optional[str] = None,
    filter_album: Optional[str] = None,
    session_id: Optional[str] = None,
    visitor_id: Optional[str] = None,
    device_type: Optional[str] = None
):
    search_id = str(uuid.uuid4())[:12]
    search_start = time.time()
    analytics.record_search_started(search_id=search_id, session_id=session_id or "anon", visitor_id=visitor_id, device_type=device_type or "desktop", event_slug=event_slug)

    embs, names, known = get_event_index(event_slug)
    if len(names) == 0:
        analytics.record_search_failed(search_id=search_id, session_id=session_id or "anon", error_category="no_index", visitor_id=visitor_id, event_slug=event_slug)
        raise HTTPException(503, "Photos are still being prepared for this event. Please check back soon.")
    data = selfie.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        analytics.record_search_failed(search_id=search_id, session_id=session_id or "anon", error_category="file_too_large", visitor_id=visitor_id, event_slug=event_slug)
        raise HTTPException(413, "Photo is too large. Use one under 10 MB.")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        analytics.record_search_failed(search_id=search_id, session_id=session_id or "anon", error_category="unreadable_image", visitor_id=visitor_id, event_slug=event_slug)
        raise HTTPException(400, "That file is not a readable image.")
    h, w = img.shape[:2]
    if max(h, w) > 1280:
        s = 1280 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))

    face_app = get_face_app()
    with _face_app_lock:
        faces = face_app.get(img)
    if not faces:
        duration_ms = round((time.time() - search_start) * 1000, 1)
        analytics.record_search_failed(search_id=search_id, session_id=session_id or "anon", error_category="no_face_detected", duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)
        raise HTTPException(422, "No face found. Try a clear, front-facing photo in good light.")

    # Sort faces left-to-right based on bounding box
    faces = sorted(faces, key=lambda f: f.bbox[0])

    is_search_all_faces = (str(face_index).strip().lower() == "all") if face_index is not None else False
    numeric_face_index = None
    if face_index is not None and not is_search_all_faces:
        try:
            numeric_face_index = int(face_index)
        except ValueError:
            numeric_face_index = None

    if len(faces) > 1 and face_index is None:
        detected_list = []
        for idx, f in enumerate(faces):
            thumb = crop_face_thumb(img, f.bbox)
            detected_list.append({
                "face_index": idx,
                "thumb": thumb,
                "box": [int(v) for v in f.bbox]
            })
        return {
            "multiple_faces": True,
            "total_faces": len(faces),
            "faces": detected_list
        }

    if is_search_all_faces:
        query_embs = [f.normed_embedding for f in faces]
        selected_face_thumb = crop_face_thumb(img, faces[0].bbox)
        selected_idx = "all"
        active_query_emb = query_embs
        res = query_matched_photos(embs, names, query_embs, event_slug, filter_date, filter_album, page, page_size)
    else:
        if numeric_face_index is not None and 0 <= numeric_face_index < len(faces):
            face = faces[numeric_face_index]
            selected_idx = numeric_face_index
        else:
            face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            selected_idx = faces.index(face)
        selected_face_thumb = crop_face_thumb(img, face.bbox)
        active_query_emb = face.normed_embedding
        res = query_matched_photos(embs, names, face.normed_embedding, event_slug, filter_date, filter_album, page, page_size)

    if session_id:
        store_search_session(session_id, active_query_emb)
    if visitor_id:
        store_search_session(visitor_id, active_query_emb)
    store_search_session(search_id, active_query_emb)

    duration_ms = round((time.time() - search_start) * 1000, 1)
    if res["total_count"] > 0:
        analytics.record_search_completed(search_id=search_id, session_id=session_id or "anon", matches_count=res["total_count"], duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)
    else:
        analytics.record_search_no_results(search_id=search_id, session_id=session_id or "anon", duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)

    try:
        review_dir = DATA / "review_crops"
        review_dir.mkdir(parents=True, exist_ok=True)
        crop_fname = f"query_{search_id}.jpg"
        crop_full_path = review_dir / crop_fname
        if selected_face_thumb and selected_face_thumb.startswith("data:image"):
            b64_data = selected_face_thumb.split(",", 1)[1]
            crop_full_path.write_bytes(base64.b64decode(b64_data))

            conn_rev = get_db()
            c_rev = conn_rev.cursor()
            for p in res["photos"][:3]:
                c_rev.execute("SELECT id FROM photos WHERE rel_path = ? OR filename = ? LIMIT 1", (p["rel_path"], p["filename"]))
                p_row = c_rev.fetchone()
                p_id = p_row["id"] if p_row else None
                record_match_review(
                    search_id=search_id,
                    event_slug=event_slug,
                    query_crop_path=f"review_crops/{crop_fname}",
                    matched_photo_id=p_id,
                    matched_rel_path=p["rel_path"],
                    similarity_score=p["similarity"],
                    confidence_tier=p.get("confidence_tier", "medium")
                )
            conn_rev.close()
    except Exception as e:
        print(f"Match review recording notice: {e}")

    creation_token = None
    all_matched_ids = [p["id"] for p in res.get("all_photos", []) if p.get("id")]
    if all_matched_ids:
        try:
            conn_ev = get_db()
            c_ev = conn_ev.cursor()
            c_ev.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir') LIMIT 1", (event_slug, event_slug))
            erow = c_ev.fetchone()
            conn_ev.close()
            eid = erow["id"] if erow else 1
            _, creation_token = save_search_result_proof(
                search_id=search_id,
                event_id=eid,
                matched_photo_ids=all_matched_ids,
                creator_session_id=session_id or visitor_id or "",
                ttl_seconds=3600
            )
        except Exception as e:
            print(f"Notice: could not record search-result proof: {e}")

    return {
        "search_id": search_id,
        "creation_token": creation_token,
        "multiple_faces": False,
        "count": res["total_count"],
        "total_count": res["total_count"],
        "unfiltered_total_count": res["unfiltered_total_count"],
        "page": page,
        "page_size": page_size if page_size > 0 else res["total_count"],
        "has_more": res["has_more"],
        "next_page": res["next_page"],
        "photos": res["photos"],
        "legacy_photos": [p["rel_path"] for p in res["photos"]],
        "date_summary": res["date_summary"],
        "filter_date": filter_date,
        "filter_album": filter_album,
        "face_thumb": selected_face_thumb,
        "total_faces_in_query": len(faces),
        "selected_face_index": selected_idx,
    }
