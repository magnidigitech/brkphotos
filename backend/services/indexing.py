import os
import sys
import re
import time
import secrets
import subprocess
from datetime import datetime
from pathlib import Path

from backend.config import (
    BASE, PHOTOS, DATA, STORAGE,
    current_task, task_lock, log_buffer, task_cancel_event,
    append_log
)
from backend.db.connection import get_db, now_ist, extract_photo_capture_date
from backend.db.tasks import create_task_record, update_task_progress
from backend.db.people import cluster_and_sync_people
from backend.services.face_search import get_event_index
from backend.services.media import optimize_photo_inplace

active_process = None
_last_sync_folders_time = {}

def run_indexing_task(event_slug="brk-sir", task_id=None):
    global active_process
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

def sync_event_folders_and_photos(slug: str, force: bool = False):
    """Auto-detect physical folders on disk, register missing albums, and link unassigned photos (throttled to 5 mins)."""
    now_t = time.time()
    if not force and (now_t - _last_sync_folders_time.get(slug, 0) < 300):
        return
    _last_sync_folders_time[slug] = now_t

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
        conn.commit()
    except Exception as e:
        print(f"Error syncing folders for event {slug}: {e}")
    finally:
        conn.close()
