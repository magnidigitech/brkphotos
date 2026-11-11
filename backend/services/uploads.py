import os
import sys
import re
import json
import time
import shutil
import zipfile
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageOps

from backend.config import (
    PHOTOS, DATA, STORAGE,
    current_task, task_lock, task_cancel_event,
    append_log
)
from backend.db.connection import get_db, now_ist
from backend.db.tasks import create_task_record, update_task_progress, update_queue_task
from backend.services.media import (
    drive_map, optimize_photo_inplace, download_gdrive_file, reload_drive_map
)
from backend.services.indexing import run_indexing_task, sync_photos_to_database

active_process = None

def run_gdrive_download(url: str, dest_dir: Path, rel_prefix: str, event_slug: str, allowed_exts: list, task_id=None):
    global active_process
    display_title = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    task_cancel_event.clear()
    if not task_id:
        task_id = f"gdrive_{secrets.token_hex(4)}"
        create_task_record(task_id, "gdrive_ingest", display_title, dest_dir.name, url)

    if not allowed_exts:
        allowed_exts = ["jpg", "jpeg", "png", "webp"]
    allowed_set = {x.lower().strip(".") for x in allowed_exts}

    with task_lock:
        current_task.update({
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
        })

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

def execute_queued_job(job: dict):
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
            try:
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
            finally:
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

