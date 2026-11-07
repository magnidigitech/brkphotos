import os, sys, threading, subprocess, secrets, shutil, zipfile, io, re, time, base64, tempfile, uuid
from typing import Optional

# Limit CPU threads to prevent memory and CPU spikes on VPS
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

from datetime import datetime
from pathlib import Path
from PIL import Image, ImageOps
import cv2
import numpy as np
import requests
from fastapi import FastAPI, File, HTTPException, UploadFile, Request, Depends, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from insightface.app import FaceAnalysis
import json
import analytics

from database import (
    get_db, init_db, migrate_existing_data,
    cluster_and_sync_people, list_people_for_event, get_person_photos,
    create_task_record, update_task_progress, get_latest_task, list_task_history, get_task_by_id,
    stop_running_tasks,
    enqueue_upload_task, claim_next_queued_task, update_queue_task,
    get_queue_tasks, get_queue_task_by_id, get_queue_task_logs, cancel_queue_task, retry_queue_task,
    clear_completed_queue_tasks, get_queue_stats, reset_stale_running_queue_tasks,
    create_automation, get_automations, get_automation_by_id, update_automation,
    delete_automation, toggle_automation, record_automation_history, get_automation_history,
    get_due_automations, advance_automation_schedule, is_automation_task_pending, now_ist, extract_photo_capture_date,
    get_setting, set_setting, get_all_settings,
    record_indexing_error, get_indexing_errors,
    record_match_review, get_match_reviews, update_match_feedback,
    record_duplicate_pair, get_duplicate_groups, resolve_duplicate_group
)
from ai_matching import (
    compute_sha256, compute_dhash, assess_photo_quality,
    classify_confidence, scan_and_record_duplicates
)

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
PHOTOS = STORAGE / "photos"
DATA = STORAGE / "data"
MODELS = STORAGE / "models"
QUEUE_DIR = STORAGE / "queue"
PHOTOS.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.40"))
MAX_UPLOAD = 10 * 1024 * 1024
ADMIN_KEY = os.environ.get("ADMIN_KEY", "Magni@3939")

# Initialize database on startup
init_db()
migrate_existing_data()
reset_stale_running_queue_tasks()

face_app = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
face_app.prepare(ctx_id=-1, det_size=(640, 640))
lock = threading.Lock()

# Multi-event vector index cache: { slug: { "embs": ..., "names": ..., "known": ..., "mtime": ... } }
event_indices = {}
drive_map = {}
thumb_semaphore = threading.Semaphore(2)

def reload_drive_map():
    global drive_map
    map_file = DATA / "drive_map.json"
    if map_file.exists():
        try:
            drive_map = json.loads(map_file.read_text())
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

def optimize_photo_inplace(path: Path, max_dim=1920, quality=82):
    try:
        if path.stat().st_size <= 900 * 1024:
            return
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            img.save(path, "JPEG", quality=quality, optimize=True)
    except Exception:
        pass

# Admin session & tasks
active_tokens = set()
task_lock = threading.Lock()
current_task = {"status": "idle", "type": None, "message": "Ready", "event": None}
log_buffer = []
active_process = None
task_cancel_event = threading.Event()

def append_log(msg: str):
    global log_buffer
    log_buffer.append(msg)
    if len(log_buffer) > 500:
        log_buffer.pop(0)
    try:
        with open(STORAGE / "index.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def get_auth_token(req: Request) -> str:
    token = req.headers.get("X-Admin-Token") or req.cookies.get("admin_token")
    if not token or token not in active_tokens:
        raise HTTPException(401, "Unauthorized. Please log in with the admin key.")
    return token

def download_gdrive_file(url: str, dest_path: Path) -> tuple[bool, str]:
    """Downloads a single Google Drive file directly via HTTP stream with confirmation token support."""
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        resp = session.get(url, stream=True, timeout=45)
        
        # Check for virus scan confirmation token if returned
        for k, v in resp.cookies.items():
            if k.startswith("download_warning"):
                sep = "&" if "?" in url else "?"
                resp = session.get(f"{url}{sep}confirm={v}", stream=True, timeout=45)
                break
                
        # Also check html body for confirm link if returned as html
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type:
            text = resp.text[:2000]
            m = re.search(r'href="(/uc\?export=download[^"]+confirm=([^"&]+)[^"]*)"', text)
            if m:
                confirm_url = "https://drive.google.com" + m.group(1).replace("&amp;", "&")
                resp = session.get(confirm_url, stream=True, timeout=45)

        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if task_cancel_event.is_set():
                    if tmp_path.exists():
                        try: tmp_path.unlink()
                        except Exception: pass
                    return False, "Cancelled"
                if chunk:
                    f.write(chunk)

        if tmp_path.exists():
            tmp_path.replace(dest_path)
            return True, "OK"
        return False, "File missing after write"
    except Exception as e:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        return False, str(e)

def run_indexing_task(event_slug="brk-sir", task_id=None):
    global current_task, active_process
    display_title = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    owns_task = False
    if not task_id:
        owns_task = True
        task_id = f"idx_{secrets.token_hex(4)}"
        create_task_record(task_id, "face_index", display_title, "All Albums", "Local Storage")

    # CRITICAL: Always reset task_cancel_event so new tasks run cleanly without prior cancel flags
    task_cancel_event.clear()

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM photos WHERE is_deleted = 0")
    total_lib_photos = c.fetchone()[0] or 0
    conn.close()

    init_pct = 80 if not owns_task else 5
    with task_lock:
        current_task["task_id"] = task_id
        current_task["status"] = "running"
        current_task["type"] = "indexing"
        current_task["event"] = display_title
        current_task["progress_pct"] = init_pct
        current_task["total_files"] = total_lib_photos
        current_task["processed_files"] = 0
        current_task["message"] = f"Initializing AI face indexing for '{display_title}'..."

    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Starting face indexing for '{display_title}'")
    update_task_progress(task_id, progress_pct=init_pct,
                         total_files=total_lib_photos, processed_files=0,
                         current_step=f"Scanning library photos for '{display_title}'...",
                         log_line=f"Face indexing started for '{display_title}' ({total_lib_photos} photos in library)")

    try:
        idx_env = os.environ.copy()
        idx_env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [sys.executable, "-u", str(BASE / "index_photos.py"), "--event", event_slug, "--task-id", task_id or ""],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(BASE),
            env=idx_env
        )
        active_process = proc
        for line in proc.stdout:
            if task_cancel_event.is_set():
                proc.kill()
                break
            txt = line.rstrip()
            append_log(txt)

            # Match total scan count
            m_scan = re.search(r"Scan complete:\s*(\d+)\s*total photos found", txt)
            if m_scan:
                tot_p = int(m_scan.group(1))
                with task_lock:
                    current_task["total_files"] = tot_p
                update_task_progress(task_id, total_files=tot_p, log_line=txt)
                continue

            # Match already indexed and pending
            m_pending = re.search(r"(\d+)\s*already indexed\.\s*(\d+)\s*pending", txt)
            if m_pending:
                done_cnt = int(m_pending.group(1))
                pending_cnt = int(m_pending.group(2))
                tot_p = done_cnt + pending_cnt
                if pending_cnt == 0:
                    pct = 90 if not owns_task else 75
                    msg = f"All {done_cnt} photos indexed. Running facial clustering check..."
                    with task_lock:
                        current_task["processed_files"] = done_cnt
                        current_task["total_files"] = tot_p
                        current_task["progress_pct"] = pct
                        current_task["message"] = msg
                    update_task_progress(task_id, progress_pct=pct, processed_files=done_cnt,
                                         total_files=tot_p, current_step=msg, log_line=txt)
                else:
                    pct = int(80 + (done_cnt / max(tot_p, 1)) * 16) if not owns_task else int(5 + (done_cnt / max(tot_p, 1)) * 75)
                    msg = f"Resuming: {done_cnt}/{tot_p} photos indexed ({pending_cnt} pending)..."
                    with task_lock:
                        current_task["processed_files"] = done_cnt
                        current_task["total_files"] = tot_p
                        current_task["progress_pct"] = pct
                        current_task["message"] = msg
                    update_task_progress(task_id, progress_pct=pct, processed_files=done_cnt,
                                         total_files=tot_p, current_step=msg, log_line=txt)
                continue

            # Match "All X photos are already indexed! Running clustering check..."
            m_all = re.search(r"All\s*(\d+)\s*photos are already indexed", txt)
            if m_all:
                done_cnt = int(m_all.group(1))
                pct = 92 if not owns_task else 80
                msg = f"All {done_cnt} photos indexed. Running facial clustering..."
                with task_lock:
                    current_task["processed_files"] = done_cnt
                    current_task["total_files"] = done_cnt
                    current_task["progress_pct"] = pct
                    current_task["message"] = msg
                update_task_progress(task_id, progress_pct=pct, processed_files=done_cnt,
                                     total_files=done_cnt, current_step=msg, log_line=txt)
                continue

            # Parse live progress e.g. "Progress: 8649/13557 (64%) [Batch: 1/4909]"
            m_prog = re.search(r"Progress:\s*(\d+)/(\d+)", txt)
            if m_prog:
                cur_p = int(m_prog.group(1))
                tot_p = int(m_prog.group(2))
                m_batch = re.search(r"\[Batch:\s*(\d+)/(\d+)\]", txt)
                batch_str = f" [New: {m_batch.group(1)}/{m_batch.group(2)}]" if m_batch else ""
                calc_pct = int((cur_p / max(tot_p, 1)) * 100) if owns_task else int(80 + (cur_p / max(tot_p, 1)) * 19)
                step_msg = f"Indexed {cur_p}/{tot_p} photos ({int((cur_p / max(tot_p, 1)) * 100)}%){batch_str}..."
                with task_lock:
                    current_task["processed_files"] = cur_p
                    current_task["total_files"] = tot_p
                    current_task["progress_pct"] = calc_pct
                    current_task["message"] = step_msg
                update_task_progress(task_id, progress_pct=calc_pct, processed_files=cur_p, total_files=tot_p,
                                     current_step=step_msg, log_line=txt)
                continue

            # Parse clustering milestones
            if "Running facial clustering" in txt or "Running clustering check" in txt:
                pct = 94 if not owns_task else 88
                msg = "Clustering facial embeddings into people profiles..."
                with task_lock:
                    current_task["progress_pct"] = pct
                    current_task["message"] = msg
                update_task_progress(task_id, progress_pct=pct, current_step=msg, log_line=txt)
                continue

            if "Clustering complete" in txt:
                pct = 97 if not owns_task else 94
                msg = "Face clustering finished. Validating face avatars..."
                with task_lock:
                    current_task["progress_pct"] = pct
                    current_task["message"] = msg
                update_task_progress(task_id, progress_pct=pct, current_step=msg, log_line=txt)
                continue

            if "Face crop" in txt or "face_crop" in txt.lower():
                pct = 99 if not owns_task else 97
                msg = "Face crops and avatars synced successfully."
                with task_lock:
                    current_task["progress_pct"] = pct
                    current_task["message"] = msg
                update_task_progress(task_id, progress_pct=pct, current_step=msg, log_line=txt)
                continue

            update_task_progress(task_id, log_line=txt)

        proc.wait()
        active_process = None

        if task_cancel_event.is_set():
            append_log("Indexing cancelled by administrator.")
            with task_lock:
                current_task["status"] = "cancelled"
                current_task["message"] = "Task stopped by admin."
            update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.", log_line="Indexing cancelled by administrator.")
            return

        if proc.returncode != 0:
            err_msg = f"Indexing process terminated with exit code {proc.returncode}."
            append_log(err_msg)
            with task_lock:
                current_task["status"] = "error"
                current_task["message"] = err_msg
            update_task_progress(task_id, status="failed", current_step=err_msg, log_line=err_msg)
            return

        get_event_index(event_slug)
        now_ts = now_ist().strftime("%H:%M:%S")
        append_log(f"[{now_ts}] --- Face Indexing for '{event_slug}' Completed (exit code {proc.returncode}) ---")
        
        if owns_task:
            final_files = current_task.get("total_files") or total_lib_photos
            with task_lock:
                current_task["status"] = "completed"
                current_task["progress_pct"] = 100
                current_task["total_files"] = final_files
                current_task["processed_files"] = final_files
                current_task["message"] = f"Face indexing completed successfully for '{event_slug}'."
            update_task_progress(task_id, progress_pct=100, status="completed",
                                 processed_files=final_files, total_files=final_files,
                                 current_step=f"Face indexing completed successfully for '{event_slug}'.",
                                 log_line="Face indexing finished successfully.")
    except Exception as e:
        active_process = None
        append_log(f"Indexing error: {e}")
        with task_lock:
            current_task["status"] = "error"
            current_task["message"] = f"Error: {e}"
        update_task_progress(task_id, status="failed", current_step=f"Error: {e}", log_line=f"ERROR: {e}")

def run_reclustering_task(event_slug="brk-sir", threshold=0.48, task_id=None):
    global current_task
    display_title = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    if not task_id:
        task_id = f"cl_{secrets.token_hex(4)}"

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM photos WHERE is_deleted = 0")
    total_lib_photos = c.fetchone()[0] or 0
    conn.close()

    create_task_record(task_id, "recluster", display_title, "All People & Faces", "Existing Embeddings")
    task_cancel_event.clear()

    with task_lock:
        current_task["task_id"] = task_id
        current_task["status"] = "running"
        current_task["type"] = "reclustering"
        current_task["event"] = display_title
        current_task["progress_pct"] = 15
        current_task["total_files"] = total_lib_photos
        current_task["processed_files"] = total_lib_photos
        current_task["message"] = f"Face reclustering in progress for '{display_title}'..."

    now_ts = now_ist().strftime("%H:%M:%S")
    append_log(f"[{now_ts}] Recluster requested by admin for '{display_title}' (threshold: {threshold})")
    update_task_progress(task_id, progress_pct=15, total_files=total_lib_photos, processed_files=total_lib_photos,
                         current_step="Loading existing face embeddings...",
                         log_line=f"Recluster requested by admin for '{display_title}'")

    try:
        now_ts = now_ist().strftime("%H:%M:%S")
        append_log(f"[{now_ts}] Loading existing face records from database and vector index...")
        embs, names, _ = get_event_index(event_slug)
        if len(embs) == 0:
            now_ts = now_ist().strftime("%H:%M:%S")
            msg = f"[{now_ts}] No face records found to cluster."
            append_log(msg)
            with task_lock:
                current_task["status"] = "completed"
                current_task["progress_pct"] = 100
                current_task["message"] = msg
            update_task_progress(task_id, status="completed", progress_pct=100, current_step=msg, log_line=msg)
            return

        now_ts = now_ist().strftime("%H:%M:%S")
        unique_photos = len(set(names))
        append_log(f"[{now_ts}] Found {len(embs):,} face records across {unique_photos:,} photos.")
        append_log(f"[{now_ts}] Starting face clustering (similarity threshold: {threshold})...")
        update_task_progress(task_id, progress_pct=40,
                             current_step=f"Clustering {len(embs):,} faces across {unique_photos:,} photos...",
                             log_line=f"Found {len(embs):,} face records. Starting face clustering...")

        if task_cancel_event.is_set():
            append_log("Reclustering cancelled by administrator.")
            with task_lock:
                current_task["status"] = "cancelled"
                current_task["message"] = "Task stopped by admin."
            update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.", log_line="Reclustering cancelled by administrator.")
            return

        now_ts = now_ist().strftime("%H:%M:%S")
        append_log(f"[{now_ts}] Clustering in progress: computing centroid merges and face-to-person relationships...")
        update_task_progress(task_id, progress_pct=65, current_step="Merging cluster centroids and syncing database...", log_line="Clustering in progress...")

        res = cluster_and_sync_people(event_slug, threshold=threshold, cancel_check=lambda: task_cancel_event.is_set())

        if task_cancel_event.is_set() or res.get("status") == "cancelled":
            append_log(f"[{now_ist().strftime('%H:%M:%S')}] Reclustering cancelled by administrator.")
            with task_lock:
                current_task["status"] = "cancelled"
                current_task["message"] = "Task stopped by admin."
            update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.", log_line="Reclustering cancelled by administrator.")
            return

        now_ts = now_ist().strftime("%H:%M:%S")
        people_count = res.get("people_count", 0)
        append_log(f"[{now_ts}] Clustering completed successfully: {people_count} people profiles created/updated.")
        append_log(f"[{now_ts}] Updated people records and face relationships.")
        
        with task_lock:
            current_task["status"] = "completed"
            current_task["progress_pct"] = 100
            current_task["total_files"] = unique_photos
            current_task["processed_files"] = unique_photos
            current_task["message"] = f"Clustering completed ({people_count} people)"
        update_task_progress(task_id, status="completed", progress_pct=100,
                             total_files=unique_photos, processed_files=unique_photos,
                             current_step=f"Clustering completed ({people_count} people)",
                             log_line=f"Clustering completed successfully. {people_count} people updated.")

    except Exception as e:
        now_ts = datetime.now().strftime("%H:%M:%S")
        err_msg = f"Reclustering error: {e}"
        append_log(f"[{now_ts}] ERROR: {err_msg}")
        with task_lock:
            current_task["status"] = "error"
            current_task["message"] = err_msg
        update_task_progress(task_id, status="failed", current_step=err_msg, log_line=f"ERROR: {err_msg}")


def sync_photos_to_database(dest_dir: Path, event_slug: str, drive_map: dict, allowed_set: set) -> int:
    """
    Progressively scans downloaded photos on disk, auto-creates/matches subfolder albums,
    and upserts photo records into SQLite. Can be safely called periodically during long imports.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')", (event_slug, event_slug))
    erow = cursor.fetchone()
    if not erow:
        conn.close()
        return 0
    eid = erow["id"]
    album_cache = {}

    # Pre-cache existing albums
    cursor.execute("SELECT id, slug, name FROM albums WHERE event_id = ?", (eid,))
    for a in cursor.fetchall():
        album_cache[a["slug"]] = a["id"]
        album_cache[a["name"]] = a["id"]

    registered_count = 0
    for img_path in dest_dir.rglob("*"):
        if img_path.is_file() and img_path.suffix.lower().strip(".") in allowed_set and not img_path.name.startswith("."):
            rel_to_dest = img_path.relative_to(dest_dir)
            sub_parts = rel_to_dest.parent.parts
            if sub_parts:
                album_name = sub_parts[-1]
            else:
                album_name = dest_dir.name if dest_dir != PHOTOS else "BRK Photos"

            album_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", album_name.lower()).strip("-")
            if not album_slug:
                album_slug = "brk-photos"

            if album_slug not in album_cache:
                cursor.execute("SELECT id FROM albums WHERE event_id = ? AND (slug = ? OR name = ?)", (eid, album_slug, album_name))
                arow = cursor.fetchone()
                if not arow:
                    cursor.execute("""
                        INSERT INTO albums (event_id, slug, name, sort_order)
                        VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM albums WHERE event_id = ?))
                    """, (eid, album_slug, album_name, eid))
                    aid = cursor.lastrowid
                else:
                    aid = arow["id"]
                album_cache[album_slug] = aid
                album_cache[album_name] = aid

            album_id = album_cache[album_slug]
            rel = str(img_path.relative_to(PHOTOS))
            fsize = img_path.stat().st_size
            did = drive_map.get(rel) or drive_map.get(img_path.name)
            if not did:
                for k, v in drive_map.items():
                    if k.endswith(img_path.name):
                        did = v
                        break
            cursor.execute("""
                INSERT INTO photos (event_id, album_id, filename, rel_path, drive_file_id, file_size_bytes, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(rel_path) DO UPDATE SET
                    event_id = excluded.event_id,
                    album_id = excluded.album_id,
                    drive_file_id = COALESCE(excluded.drive_file_id, photos.drive_file_id),
                    file_size_bytes = excluded.file_size_bytes,
                    is_deleted = 0
            """, (eid, album_id, img_path.name, rel, did, fsize))
            registered_count += 1
            
    conn.commit()
    conn.close()
    return registered_count

def run_gdrive_download(url: str, dest_dir: Path, rel_prefix: str, event_slug: str, allowed_exts: list = None, task_id: str = None):
    global current_task, drive_map, active_process
    display_title = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    task_cancel_event.clear()
    if not task_id:
        task_id = f"gdrive_{secrets.token_hex(4)}"
        create_task_record(task_id, "gdrive_ingest", display_title, dest_dir.name, url)

    if not allowed_exts:
        allowed_exts = ["jpg", "jpeg", "png", "webp"]
    allowed_set = {x.lower().strip(".") for x in allowed_exts}

    with task_lock:
        current_task = {
            "task_id": task_id,
            "status": "running",
            "type": "gdrive_ingest",
            "event": display_title,
            "target_folder": dest_dir.name,
            "source_url": url,
            "progress_pct": 5,
            "processed_files": 0,
            "total_files": 0,
            "storage_saved_mb": 0.0,
            "message": f"Scanning Google Drive folder into '{dest_dir.name}'...",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Google Drive download for '{display_title}', folder '{dest_dir.name}' from {url}")
    update_task_progress(task_id, progress_pct=5, current_step="Scanning Google Drive folder manifest...",
                         log_line=f"Target: {dest_dir.name} | Formats: {', '.join(sorted(allowed_set))} | Source: {url}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    map_file = DATA / "drive_map.json"
    processed_count = 0
    saved_bytes_total = 0

    try:
        # Step 1: Scan manifest using gdown --folder --json
        cmd = [sys.executable, "-m", "gdown", "--folder", "--json", url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        active_process = proc
        stdout, stderr = proc.communicate()
        active_process = None

        if task_cancel_event.is_set():
            append_log("Google Drive scan cancelled by administrator.")
            with task_lock:
                current_task["status"] = "cancelled"
                current_task["message"] = "Task stopped by admin."
            update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.", log_line="Scan cancelled by administrator.")
            return

        items = []
        try:
            if stdout and stdout.strip().startswith("["):
                items = json.loads(stdout.strip())
        except Exception as e:
            append_log(f"Manifest parse warning: {e}")

        # Step 2: Apply format filter
        if items:
            valid_items = []
            skipped_count = 0
            for item in items:
                p = item.get("path", "")
                ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
                if ext in allowed_set:
                    valid_items.append(item)
                else:
                    skipped_count += 1
            
            total_photos = len(valid_items)
            append_log(f"Manifest scan complete: found {len(items)} files. Filter kept {total_photos} photos ({', '.join(sorted(allowed_set))}), skipped {skipped_count} unsupported/RAW files.")
            update_task_progress(task_id, progress_pct=10, total_files=total_photos,
                                 current_step=f"Found {total_photos} photos matching format filter (skipped {skipped_count} RAW/unsupported files)",
                                 log_line=f"Found {len(items)} total files. Kept {total_photos} photos, skipped {skipped_count} files.")

            # Step 3: Stream download each valid photo directly
            skipped_existing_count = 0
            last_log_time = time.time()
            last_db_sync_time = time.time()
            pending_db_sync = False

            for idx, item in enumerate(valid_items):
                if task_cancel_event.is_set():
                    append_log("Download cancelled by administrator.")
                    with task_lock:
                        current_task["status"] = "cancelled"
                        current_task["message"] = "Task stopped by admin."
                    update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.", log_line="Task cancelled by administrator.")
                    return

                item_raw_path = item.get("path", f"photo_{idx}.jpg")
                p = Path(item_raw_path)
                fname = p.name
                furl = item.get("url", "")
                
                # Extract file id
                fid_match = re.search(r'id=([a-zA-Z0-9_-]+)', furl)
                fid = fid_match.group(1) if fid_match else ""

                # Preserve folder hierarchy on disk for automatic album creation
                dest_file = dest_dir / item_raw_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                
                # Skip if already exists on disk and is valid (UPSERT & SKIP EXISTING)
                if dest_file.exists() and dest_file.stat().st_size > 1000:
                    rel = str(dest_file.relative_to(PHOTOS))
                    if fid:
                        drive_map[rel] = fid
                        drive_map[fname] = fid
                    processed_count += 1
                    skipped_existing_count += 1
                    pending_db_sync = True
                else:
                    # Download with retry
                    success = False
                    err_msg = ""
                    for attempt in range(3):
                        if task_cancel_event.is_set():
                            break
                        success, err_msg = download_gdrive_file(furl, dest_file)
                        if success:
                            break
                        time.sleep(1.5 * (attempt + 1))

                    if task_cancel_event.is_set():
                        if dest_file.exists():
                            try: dest_file.unlink()
                            except Exception: pass
                        break

                    if success and dest_file.exists():
                        orig_size = dest_file.stat().st_size
                        optimize_photo_inplace(dest_file)
                        new_size = dest_file.stat().st_size
                        saved_bytes_total += max(0, orig_size - new_size)

                        # Pre-generate web thumbnail immediately to avoid browser stampede
                        try:
                            rel = str(dest_file.relative_to(PHOTOS))
                            t_path = DATA / "thumbs" / (rel.replace("/", "__") + ".jpg")
                            t_path.parent.mkdir(parents=True, exist_ok=True)
                            with Image.open(dest_file) as timg:
                                try: timg.draft("RGB", (480, 480))
                                except Exception: pass
                                timg = ImageOps.exif_transpose(timg).convert("RGB")
                                timg.thumbnail((480, 480))
                                timg.save(t_path, "JPEG", quality=80, optimize=True)
                        except Exception:
                            pass
                        
                        rel = str(dest_file.relative_to(PHOTOS))
                        if fid:
                            drive_map[rel] = fid
                            drive_map[fname] = fid
                            
                        processed_count += 1
                        pending_db_sync = True
                        time.sleep(0.04) # Keep CPU cool
                    else:
                        append_log(f"Skipping {fname}: could not download ({err_msg})")

                # Progress calculations
                pct = min(75, 10 + int((processed_count / max(1, total_photos)) * 65))
                saved_mb = round(saved_bytes_total / (1024 * 1024), 1)

                with task_lock:
                    current_task["progress_pct"] = pct
                    current_task["processed_files"] = processed_count
                    current_task["total_files"] = total_photos
                    current_task["storage_saved_mb"] = saved_mb
                    current_task["message"] = f"Processed {fname} ({processed_count}/{total_photos})"

                now = time.time()
                time_since_log = now - last_log_time
                time_since_sync = now - last_db_sync_time

                # 1. Update task log every 25 seconds OR every 25 files OR on final file
                should_log = (time_since_log >= 25.0 and processed_count > 0) or (processed_count % 25 == 0) or (processed_count == total_photos)
                if should_log:
                    last_log_time = now
                    now_str = datetime.now().strftime("%H:%M:%S")
                    log_line = f"[{now_str}] Progress: {processed_count}/{total_photos} photos ({pct}%) | Saved: {saved_mb} MB | Current: {fname} (Skipped {skipped_existing_count} existing)"
                    append_log(log_line)
                    update_task_progress(task_id, progress_pct=pct,
                                         current_step=f"Processed {fname} ({processed_count}/{total_photos}, skipped {skipped_existing_count} existing)",
                                         processed_files=processed_count, total_files=total_photos,
                                         storage_saved_mb=saved_mb, log_line=log_line)
                elif processed_count % 5 == 0 or processed_count == total_photos:
                    update_task_progress(task_id, progress_pct=pct,
                                         current_step=f"Processed {fname} ({processed_count}/{total_photos}, skipped {skipped_existing_count} existing)",
                                         processed_files=processed_count, total_files=total_photos,
                                         storage_saved_mb=saved_mb)

                # 2. Live Progressive Sync newly downloaded photos into albums & database every 35-45 seconds or every 50 photos
                should_sync_db = (time_since_sync >= 35.0 and pending_db_sync) or (processed_count % 50 == 0 and pending_db_sync) or (processed_count == total_photos and pending_db_sync)
                if should_sync_db:
                    last_db_sync_time = now
                    pending_db_sync = False
                    try:
                        reg_count = sync_photos_to_database(dest_dir, event_slug, drive_map, allowed_set)
                        try:
                            map_file.write_text(json.dumps(drive_map, indent=2))
                        except Exception:
                            pass
                        now_str = datetime.now().strftime("%H:%M:%S")
                        sync_log = f"[{now_str}] Live Albums Sync: {reg_count} photos registered across albums in database"
                        append_log(sync_log)
                        update_task_progress(task_id, current_step=f"Live Albums Sync: {reg_count} photos ready", log_line=sync_log)
                    except Exception as e:
                        append_log(f"Warning during intermediate DB sync: {e}")

            if skipped_existing_count > 0:
                append_log(f"Skipped {skipped_existing_count} already downloaded photos.")

        else:
            # Fallback if --json manifest could not be retrieved
            append_log(f"Falling back to standard folder download for {url}...")
            proc = subprocess.Popen(
                [sys.executable, "-m", "gdown", "--folder", url, "-O", str(dest_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            active_process = proc
            for line in proc.stdout:
                if task_cancel_event.is_set():
                    proc.kill()
                    break
                txt = line.rstrip()
                append_log(txt)
            proc.wait()
            active_process = None
            
            # Post-download format filter: remove any unaccepted files (e.g. .arw)
            for f in dest_dir.rglob("*"):
                if f.is_file():
                    ext = f.suffix.lower().strip(".")
                    if ext not in allowed_set:
                        try:
                            f.unlink()
                            append_log(f"Removed non-accepted file format: {f.name}")
                        except Exception:
                            pass

        if task_cancel_event.is_set():
            append_log("Task stopped by administrator.")
            with task_lock:
                current_task["status"] = "cancelled"
                current_task["message"] = "Task stopped by admin."
            update_task_progress(task_id, status="cancelled", current_step="Task stopped by admin.")
            return

        try:
            map_file.write_text(json.dumps(drive_map, indent=2))
        except Exception:
            pass

        # Final pass: Ensure 100% of downloaded photos and albums are synchronized in SQLite
        update_task_progress(task_id, progress_pct=78, current_step="Finalizing albums and photo database...",
                             log_line="Finalizing albums and photo database...")
        final_count = sync_photos_to_database(dest_dir, event_slug, drive_map, allowed_set)
        append_log(f"Database sync complete: {final_count} photos active in albums.")

        if task_cancel_event.is_set():
            return

        # Trigger indexing
        update_task_progress(task_id, progress_pct=82, current_step="Starting AI facial recognition indexing...")
        run_indexing_task(event_slug, task_id)

        # Final completion
        if not task_cancel_event.is_set():
            saved_mb = round(saved_bytes_total / (1024 * 1024), 1)
            with task_lock:
                current_task["status"] = "completed"
                current_task["progress_pct"] = 100
                current_task["message"] = f"Finished! {processed_count} photos imported and indexed into '{dest_dir.name}'."
            update_task_progress(task_id, progress_pct=100, status="completed",
                                 current_step=f"Finished! {processed_count} photos imported and indexed into '{dest_dir.name}'.",
                                 processed_files=processed_count, total_files=max(processed_count, 1),
                                 storage_saved_mb=saved_mb, log_line=f"SUCCESS: {processed_count} photos processed.")

    except Exception as e:
        active_process = None
        append_log(f"Google Drive ingestion error: {e}")
        with task_lock:
            current_task["status"] = "error"
            current_task["message"] = f"Error: {e}"
        update_task_progress(task_id, status="failed", current_step=f"Error: {e}", log_line=f"ERROR: {e}")
    finally:
        active_process = None

# --- Background Upload Task Queue Worker (FIFO Sequential Processing) ---

queue_worker_running = False
queue_worker_lock = threading.Lock()

def trigger_queue_worker():
    global queue_worker_running
    with queue_worker_lock:
        if not queue_worker_running:
            queue_worker_running = True
            threading.Thread(target=_queue_worker_loop, daemon=True).start()

def _queue_worker_loop():
    global queue_worker_running
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Upload task queue worker loop started.")
    try:
        while True:
            # Check if any manual/active task is currently running in current_task
            with task_lock:
                busy = current_task.get("status") == "running"
            if busy:
                time.sleep(1.5)
                continue

            # Atomically claim the next queued task in FIFO order
            job = claim_next_queued_task()
            if not job:
                # No pending queued jobs
                break

            try:
                execute_queued_job(job)
            except Exception as ex:
                append_log(f"[{now_ist().strftime('%H:%M:%S')}] Queue worker uncaught exception on task #{job.get('task_id')}: {ex}")
            time.sleep(1)
    finally:
        with queue_worker_lock:
            queue_worker_running = False
        append_log(f"[{now_ist().strftime('%H:%M:%S')}] Upload task queue worker idle (all tasks processed).")

def execute_queued_job(job: dict):
    global current_task, active_process
    task_id = job["task_id"]
    task_name = job["task_name"]
    task_type = job["task_type"]
    event_slug = job["event_slug"]
    target_album_slug = job["target_album_slug"]
    target_album_name = job["target_album_name"] or target_album_slug
    staged_dir_str = job.get("staged_dir")
    staged_dir = Path(staged_dir_str) if staged_dir_str else None
    display_title = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug

    task_cancel_event.clear()
    now_str = now_ist().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Starting queued task #{task_id}: {task_name}")

    create_task_record(task_id, "upload_queue", display_title, target_album_name, f"Queue: {task_name}")

    with task_lock:
        current_task.update({
            "task_id": task_id,
            "status": "running",
            "type": "upload",
            "event": display_title,
            "progress_pct": 5,
            "message": f"Processing queue task '{task_name}'...",
            "started_at": now_ist().strftime("%Y-%m-%d %H:%M:%S")
        })

    update_queue_task(task_id, status="running", progress_pct=5, current_step="Staging and preparing files...",
                      log_line=f"Processing started for {task_name}")

    try:
        if task_type == "gdrive_sync":
            source_url = job.get("source_info", "")
            allowed_exts = ["jpg", "jpeg", "png", "webp"]
            safe_album = "".join(c for c in target_album_slug.strip() if c.isalnum() or c in "-_") or "highlights"
            if safe_album == "auto":
                if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
                    dest_dir = PHOTOS
                    rel_prefix = ""
                else:
                    dest_dir = PHOTOS / "events" / event_slug
                    rel_prefix = f"events/{event_slug}"
            elif event_slug in {"default", "wedding", "brk-sir", "brksir"}:
                dest_dir = PHOTOS / safe_album
                rel_prefix = safe_album
            else:
                dest_dir = PHOTOS / "events" / event_slug / safe_album
                rel_prefix = f"events/{event_slug}/{safe_album}"

            run_gdrive_download(source_url, dest_dir, rel_prefix, event_slug, allowed_exts, task_id=task_id)
            update_queue_task(task_id, status="completed", progress_pct=100, current_step="Completed successfully",
                              log_line=f"Google Drive sync completed for task #{task_id}")
        else:
            # direct_upload / zip_upload
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM events WHERE slug = ?", (event_slug,))
            event = cursor.fetchone()
            if not event:
                cursor.execute("SELECT id FROM events WHERE slug = 'brk-sir' OR slug = 'wedding' LIMIT 1")
                event = cursor.fetchone()
            eid = event["id"] if event else 1

            cursor.execute("SELECT id FROM albums WHERE event_id = ? AND slug = ?", (eid, target_album_slug))
            arow = cursor.fetchone()
            album_id = arow["id"] if arow else None

            safe_album = "".join(c for c in target_album_slug.strip() if c.isalnum() or c in "-_") or "highlights"
            if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
                dest_dir = PHOTOS / safe_album
                rel_prefix = safe_album
            else:
                dest_dir = PHOTOS / "events" / event_slug / safe_album
                rel_prefix = f"events/{event_slug}/{safe_album}"
            dest_dir.mkdir(parents=True, exist_ok=True)

            valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
            saved_count = 0

            staged_files = []
            if staged_dir and staged_dir.exists():
                for p in staged_dir.iterdir():
                    if p.is_file():
                        staged_files.append(p)

            total_staged = len(staged_files)
            update_queue_task(task_id, file_count=total_staged, progress_pct=15,
                              current_step=f"Processing {total_staged} staged files...",
                              log_line=f"Found {total_staged} files in staging area.")
            append_log(f"[{now_ist().strftime('%H:%M:%S')}] Task #{task_id}: Processing {total_staged} staged files into '{safe_album}'")

            for idx, fpath in enumerate(staged_files, 1):
                if task_cancel_event.is_set():
                    update_queue_task(task_id, status="cancelled", current_step="Cancelled by administrator",
                                      log_line="Upload task cancelled during file ingestion.")
                    return

                ext = fpath.suffix.lower()
                if ext == ".zip":
                    try:
                        with zipfile.ZipFile(fpath) as z:
                            infolist = [info for info in z.infolist() if not info.is_dir() and Path(info.filename).suffix.lower() in valid_exts]
                            for z_idx, info in enumerate(infolist, 1):
                                if task_cancel_event.is_set():
                                    break
                                sub_name = Path(info.filename).name
                                dest_path = dest_dir / sub_name
                                with z.open(info) as src, open(dest_path, "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                                optimize_photo_inplace(dest_path)
                                rel_p = f"{rel_prefix}/{sub_name}"
                                cursor.execute("""
                                    INSERT INTO photos (event_id, album_id, filename, rel_path, file_size_bytes, is_deleted)
                                    VALUES (?, ?, ?, ?, ?, 0)
                                    ON CONFLICT(rel_path) DO UPDATE SET
                                        event_id = excluded.event_id,
                                        album_id = excluded.album_id,
                                        file_size_bytes = excluded.file_size_bytes,
                                        is_deleted = 0
                                """, (eid, album_id, sub_name, rel_p, dest_path.stat().st_size))
                                saved_count += 1
                                if z_idx % 10 == 0 or z_idx == len(infolist):
                                    pct = int(15 + (z_idx / max(len(infolist), 1)) * 40)
                                    update_queue_task(task_id, processed_count=saved_count, progress_pct=pct,
                                                      current_step=f"Extracted {saved_count} photos from ZIP...")
                    except Exception as ze:
                        append_log(f"[{now_ist().strftime('%H:%M:%S')}] Task #{task_id} ZIP error: {ze}")
                elif ext in valid_exts:
                    dest_path = dest_dir / fpath.name
                    shutil.copy2(fpath, dest_path)
                    optimize_photo_inplace(dest_path)
                    rel_p = f"{rel_prefix}/{fpath.name}"
                    cursor.execute("""
                        INSERT INTO photos (event_id, album_id, filename, rel_path, file_size_bytes, is_deleted)
                        VALUES (?, ?, ?, ?, ?, 0)
                        ON CONFLICT(rel_path) DO UPDATE SET
                            event_id = excluded.event_id,
                            album_id = excluded.album_id,
                            file_size_bytes = excluded.file_size_bytes,
                            is_deleted = 0
                    """, (eid, album_id, fpath.name, rel_p, dest_path.stat().st_size))
                    saved_count += 1
                    if idx % 10 == 0 or idx == total_staged:
                        pct = int(15 + (idx / max(total_staged, 1)) * 45)
                        update_queue_task(task_id, processed_count=saved_count, progress_pct=pct,
                                          current_step=f"Optimized {saved_count}/{total_staged} photos...")

            conn.commit()
            conn.close()

            update_queue_task(task_id, processed_count=saved_count, file_count=saved_count, progress_pct=60,
                              current_step="Starting AI face indexing...",
                              log_line=f"Saved {saved_count} photos. Proceeding to face recognition indexing.")
            append_log(f"[{now_ist().strftime('%H:%M:%S')}] Task #{task_id}: Saved {saved_count} photos. Starting face indexing.")

            # Step 2: Face indexing
            if saved_count > 0:
                run_indexing_task(event_slug, task_id=task_id)

            update_queue_task(task_id, status="completed", progress_pct=100, current_step="Completed successfully",
                              log_line=f"Task #{task_id} completed successfully ({saved_count} photos indexed).")
            append_log(f"[{now_ist().strftime('%H:%M:%S')}] Task #{task_id} completed successfully.")

            # Clean up staged files on success
            if staged_dir and staged_dir.exists():
                shutil.rmtree(staged_dir, ignore_errors=True)

    except Exception as e:
        now_err = now_ist().strftime('%H:%M:%S')
        append_log(f"[{now_err}] Task #{task_id} error: {e}")
        update_queue_task(task_id, status="failed", error_message=str(e),
                          current_step="Task failed", log_line=f"Error: {e}")
        update_task_progress(task_id, status="failed", current_step=f"Error: {e}")
    finally:
        with task_lock:
            current_task["status"] = "idle"
            current_task["message"] = "Ready"
        append_log(f"[{now_ist().strftime('%H:%M:%S')}] Finished task #{task_id}. Checking next queued task...")

app = FastAPI()

@app.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/images/") or path.endswith((".webp", ".png", ".jpg", ".jpeg", ".ico", ".svg", ".woff2")):
        # Cache static brand assets and background images for 30 days
        response.headers["Cache-Control"] = "public, max-age=2592000, stale-while-revalidate=86400"
    elif path.startswith(("/thumb/", "/preview/")):
        response.headers["Cache-Control"] = "public, max-age=604800, stale-while-revalidate=86400"
    return response

# --- Search & Public Delivery APIs ---

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

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def parse_photo_date_info(item: dict) -> dict:
    """Parses date or album name from a photo record matching frontend logic."""
    album = (item.get("album_name") or "").strip()
    cap_dt = (item.get("captured_at") or item.get("created_at") or "").strip()

    # 1. Match DD-MM-YY or DD-MM-YYYY in album name (e.g. "01-11-26", "21-10-2026")
    m_dmy = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", album)
    if m_dmy:
        d = int(m_dmy.group(1))
        m = int(m_dmy.group(2))
        y = int(m_dmy.group(3))
        if y < 100:
            y += 2000
        try:
            datetime(y, m, d)
            return {"is_date": True, "date_key": f"{y:04d}-{m:02d}-{d:02d}", "raw_album": album}
        except ValueError:
            pass

    # Match YYYY-MM-DD in album name
    m_ymd_alb = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", album)
    if m_ymd_alb:
        y = int(m_ymd_alb.group(1))
        m = int(m_ymd_alb.group(2))
        d = int(m_ymd_alb.group(3))
        try:
            datetime(y, m, d)
            return {"is_date": True, "date_key": f"{y:04d}-{m:02d}-{d:02d}", "raw_album": album}
        except ValueError:
            pass

    # 2. Match YYYY-MM-DD in captured_at / created_at
    if cap_dt:
        m_ymd = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", cap_dt)
        if m_ymd:
            y = int(m_ymd.group(1))
            m = int(m_ymd.group(2))
            d = int(m_ymd.group(3))
            try:
                datetime(y, m, d)
                return {"is_date": True, "date_key": f"{y:04d}-{m:02d}-{d:02d}", "raw_album": album or f"{y:04d}-{m:02d}-{d:02d}"}
            except ValueError:
                pass

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

    best_by_filename = {}
    matched_faces_map = {}

    # Support both single face embedding (512D) and multi-face list for group photo discovery
    if isinstance(query_emb, (list, tuple)) and len(query_emb) > 0 and (isinstance(query_emb[0], (list, tuple, np.ndarray))):
        for f_idx, q_single in enumerate(query_emb):
            q_vec = np.array(q_single, dtype=np.float32)
            sims = embs @ q_vec
            for idx in np.where(sims >= min_thresh)[0]:
                n = names[idx]
                fname = Path(n).name
                score = float(sims[idx])
                if fname not in best_by_filename or score > best_by_filename[fname]["score"]:
                    best_by_filename[fname] = {"rel_path": n, "score": score}
                matched_faces_map.setdefault(fname, set()).add(f_idx)
    else:
        q_vec = np.array(query_emb, dtype=np.float32)
        sims = embs @ q_vec
        for idx in np.where(sims >= min_thresh)[0]:
            n = names[idx]
            fname = Path(n).name
            score = float(sims[idx])
            if fname not in best_by_filename or score > best_by_filename[fname]["score"]:
                best_by_filename[fname] = {"rel_path": n, "score": score}
            matched_faces_map.setdefault(fname, set()).add(0)

    ranked = sorted(best_by_filename.items(), key=lambda kv: -kv[1]["score"])
    reload_drive_map()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.filename, p.rel_path, p.is_featured, p.drive_file_id, p.captured_at, p.created_at,
               a.name as album_name, a.slug as album_slug, 
               e.name as event_name, e.slug as event_slug
        FROM photos p
        JOIN events e ON p.event_id = e.id
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE (e.slug = ? OR ? IN ('brk-sir', 'brksir', 'wedding', 'default', '')) AND p.is_deleted = 0
    """, (event_slug, event_slug))
    rows = cursor.fetchall()
    conn.close()

    photo_info = {}
    for r in rows:
        d = dict(r)
        if d.get("filename"):
            photo_info[d["filename"]] = d
        if d.get("rel_path"):
            photo_info[d["rel_path"]] = d

    photos_data = []
    for fname, item in ranked:
        n = item["rel_path"]
        score = item["score"]
        score_val = round(float(score), 3)
        conf_info = classify_confidence(score_val, high_thresh, med_thresh, min_thresh)
        info = photo_info.get(fname) or photo_info.get(n) or {}
        cap_dt = info.get("captured_at") or info.get("created_at") or ""
        photos_data.append({
            "rel_path": n,
            "filename": fname,
            "drive_file_id": info.get("drive_file_id") or drive_map.get(n) or drive_map.get(fname),
            "similarity": score_val,
            "confidence": score_val,
            "confidence_tier": conf_info["tier"],
            "confidence_label": conf_info["label"],
            "matched_faces": sorted(list(matched_faces_map.get(fname, [0]))),
            "is_featured": bool(info.get("is_featured", 0)),
            "captured_at": cap_dt,
            "created_at": info.get("created_at", ""),
            "album_name": info.get("album_name", ""),
            "event_name": info.get("event_name", ""),
            "event_slug": info.get("event_slug", event_slug)
        })

    date_summary = aggregate_photo_dates(photos_data)
    unfiltered_total_count = len(photos_data)

    if filter_date:
        photos_data = [p for p in photos_data if parse_photo_date_info(p).get("date_key") == filter_date]
    if filter_album:
        photos_data = [p for p in photos_data if parse_photo_date_info(p).get("raw_album") == filter_album]

    total_count = len(photos_data)

    photos_data.sort(key=lambda p: p.get("captured_at") or p.get("created_at") or "", reverse=True)
    photos_data.sort(key=lambda p: 0 if p.get("is_featured") else 1)

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
    with lock:
        faces = face_app.get(img)
    if not faces:
        duration_ms = round((time.time() - search_start) * 1000, 1)
        analytics.record_search_failed(search_id=search_id, session_id=session_id or "anon", error_category="no_face_detected", duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)
        raise HTTPException(422, "No face found. Try a clear, front-facing photo in good light.")

    # Sort faces left-to-right based on bounding box
    faces = sorted(faces, key=lambda f: f.bbox[0])

    # Check for group search or individual selection
    is_search_all_faces = (str(face_index).strip().lower() == "all") if face_index is not None else False
    numeric_face_index = None
    if face_index is not None and not is_search_all_faces:
        try:
            numeric_face_index = int(face_index)
        except ValueError:
            numeric_face_index = None

    # If multiple people are detected in the uploaded photo and no choice was made yet:
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

    # Execute search based on selection
    if is_search_all_faces:
        query_embs = [f.normed_embedding for f in faces]
        selected_face_thumb = crop_face_thumb(img, faces[0].bbox)
        selected_idx = "all"
        res = query_matched_photos(embs, names, query_embs, event_slug, filter_date, filter_album, page, page_size)
    else:
        if numeric_face_index is not None and 0 <= numeric_face_index < len(faces):
            face = faces[numeric_face_index]
            selected_idx = numeric_face_index
        else:
            face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            selected_idx = faces.index(face)
        selected_face_thumb = crop_face_thumb(img, face.bbox)
        res = query_matched_photos(embs, names, face.normed_embedding, event_slug, filter_date, filter_album, page, page_size)

    duration_ms = round((time.time() - search_start) * 1000, 1)
    if res["total_count"] > 0:
        analytics.record_search_completed(search_id=search_id, session_id=session_id or "anon", matches_count=res["total_count"], duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)
    else:
        analytics.record_search_no_results(search_id=search_id, session_id=session_id or "anon", duration_ms=duration_ms, visitor_id=visitor_id, event_slug=event_slug)

    # Human-in-the-loop review recording for confidence diagnostics
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

    return {
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
        "is_group_search": is_search_all_faces
    }

@app.post("/api/search")
def default_search(selfie: UploadFile = File(...), face_index: Optional[str] = Form(None), page: int = Form(1), page_size: int = Form(50), filter_date: Optional[str] = Form(None), filter_album: Optional[str] = Form(None), session_id: Optional[str] = Form(None), visitor_id: Optional[str] = Form(None), device_type: Optional[str] = Form(None)):
    """Search for matching photos using face recognition with server-side pagination and date summary."""
    return perform_face_search(selfie, "brk-sir", face_index=face_index, page=page, page_size=page_size, filter_date=filter_date, filter_album=filter_album, session_id=session_id, visitor_id=visitor_id, device_type=device_type)

@app.post("/api/{slug}/search")
def event_search(slug: str, selfie: UploadFile = File(...), face_index: Optional[str] = Form(None), page: int = Form(1), page_size: int = Form(50), filter_date: Optional[str] = Form(None), filter_album: Optional[str] = Form(None), session_id: Optional[str] = Form(None), visitor_id: Optional[str] = Form(None), device_type: Optional[str] = Form(None)):
    """Search for matching photos using face recognition with server-side pagination and date summary."""
    return perform_face_search(selfie, slug, face_index=face_index, page=page, page_size=page_size, filter_date=filter_date, filter_album=filter_album, session_id=session_id, visitor_id=visitor_id, device_type=device_type)

@app.post("/api/events/{slug}/check-profile-matches")
@app.post("/api/check-profile-matches")
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

    query_emb = None
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
            with lock:
                faces = face_app.get(img)
            if faces:
                target_f = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                query_emb = target_f.normed_embedding
            elif img is not None and img.size > 0:
                h_c, w_c = img.shape[:2]
                scale = max(640.0 / max(h_c, w_c), 2.5)
                upscaled = cv2.resize(img, (int(w_c * scale), int(h_c * scale)), interpolation=cv2.INTER_CUBIC)
                with lock:
                    up_faces = face_app.get(upscaled)
                if up_faces:
                    target_f = max(up_faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                    query_emb = target_f.normed_embedding
        except Exception as e:
            print("Error decoding face_thumb in match check:", e)

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
        search_id = str(uuid.uuid4())[:12]
        sess_id = body.get("session_id") or "anon"
        vis_id = body.get("visitor_id")
        duration_ms = round((time.time() - search_start) * 1000, 1)
        if total_count > 0:
            analytics.record_search_completed(search_id=search_id, session_id=sess_id, matches_count=total_count, duration_ms=duration_ms, visitor_id=vis_id, event_slug=slug)
        else:
            analytics.record_search_no_results(search_id=search_id, session_id=sess_id, duration_ms=duration_ms, visitor_id=vis_id, event_slug=slug)

    return {
        "status": "ok",
        "count": total_count,
        "total_count": total_count,
        "unfiltered_total_count": res["unfiltered_total_count"],
        "page": page,
        "page_size": page_size if page_size > 0 else total_count,
        "has_more": res["has_more"],
        "next_page": res["next_page"],
        "photos": res["photos"],
        "date_summary": res["date_summary"],
        "embedding": query_emb.tolist() if (query_emb is not None and hasattr(query_emb, "tolist")) else None,
        "filter_date": filter_date,
        "filter_album": filter_album,
        "has_new": has_new,
        "new_count": new_count
    }

@app.get("/thumb/{name:path}")
def thumb(name: str):
    """Delivers 480px web thumbnail or cropped face avatar safely."""
    # 0. Query review crops
    if name.startswith("review_crops/"):
        crop_name = Path(name).name
        target = DATA / "review_crops" / crop_name
        if target.exists():
            return FileResponse(target)

    # 1. Face avatars
    if name.startswith("faces/"):
        face_name = Path(name).name
        target = DATA / "faces" / face_name
        if target.exists():
            return FileResponse(target)

    # 2. Direct match in root thumbs directory
    direct_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if direct_thumb.exists():
        return FileResponse(direct_thumb)

    # 3. Match in event-scoped thumbs: events/<slug>/<subpath> -> DATA/events/<slug>/thumbs/<subpath.replace('/', '__')>.jpg
    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            slug = parts[1]
            sub = parts[2]
            ev_thumb = DATA / "events" / slug / "thumbs" / (sub.replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb)

    # 4. Search in any events/*/thumbs/ directory
    fname = Path(name).name
    candidates = list(DATA.glob(f"events/*/thumbs/*{fname}*.jpg"))
    if candidates and candidates[0].exists():
        return FileResponse(candidates[0])

    candidates = list(DATA.glob(f"thumbs/*{fname}*.jpg"))
    if candidates and candidates[0].exists():
        return FileResponse(candidates[0])

    # 5. On-the-fly thumbnail generation from original photo on disk with concurrency limit
    photo_file = PHOTOS / name
    if not photo_file.exists():
        matches = list(PHOTOS.glob(f"**/{fname}"))
        if matches:
            photo_file = matches[0]

    if photo_file.exists():
        with thumb_semaphore:
            cache_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
            if cache_thumb.exists():
                return FileResponse(cache_thumb)
            try:
                cache_thumb.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(photo_file) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((1080, 1080), Image.Resampling.LANCZOS)
                    img.save(cache_thumb, "JPEG", quality=84, optimize=True)
                return FileResponse(cache_thumb)
            except Exception:
                return FileResponse(photo_file)

    raise HTTPException(404, "Thumbnail not found")

@app.get("/photo/{name:path}")
def photo(name: str):
    """Delivers photo directly from VPS without redirecting to Google Drive."""
    fname = Path(name).name
    target = PHOTOS / name
    if not target.exists():
        matches = list(PHOTOS.glob(f"**/{fname}"))
        if matches:
            target = matches[0]

    if target.exists():
        return FileResponse(target, filename=fname)

    # Fallback to high-res thumbnail on VPS if original was pruned
    direct_thumb = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if direct_thumb.exists():
        return FileResponse(direct_thumb, filename=fname)

    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            ev_thumb = DATA / "events" / parts[1] / "thumbs" / (parts[2].replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb, filename=fname)

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

@app.get("/preview/{name:path}")
def preview(name: str):
    """Serves studio-quality 1600px HD preview for lightbox viewer, cached on VPS for instant 1ms response."""
    cache_preview = DATA / "previews" / (name.replace("/", "__") + ".jpg")
    if cache_preview.exists():
        return FileResponse(cache_preview, media_type="image/jpeg")

    fname = Path(name).name
    target = PHOTOS / name
    if not target.exists():
        matches = list(PHOTOS.glob(f"**/{fname}"))
        if matches:
            target = matches[0]

    if target.exists():
        try:
            cache_preview.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(target) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                img.save(cache_preview, "JPEG", quality=86, optimize=True)
            return FileResponse(cache_preview, media_type="image/jpeg")
        except Exception:
            return FileResponse(target, media_type="image/jpeg")

    # Fallback to high-res thumbnail if original not found
    thumb_path = DATA / "thumbs" / (name.replace("/", "__") + ".jpg")
    if thumb_path.exists():
        return FileResponse(thumb_path, media_type="image/jpeg")

    if name.startswith("events/"):
        parts = name.split("/", 2)
        if len(parts) >= 3:
            ev_thumb = DATA / "events" / parts[1] / "thumbs" / (parts[2].replace("/", "__") + ".jpg")
            if ev_thumb.exists():
                return FileResponse(ev_thumb, media_type="image/jpeg")

    raise HTTPException(404, "Preview photo not found")

@app.get("/download/hd/{name:path}")
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
    target = PHOTOS / name
    if not target.exists():
        matches = list(PHOTOS.glob(f"**/{fname}"))
        if matches:
            target = matches[0]

    if target.exists():
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

@app.get("/download/4k/{name:path}")
def download_4k(name: str, req: Request):
    """Downloads original full-resolution 4K file directly from server without requiring Google login."""
    vis_id = req.headers.get("X-Visitor-Id") or req.cookies.get("brk_visitor_id")
    sess_id = req.headers.get("X-Session-Id") or req.cookies.get("brk_session_id") or "anon"
    analytics.record_photo_download(name, quality="4k", session_id=sess_id, visitor_id=vis_id)
    fname = Path(name).name
    # 1. Check if original photo exists on VPS storage
    target = PHOTOS / name
    if not target.exists():
        matches = list(PHOTOS.glob(f"**/{fname}"))
        if matches:
            target = matches[0]

    if target.exists():
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

@app.post("/api/download/bulk-zip")
@app.post("/api/{slug}/download/bulk-zip")
async def download_bulk_zip(request: Request, background_tasks: BackgroundTasks, slug: str = "brk-sir"):
    """
    Creates and streams a downloadable ZIP file containing the selected original full-resolution photos.
    Uses ZIP_STORED (0% CPU recompression overhead, fast disk stream) and prevents directory traversal.
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
    photos_root = PHOTOS.resolve()

    # Use ZIP_STORED to eliminate heavy CPU re-compression overhead on JPEG photos
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

            # Prevent directory traversal attacks
            candidate = None
            try:
                target = (PHOTOS / p_path).resolve()
                if str(target).startswith(str(photos_root)) and target.is_file():
                    candidate = target
            except Exception:
                candidate = None

            # Fallback search within PHOTOS by filename
            if candidate is None:
                matches = list(PHOTOS.glob(f"**/{fname}"))
                for m in matches:
                    try:
                        resolved_m = m.resolve()
                        if str(resolved_m).startswith(str(photos_root)) and resolved_m.is_file():
                            candidate = resolved_m
                            break
                    except Exception:
                        pass

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

# --- Public Gallery Metadata APIs ---

@app.get("/api/public/gallery")
def public_gallery():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_event_details(slug)

@app.get("/api/public/albums/{album_slug}")
def public_album_direct(album_slug: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_album_photos(slug, album_slug)

@app.get("/api/public/events/{slug}")
def public_event_details(slug: str):
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
        
    cursor.execute("""
        SELECT a.*, COUNT(p.id) as photo_count 
        FROM albums a
        LEFT JOIN photos p ON a.id = p.album_id AND p.is_deleted = 0 AND p.is_hidden = 0
        WHERE a.event_id = ? AND a.is_published = 1 AND a.is_deleted = 0
        GROUP BY a.id
        ORDER BY a.sort_order ASC, a.name ASC
    """, (event["id"],))
    albums = [dict(row) for row in cursor.fetchall()]
    
    # Auto-populate album cover image if not explicitly set
    for a in albums:
        if not a["cover_image_path"]:
            cursor.execute("""
                SELECT rel_path FROM photos 
                WHERE album_id = ? AND is_deleted = 0 AND is_hidden = 0 
                ORDER BY is_featured DESC, id DESC LIMIT 1
            """, (a["id"],))
            p_row = cursor.fetchone()
            if p_row:
                a["cover_image_path"] = p_row["rel_path"]

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
    
    return {
        "event": event_dict,
        "albums": albums,
        "photo_count": photo_count
    }

@app.get("/api/public/events/{slug}/albums/{album_slug}")
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

# --- Admin APIs ---

@app.post("/api/admin/login")
async def admin_login(payload: dict):
    key = payload.get("key", "").strip()
    if key != ADMIN_KEY:
        raise HTTPException(401, "Incorrect admin key.")
    token = secrets.token_hex(24)
    active_tokens.add(token)
    response = JSONResponse({"status": "ok", "token": token})
    response.set_cookie("admin_token", token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response

@app.post("/api/admin/logout")
def admin_logout(token: str = Depends(get_auth_token)):
    active_tokens.discard(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("admin_token")
    return response

@app.get("/api/admin/overview")
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
        SELECT e.*, COUNT(p.id) as photo_count, COUNT(DISTINCT a.id) as album_count
        FROM events e
        LEFT JOIN photos p ON e.id = p.event_id AND p.is_deleted = 0
        LEFT JOIN albums a ON e.id = a.event_id
        WHERE e.status != 'archived'
        GROUP BY e.id
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

@app.post("/api/admin/events")
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

def sync_event_folders_and_photos(slug: str):
    """Auto-detect physical folders on disk, register missing albums, and link unassigned photos."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ?", (slug,))
    erow = cursor.fetchone()
    if not erow:
        conn.close()
        return
    eid = erow["id"]

    if slug in {"default", "wedding", "brk-sir", "brksir"}:
        event_dir = PHOTOS
    else:
        event_dir = PHOTOS / "events" / slug

    event_dir.mkdir(parents=True, exist_ok=True)
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}

    try:
        for d in sorted(event_dir.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")) or d.name == "events":
                continue
            folder_name = d.name
            album_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", folder_name.lower()).strip("-")
            if not album_slug:
                continue
            display_name = folder_name.replace("-", " ").replace("_", " ").title()

            cursor.execute("SELECT id, is_deleted FROM albums WHERE event_id = ? AND (slug = ? OR name = ?)", (eid, album_slug, display_name))
            arow = cursor.fetchone()
            if not arow:
                cursor.execute("""
                    INSERT INTO albums (event_id, slug, name, sort_order)
                    VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM albums WHERE event_id = ?))
                """, (eid, album_slug, display_name, eid))
                album_id = cursor.lastrowid
            else:
                if arow["is_deleted"]:
                    continue  # Trashed album: skip syncing its contents
                album_id = arow["id"]

            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                    rel = str(f.relative_to(PHOTOS))
                    fsize = f.stat().st_size
                    cap_date = extract_photo_capture_date(file_path=f, rel_path=rel, album_hint=folder_name)
                    cursor.execute("""
                        INSERT INTO photos (event_id, album_id, filename, rel_path, file_size_bytes, captured_at, is_deleted)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                        ON CONFLICT(rel_path) DO UPDATE SET
                            event_id = excluded.event_id,
                            album_id = excluded.album_id,
                            file_size_bytes = excluded.file_size_bytes,
                            captured_at = COALESCE(excluded.captured_at, photos.captured_at),
                            is_deleted = 0
                    """, (eid, album_id, f.name, rel, fsize, cap_date))

        # Direct files in event root
        for f in event_dir.iterdir():
            if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                rel = str(f.relative_to(PHOTOS))
                fsize = f.stat().st_size
                cap_date = extract_photo_capture_date(file_path=f, rel_path=rel, album_hint="")
                cursor.execute("""
                    INSERT OR IGNORE INTO photos (event_id, filename, rel_path, file_size_bytes, captured_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (eid, f.name, rel, fsize, cap_date))

        # Set cover image for any album that has photos but no cover
        cursor.execute("""
            SELECT a.id, a.cover_image_path,
                   (SELECT p.rel_path FROM photos p WHERE p.album_id = a.id AND p.is_deleted = 0 ORDER BY p.id ASC LIMIT 1) as first_photo
            FROM albums a WHERE a.event_id = ?
        """, (eid,))
        for a in cursor.fetchall():
            if not a["cover_image_path"] and a["first_photo"]:
                cursor.execute("UPDATE albums SET cover_image_path = ? WHERE id = ?", (a["first_photo"], a["id"]))

        conn.commit()
    except Exception as e:
        print(f"Error in sync_event_folders_and_photos({slug}): {e}")
    finally:
        conn.close()

@app.get("/api/admin/events/{slug}")
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

@app.get("/api/admin/albums")
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

@app.post("/api/admin/albums")
def admin_create_album_direct(payload: dict, token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return admin_create_album(slug, payload, token)

@app.post("/api/admin/events/{slug}/albums")
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

@app.put("/api/admin/events/{slug}")
@app.post("/api/admin/events/{slug}/update")
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

@app.delete("/api/admin/events/{slug}")
@app.post("/api/admin/events/{slug}/delete")
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

@app.put("/api/admin/events/{slug}/albums/{album_id}")
@app.post("/api/admin/events/{slug}/albums/{album_id}/update")
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

@app.delete("/api/admin/events/{slug}/albums/{album_id}")
@app.post("/api/admin/events/{slug}/albums/{album_id}/delete")
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

@app.get("/api/admin/events/{slug}/photos")
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

@app.post("/api/admin/events/{slug}/photos/bulk-move")
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

@app.post("/api/admin/events/{slug}/photos/bulk-action")
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

@app.post("/api/admin/events/{slug}/albums/{album_id}/set-cover")
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

@app.post("/api/admin/events/{slug}/photos/{photo_id}/toggle-feature")
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

@app.get("/api/admin/trash")
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

@app.post("/api/admin/trash/restore")
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

@app.post("/api/admin/trash/permanent-delete")
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

@app.post("/api/admin/events/{slug}/upload")
@app.post("/api/admin/events/{slug}/upload-queue")
async def admin_upload_to_event(
    slug: str,
    files: list[UploadFile] = File(...),
    album_slug: str = Form("highlights"),
    action: str = Form("now"),
    auto_index: bool = Form(True),
    token: str = Depends(get_auth_token)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    if not event:
        cursor.execute("SELECT id, name FROM events WHERE slug = 'brk-sir' OR slug = 'wedding' LIMIT 1")
        event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(404, "Event not found")
        
    eid = event["id"]
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND slug = ?", (eid, album_slug))
    arow = cursor.fetchone()
    album_name = arow["name"] if arow else album_slug
    conn.close()
    
    task_id = f"up_{secrets.token_hex(4)}"
    staged_dir = QUEUE_DIR / task_id
    staged_dir.mkdir(parents=True, exist_ok=True)
    
    file_count = 0
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".zip"}
    
    for f in files:
        fname = Path(f.filename or "upload").name
        ext = Path(fname).suffix.lower()
        if ext in valid_exts:
            content = await f.read()
            dest_file = staged_dir / fname
            with open(dest_file, "wb") as dst:
                dst.write(content)
            file_count += 1
            
    if file_count == 0:
        shutil.rmtree(staged_dir, ignore_errors=True)
        raise HTTPException(400, "No supported image or ZIP files provided.")
        
    task_name = f"Upload to {album_name} ({file_count} files)"
    enqueue_upload_task(
        task_id=task_id,
        task_name=task_name,
        task_type="direct_upload",
        event_slug=slug,
        target_album_slug=album_slug,
        target_album_name=album_name,
        source_info=f"{file_count} files",
        staged_dir=str(staged_dir),
        file_count=file_count
    )
    
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Upload task #{task_id} created with {file_count} files for '{album_name}'.")
    append_log(f"[{now_str}] Task #{task_id} added to queue.")
    
    with task_lock:
        is_running = current_task.get("status") == "running"
        
    trigger_queue_worker()
    
    if action == "now" and not is_running:
        msg = f"Upload #{task_id} started immediately."
        res_status = "started"
    else:
        msg = f"Task #{task_id} added to upload queue (FIFO order)."
        res_status = "queued"
        
    return {
        "status": res_status,
        "task_id": task_id,
        "file_count": file_count,
        "album": album_slug,
        "message": msg
    }

@app.post("/api/admin/events/{slug}/gdrive")
@app.post("/api/admin/events/{slug}/gdrive-queue")
def admin_event_gdrive(slug: str, payload: dict, token: str = Depends(get_auth_token)):
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(400, "Google Drive URL is required.")
        
    album_slug = payload.get("album_slug", "").strip() or "auto"
    allowed_exts = payload.get("allowed_extensions") or ["jpg", "jpeg", "png", "webp"]
    action = payload.get("action", "now")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM events WHERE slug = ?", (slug,))
    event = cursor.fetchone()
    eid = event["id"] if event else 1
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND slug = ?", (eid, album_slug))
    arow = cursor.fetchone()
    album_name = arow["name"] if arow else album_slug
    conn.close()
    
    task_id = f"gd_{secrets.token_hex(4)}"
    task_name = f"Google Drive Ingest ({album_name})"
    enqueue_upload_task(
        task_id=task_id,
        task_name=task_name,
        task_type="gdrive_sync",
        event_slug=slug,
        target_album_slug=album_slug,
        target_album_name=album_name,
        source_info=url,
        staged_dir="",
        file_count=0
    )
    
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Google Drive task #{task_id} created for '{album_name}'.")
    append_log(f"[{now_str}] Task #{task_id} added to queue.")
    
    with task_lock:
        is_running = current_task.get("status") == "running"
        
    trigger_queue_worker()
    
    if action == "now" and not is_running:
        msg = f"Google Drive ingest #{task_id} started immediately."
        res_status = "started"
    else:
        msg = f"Google Drive task #{task_id} added to upload queue."
        res_status = "queued"
        
    return {
        "status": res_status,
        "task_id": task_id,
        "album": album_slug,
        "message": msg
    }

@app.get("/api/admin/queue")
def admin_get_queue(limit: int = 30, token: str = Depends(get_auth_token)):
    tasks = get_queue_tasks(limit=limit)
    stats = get_queue_stats()
    with task_lock:
        mem_task = dict(current_task)
    return {
        "tasks": tasks,
        "stats": stats,
        "current_task": mem_task,
        "is_worker_running": queue_worker_running
    }

@app.get("/api/admin/queue/{task_id}")
def admin_get_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    task = get_queue_task_by_id(task_id)
    if not task:
        raise HTTPException(404, "Queue task not found")
    return {"task": task}

@app.get("/api/admin/queue/{task_id}/logs")
def admin_get_queue_task_logs(task_id: str, token: str = Depends(get_auth_token)):
    logs = get_queue_task_logs(task_id)
    if not logs:
        hist = get_task_by_id(task_id)
        if hist:
            return {"task_id": task_id, "task_name": hist.get("task_type", ""), "status": hist["status"], "current_step": hist["current_step"], "log_text": hist["log_text"]}
        raise HTTPException(404, "Task logs not found")
    return logs

@app.get("/api/admin/system/metrics")
def admin_system_metrics(token: str = Depends(get_auth_token)):
    import resource
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1, load5, load15 = (0.0, 0.0, 0.0)

    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_mb = round(usage.ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else usage.ru_maxrss / 1024, 1)

    db_size_mb = 0.0
    db_file = DATA / "magniwedding.db"
    if db_file.exists():
        db_size_mb = round(db_file.stat().st_size / (1024 * 1024), 2)

    stats = get_queue_stats()
    with task_lock:
        mem_task = dict(current_task)

    return {
        "status": "healthy",
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "memory_rss_mb": max_rss_mb,
        "db_size_mb": db_size_mb,
        "active_worker": queue_worker_running,
        "current_task_status": mem_task.get("status", "idle"),
        "queue_stats": stats,
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S")
    }

@app.post("/api/admin/queue/{task_id}/cancel")
def admin_cancel_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    global active_process
    with task_lock:
        if current_task.get("task_id") == task_id and current_task.get("status") == "running":
            task_cancel_event.set()
            if active_process and active_process.poll() is None:
                try:
                    active_process.terminate()
                    time.sleep(0.3)
                    if active_process.poll() is None:
                        active_process.kill()
                except Exception:
                    pass
            active_process = None
            current_task["status"] = "cancelled"
            current_task["message"] = "Task stopped by admin."
            
    ok = cancel_queue_task(task_id)
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Task #{task_id} cancelled by administrator.")
    trigger_queue_worker()
    return {"status": "ok", "task_id": task_id, "cancelled": ok}

@app.post("/api/admin/queue/{task_id}/retry")
def admin_retry_queue_task(task_id: str, token: str = Depends(get_auth_token)):
    ok = retry_queue_task(task_id)
    if not ok:
        raise HTTPException(400, "Task cannot be retried (must be failed or cancelled).")
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Task #{task_id} re-queued for retry by administrator.")
    trigger_queue_worker()
    return {"status": "ok", "task_id": task_id}

@app.post("/api/admin/queue/clear-completed")
def admin_clear_completed_queue(token: str = Depends(get_auth_token)):
    cleared = clear_completed_queue_tasks()
    now_str = datetime.now().strftime('%H:%M:%S')
    append_log(f"[{now_str}] Administrator cleared {cleared} completed/cancelled tasks from queue.")
    return {"status": "ok", "cleared_count": cleared}

# --- Scheduled Automation Worker & Endpoints ---

def _automation_scheduler_loop():
    """Background worker checking due automations every 30 seconds with idempotency protection."""
    while True:
        try:
            time.sleep(30)
            now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
            due_list = get_due_automations(now_str)
            for auto in due_list:
                auto_id = auto["id"]
                auto_name = auto["name"]
                event_slug = auto.get("event_slug", "brk-sir")
                gdrive_url = auto.get("gdrive_url", "")
                target_album = auto.get("target_album_slug", "auto")

                # Idempotency guard: prevent duplicate queuing if already running or queued
                if is_automation_task_pending(gdrive_url, auto_name):
                    continue

                task_id = f"auto_{secrets.token_hex(4)}"
                enqueue_upload_task(
                    task_id=task_id,
                    task_name=f"🤖 {auto_name}",
                    task_type="gdrive_sync",
                    event_slug=event_slug,
                    target_album_slug=target_album,
                    target_album_name="Auto Ingestion",
                    source_info=gdrive_url,
                    staged_dir="",
                    file_count=0
                )
                advance_automation_schedule(auto_id, task_id=task_id, status="queued")
                trigger_queue_worker()
                append_log(f"[{now_ist().strftime('%H:%M:%S')}] Automation '{auto_name}' triggered. Task #{task_id} added to upload queue.")
        except Exception:
            time.sleep(10)

threading.Thread(target=_automation_scheduler_loop, daemon=True, name="AutomationScheduler").start()

@app.get("/api/admin/automations")
def admin_get_automations(event_slug: str = "brk-sir", token: str = Depends(get_auth_token)):
    autos = get_automations(event_slug)
    return {"automations": autos}

@app.post("/api/admin/automations")
def admin_create_automation_endpoint(payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Automation name is required.")
    gdrive_url = payload.get("gdrive_url", "").strip()
    if not gdrive_url:
        raise HTTPException(400, "Google Drive URL or Folder ID is required.")
    event_slug = payload.get("event_slug", "brk-sir").strip()
    target_album = payload.get("target_album_slug", "auto").strip() or "auto"
    formats = payload.get("formats", ["jpg", "png", "webp"])
    trigger_date = payload.get("trigger_date", "").strip() or now_ist().strftime("%Y-%m-%d")
    trigger_time = payload.get("trigger_time", "").strip() or now_ist().strftime("%H:%M")
    repeat = payload.get("repeat_interval", "none").strip()
    is_active = 1 if payload.get("is_active", True) else 0

    auto_id = create_automation(
        name=name,
        event_slug=event_slug,
        gdrive_url=gdrive_url,
        target_album_slug=target_album,
        formats_list=formats,
        trigger_date=trigger_date,
        trigger_time=trigger_time,
        repeat_interval=repeat,
        is_active=is_active
    )
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Configured automation '{name}' (Schedule: {trigger_date} {trigger_time}, Repeat: {repeat}).")
    return {"status": "ok", "id": auto_id, "automation": get_automation_by_id(auto_id)}

@app.put("/api/admin/automations/{auto_id}")
def admin_update_automation_endpoint(auto_id: int, payload: dict, token: str = Depends(get_auth_token)):
    name = payload.get("name", "").strip()
    gdrive_url = payload.get("gdrive_url", "").strip()
    target_album = payload.get("target_album_slug", "auto").strip() or "auto"
    formats = payload.get("formats", ["jpg", "png", "webp"])
    trigger_date = payload.get("trigger_date", "").strip() or now_ist().strftime("%Y-%m-%d")
    trigger_time = payload.get("trigger_time", "").strip() or now_ist().strftime("%H:%M")
    repeat = payload.get("repeat_interval", "none").strip()
    is_active = 1 if payload.get("is_active", True) else 0

    ok = update_automation(
        auto_id=auto_id,
        name=name,
        gdrive_url=gdrive_url,
        target_album_slug=target_album,
        formats_list=formats,
        trigger_date=trigger_date,
        trigger_time=trigger_time,
        repeat_interval=repeat,
        is_active=is_active
    )
    if not ok:
        raise HTTPException(404, "Automation not found.")
    return {"status": "ok", "automation": get_automation_by_id(auto_id)}

@app.delete("/api/admin/automations/{auto_id}")
def admin_delete_automation_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    ok = delete_automation(auto_id)
    return {"status": "ok", "deleted": ok}

@app.post("/api/admin/automations/{auto_id}/toggle")
def admin_toggle_automation_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    ok = toggle_automation(auto_id)
    return {"status": "ok", "automation": get_automation_by_id(auto_id)}

@app.post("/api/admin/automations/{auto_id}/run-now")
def admin_run_automation_now_endpoint(auto_id: int, token: str = Depends(get_auth_token)):
    auto = get_automation_by_id(auto_id)
    if not auto:
        raise HTTPException(404, "Automation not found.")
    task_id = f"auto_{secrets.token_hex(4)}"
    enqueue_upload_task(
        task_id=task_id,
        task_name=f"🤖 {auto['name']} (Run Now)",
        task_type="gdrive_sync",
        event_slug=auto.get("event_slug", "brk-sir"),
        target_album_slug=auto.get("target_album_slug", "auto"),
        target_album_name="Auto Ingestion",
        source_info=auto.get("gdrive_url", ""),
        staged_dir="",
        file_count=0
    )
    record_automation_history(
        automation_id=auto_id,
        automation_name=auto["name"],
        task_id=task_id,
        status="queued",
        files_count=0,
        result_message=f"Manual Run Now executed at {now_ist().strftime('%Y-%m-%d %H:%M:%S')}. Added to Upload Queue as #{task_id}."
    )
    trigger_queue_worker()
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Manually triggered automation '{auto['name']}'. Task #{task_id} queued.")
    return {"status": "ok", "task_id": task_id}

@app.get("/api/admin/automations/history")
@app.get("/api/admin/automations/{auto_id}/history")
def admin_get_automation_history_endpoint(auto_id: Optional[int] = None, token: str = Depends(get_auth_token)):
    hist = get_automation_history(auto_id, limit=60)
    return {"history": hist}

@app.post("/api/admin/tasks/stop")
def admin_stop_task(token: str = Depends(get_auth_token)):
    global active_process
    task_cancel_event.set()
    if active_process and active_process.poll() is None:
        try:
            active_process.terminate()
            time.sleep(0.3)
            if active_process.poll() is None:
                active_process.kill()
        except Exception:
            pass
    active_process = None
    with task_lock:
        current_task["status"] = "cancelled"
        current_task["message"] = "Task stopped by admin."
        current_task["current_step"] = "Task stopped by admin."

    try:
        stop_running_tasks("Task stopped by administrator.")
    except Exception as e:
        print(f"Note on stop_running_tasks: {e}")

    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Active background task was stopped by administrator.")
    trigger_queue_worker()
    return {"status": "stopped", "message": "Background task successfully terminated."}

@app.post("/api/admin/events/{slug}/index")
def admin_event_index(slug: str, token: str = Depends(get_auth_token)):
    with task_lock:
        if current_task.get("status") == "running":
            raise HTTPException(409, "A task is already running.")
        current_task["status"] = "running"
    task_cancel_event.clear()
    threading.Thread(target=run_indexing_task, args=(slug,), daemon=True).start()
    return {"status": "started"}

@app.get("/api/admin/logs")
def admin_logs(token: str = Depends(get_auth_token)):
    log_file = STORAGE / "index.log"
    lines = []
    if log_file.exists():
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.strip().splitlines()[-100:]
        except Exception:
            lines = log_buffer[-100:]
    else:
        lines = log_buffer[-100:]
    return {"logs": lines, "task": current_task}

@app.get("/api/admin/tasks/current")
def admin_current_task(token: str = Depends(get_auth_token)):
    latest = get_latest_task()
    with task_lock:
        mem_task = dict(current_task)
    return {
        "memory_task": mem_task,
        "latest_task": latest,
        "is_running": mem_task.get("status") == "running" or (latest and latest.get("status") == "running")
    }

@app.get("/api/admin/tasks/history")
def admin_task_history(limit: int = 50, token: str = Depends(get_auth_token)):
    tasks = list_task_history(limit=limit)
    return {"tasks": tasks}

@app.get("/api/admin/tasks/{task_id}/logs")
def admin_task_logs(task_id: str, token: str = Depends(get_auth_token)):
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {"task": task}

@app.get("/api/admin/events/{slug}/people")
def admin_event_people(slug: str, token: str = Depends(get_auth_token)):
    people = list_people_for_event(slug)
    if not people:
        cluster_and_sync_people(slug, threshold=0.55)
        people = list_people_for_event(slug)
    return {"people": people}

@app.post("/api/admin/events/{slug}/people/recluster")
def admin_recluster_people(slug: str, payload: dict = None, token: str = Depends(get_auth_token)):
    with task_lock:
        if current_task.get("status") == "running":
            raise HTTPException(409, "A background task is already running.")
        current_task["status"] = "running"
    
    threshold = float(payload.get("threshold", 0.48)) if payload else 0.48
    task_id = f"cl_{secrets.token_hex(4)}"
    threading.Thread(target=run_reclustering_task, args=(slug, threshold, task_id), daemon=True).start()
    return {"status": "started", "task_id": task_id}

@app.get("/api/admin/events/{slug}/search")
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

@app.put("/api/admin/events/{slug}/people/{person_id}")
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

@app.get("/api/admin/events/{slug}/people/{person_id}/photos")
def admin_person_photos(slug: str, person_id: int, token: str = Depends(get_auth_token)):
    photos = get_person_photos(slug, person_id)
    return {"photos": photos}

@app.get("/api/public/people")
def public_people_all():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_event_people(slug)

@app.get("/api/public/people/{person_id}/photos")
def public_person_photos_direct(person_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM events ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    slug = row["slug"] if row else "brk-sir"
    return public_person_photos(slug, person_id)

@app.get("/api/public/events/{slug}/people")
def public_event_people(slug: str):
    people = list_people_for_event(slug)
    return {"people": people}

@app.get("/api/public/events/{slug}/people/{person_id}/photos")
def public_person_photos(slug: str, person_id: int):
    reload_drive_map()
    photos = get_person_photos(slug, person_id)
    for p in photos:
        if not p.get("drive_file_id"):
            p["drive_file_id"] = drive_map.get(p["filename"]) or drive_map.get(p["rel_path"])
    return {"photos": photos}

@app.get("/api/admin/storage")
def admin_storage_metrics(token: str = Depends(get_auth_token)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(id) as count, SUM(file_size_bytes) as total_vps_bytes FROM photos WHERE is_deleted = 0")
    row = cursor.fetchone()
    total_photos = row["count"] or 0
    vps_bytes = row["total_vps_bytes"] or 0
    conn.close()
    
    raw_drive_bytes = total_photos * 25 * 1024 * 1024 if total_photos > 0 else 500 * 1024 * 1024
    saved_bytes = max(0, raw_drive_bytes - vps_bytes)
    saved_percent = round((saved_bytes / raw_drive_bytes) * 100, 1) if raw_drive_bytes else 95.0
    
    return {
        "gdrive_capacity_tb": 5.0,
        "raw_drive_bytes": raw_drive_bytes,
        "vps_used_bytes": vps_bytes,
        "saved_bytes": saved_bytes,
        "saved_percent": saved_percent,
        "total_photos": total_photos,
        "compression_ratio": round(raw_drive_bytes / max(vps_bytes, 1), 1) if vps_bytes else 28.5
    }

# --- Analytics & Telemetry API Endpoints ---

@app.post("/api/analytics/event")
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


@app.get("/api/admin/analytics/dashboard")
def admin_analytics_dashboard_endpoint(
    range: str = "7d",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    refresh: bool = False,
    token: str = Depends(get_auth_token)
):
    """Returns comprehensive analytics dashboard metrics for the chosen date range."""
    return analytics.get_analytics_dashboard(range_type=range, from_date_str=from_date, to_date_str=to_date, refresh=refresh)


@app.get("/api/admin/analytics/system")
def admin_analytics_system_endpoint(
    refresh: bool = False,
    token: str = Depends(get_auth_token)
):
    """Returns live server resource metrics, queue lengths, and warning indicators."""
    return analytics.get_system_metrics(refresh=refresh)


@app.get("/api/admin/analytics/downloads")
def admin_analytics_downloads_endpoint(
    range: str = "7d",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    token: str = Depends(get_auth_token)
):
    """Returns download jobs history and statistics."""
    dash = analytics.get_analytics_dashboard(range_type=range, from_date_str=from_date, to_date_str=to_date)
    return dash.get("download_analytics", {})


@app.get("/api/admin/analytics/export")
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


@app.get("/api/admin/analytics")
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

@app.get("/api/admin/events/{slug}/duplicates")
@app.get("/api/admin/photos/duplicates")
def admin_get_duplicates(slug: str = "brk-sir", status: str = "pending", token: str = Depends(get_auth_token)):
    """Returns detected duplicate groups with dimensions, sizes, and recommended primary photo."""
    groups = get_duplicate_groups(slug, status=status)
    return {"status": "ok", "count": len(groups), "groups": groups}


@app.post("/api/admin/events/{slug}/duplicates/resolve")
@app.post("/api/admin/photos/duplicates/resolve")
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


@app.post("/api/admin/events/{slug}/duplicates/scan")
@app.post("/api/admin/photos/duplicates/scan")
def admin_scan_duplicates(slug: str = "brk-sir", token: str = Depends(get_auth_token)):
    """On-demand scan to detect and record duplicate photos without deleting anything."""
    summary = scan_and_record_duplicates(slug)
    return {"status": "ok", "summary": summary}


# ---------------------------------------------------------------------------
# Match Confidence Review & Human Feedback Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/admin/matching/reviews")
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


@app.post("/api/admin/matching/feedback")
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

@app.get("/api/admin/settings")
def admin_get_settings_endpoint(token: str = Depends(get_auth_token)):
    """Returns all system settings and configurable AI face-match thresholds."""
    settings = get_all_settings()
    return {"status": "ok", "settings": settings}


@app.post("/api/admin/settings")
def admin_update_settings_endpoint(payload: dict, token: str = Depends(get_auth_token)):
    """Updates configurable face-match thresholds and system settings."""
    allowed_keys = {
        "match_threshold_high",
        "match_threshold_medium",
        "match_threshold_min",
        "default_event_slug",
        "dedup_dhash_threshold"
    }
    updated = {}
    for k, v in payload.items():
        if k in allowed_keys:
            set_setting(k, str(v))
            updated[k] = str(v)
            if k == "match_threshold_min":
                global THRESHOLD
                try:
                    THRESHOLD = float(v)
                except Exception:
                    pass
    return {"status": "ok", "updated": updated}


# ---------------------------------------------------------------------------
# Indexing Errors & Diagnostics
# ---------------------------------------------------------------------------

@app.get("/api/admin/indexing/errors")
def admin_get_indexing_errors_endpoint(
    event_slug: str = "brk-sir",
    limit: int = 100,
    token: str = Depends(get_auth_token)
):
    """Returns indexing error logs and corrupt photo diagnostics."""
    errors = get_indexing_errors(event_slug=event_slug, limit=limit)
    return {"status": "ok", "errors": errors}


# --- Static & Dynamic HTML Page Handlers ---

@app.get("/admin")
def admin_page():
    return FileResponse(BASE / "static" / "admin.html")

@app.get("/")
def root_index():
    """Serves the primary photos gallery directly at the root landing page."""
    return FileResponse(BASE / "static" / "event.html")

@app.get("/wedding")
def legacy_wedding_redirect():
    """Redirects legacy /wedding handle directly to root landing page."""
    return RedirectResponse(url="/", status_code=301)

@app.get("/sitemap.xml")
def sitemap_xml():
    """Serves the SEO sitemap for search engine indexing."""
    f = BASE / "static" / "sitemap.xml"
    if f.exists():
        return FileResponse(f, media_type="application/xml")
    raise HTTPException(404, "Sitemap not found")

@app.get("/robots.txt")
def robots_txt():
    """Serves search engine crawling guidelines."""
    f = BASE / "static" / "robots.txt"
    if f.exists():
        return FileResponse(f, media_type="text/plain")
    raise HTTPException(404, "Robots not found")

@app.get("/favicon.ico")
def get_favicon():
    """Serves site favicon."""
    f = BASE / "static" / "favicon.ico"
    if f.exists():
        return FileResponse(f, media_type="image/x-icon")
    f_png = BASE / "static" / "images" / "favicon.png"
    if f_png.exists():
        return FileResponse(f_png, media_type="image/png")
    raise HTTPException(404, "Favicon not found")

@app.get("/manifest.json")
def get_manifest():
    """Serves PWA Web App Manifest."""
    f = BASE / "static" / "manifest.json"
    if f.exists():
        return FileResponse(f, media_type="application/manifest+json")
    raise HTTPException(404, "Manifest not found")

@app.get("/sw.js")
def get_sw():
    """Serves PWA Service Worker with root scope permissions."""
    f = BASE / "static" / "sw.js"
    if f.exists():
        return FileResponse(f, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})
    raise HTTPException(404, "Service Worker not found")

@app.get("/images/{filename}")
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

@app.get("/{slug}")
def event_landing_page(slug: str):
    """Serves static files or redirects legacy handles to root landing page."""
    # Check if static file exists
    static_file = BASE / "static" / slug
    if static_file.exists() and static_file.is_file():
        return FileResponse(static_file)
    # Redirect legacy slugs directly to root landing page
    return RedirectResponse(url="/", status_code=301)

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static_dir")
app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
