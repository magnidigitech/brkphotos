import secrets
import threading
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

from backend.config import current_task, task_lock
from backend.dependencies import get_auth_token
from backend.db.connection import get_db
from backend.db.people import (
    list_people_for_event, cluster_and_sync_people, get_person_photos
)
from backend.services.indexing import run_reclustering_task
from backend.services.media import reload_drive_map, drive_map

router = APIRouter(tags=["people"])

@router.get("/api/admin/events/{slug}/people")
def admin_event_people(slug: str, token: str = Depends(get_auth_token)):
    people = list_people_for_event(slug)
    if not people:
        cluster_and_sync_people(slug, threshold=0.55)
        people = list_people_for_event(slug)
    return {"people": people}

@router.post("/api/admin/events/{slug}/people/recluster")
def admin_recluster_people(slug: str, payload: dict = None, token: str = Depends(get_auth_token)):
    with task_lock:
        if current_task.get("status") == "running":
            raise HTTPException(409, "A background task is already running.")
        current_task["status"] = "running"
    
    threshold = float(payload.get("threshold", 0.48)) if payload else 0.48
    task_id = f"cl_{secrets.token_hex(4)}"
    threading.Thread(target=run_reclustering_task, args=(slug, threshold, task_id), daemon=True).start()
    return {"status": "started", "task_id": task_id}


@router.put("/api/admin/events/{slug}/people/{person_id}")
def admin_rename_person(slug: str, person_id: int, payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name is required.")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE person_profiles SET name = ? 
        WHERE id = ? AND event_id = (SELECT id FROM events WHERE slug = ?)
    """, (name, person_id, slug))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@router.get("/api/admin/events/{slug}/people/{person_id}/photos")
def admin_person_photos(slug: str, person_id: int, token: str = Depends(get_auth_token)):
    photos = get_person_photos(slug, person_id)
    return {"photos": photos}

@router.get("/api/public/people")
def public_people_all():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_event_people(slug)

@router.get("/api/public/people/{person_id}/photos")
def public_person_photos_direct(person_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_person_photos(slug, person_id)

@router.get("/api/public/events/{slug}/people")
def public_event_people(slug: str):
    people = list_people_for_event(slug)
    return {"people": people}

@router.get("/api/public/events/{slug}/people/{person_id}/photos")
def public_person_photos(slug: str, person_id: int):
    reload_drive_map()
    photos = get_person_photos(slug, person_id)
    for p in photos:
        if not p.get("drive_file_id"):
            p["drive_file_id"] = drive_map.get(p["filename"]) or drive_map.get(p["rel_path"])
    return {"photos": photos}

