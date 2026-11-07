"""
Event-aware Face Indexer and AI Quality Inspector for BRK Photos.
Can index a specific event (--event <slug>) or all events.
Saves per-event face embeddings, names, thumbnails, SHA-256, perceptual dHash,
and photo quality metrics (blur, exposure, quality grade).
"""
import argparse
import fcntl
import gc
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

def ist_ts():
    """Returns current Indian Standard Time (IST) formatted as HH:MM:SS."""
    return datetime.now(IST).strftime("%H:%M:%S")

# Force unbuffered output so logs stream live to admin console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

# Suppress noisy library deprecation warnings from InsightFace/Skimage
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# Limit CPU threads to avoid spiking and throttling VPS CPU
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

import cv2
import numpy as np
from PIL import Image, ImageOps
from insightface.app import FaceAnalysis

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from ai_matching import (
    compute_sha256,
    compute_dhash,
    assess_photo_quality,
    scan_and_record_duplicates
)
from database import get_db, record_indexing_error

STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
PHOTOS = STORAGE / "photos"
DATA = STORAGE / "data"
MODELS = STORAGE / "models"
DET_SIZE = int(os.environ.get("DET_SIZE", "640"))
EXTS = {".jpg", ".jpeg", ".png", ".webp"}

def index_event(event_slug="brk-sir", run_clustering=False, task_id=""):
    display_name = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    print(f"[{ist_ts()}] === Starting AI Face Indexing & Quality Audit for: '{display_name}' ===", flush=True)

    # Per-event directories
    if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
        event_photos_dir = PHOTOS
        event_data_dir = DATA
    else:
        event_photos_dir = PHOTOS / "events" / event_slug
        event_data_dir = DATA / "events" / event_slug

    event_photos_dir.mkdir(parents=True, exist_ok=True)
    event_data_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = event_data_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    # Concurrency Lock: Prevent multiple concurrent indexing processes on the same event
    lock_file_path = event_data_dir / ".indexing.lock"
    lock_fd = None
    try:
        lock_fd = open(lock_file_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(f"PID: {os.getpid()}\nStarted: {ist_ts()}\nTask: {task_id}\n")
        lock_fd.flush()
    except (BlockingIOError, IOError):
        print(f"[{ist_ts()}] An AI indexing task is currently already running for '{display_name}'. Concurrency lock engaged.", flush=True)
        if lock_fd:
            try:
                lock_fd.close()
            except Exception:
                pass
        return

    emb_file = event_data_dir / "embeddings.npy"
    names_file = event_data_dir / "names.json"
    done_file = event_data_dir / "done.json"

    embs = list(np.load(emb_file)) if emb_file.exists() else []
    names = json.loads(names_file.read_text()) if names_file.exists() else []

    # Load and normalize done paths (handle both forward and backward slashes)
    done = set()
    if done_file.exists():
        try:
            raw_done = json.loads(done_file.read_text())
            for item in raw_done:
                if isinstance(item, str):
                    s = item.replace("\\", "/").strip().lstrip("/")
                    done.add(s)
        except Exception as e:
            print(f"[{ist_ts()}] Warning loading done.json: {e}", flush=True)

    def norm_rel(p: Path) -> str:
        return str(p.relative_to(event_photos_dir)).replace("\\", "/").strip().lstrip("/")

    def save():
        if embs:
            np.save(emb_file, np.array(embs, dtype=np.float32).reshape(-1, 512))
        else:
            np.save(emb_file, np.zeros((0, 512), dtype=np.float32))
        names_file.write_text(json.dumps(names))
        done_file.write_text(json.dumps(sorted(done)))

        # Mirror to root DATA for backward compatibility if default event
        if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
            if embs:
                np.save(DATA / "embeddings.npy", np.array(embs, dtype=np.float32).reshape(-1, 512))
            (DATA / "names.json").write_text(json.dumps(names))
            (DATA / "done.json").write_text(json.dumps(sorted(done)))

    files = sorted(p for p in event_photos_dir.rglob("*") if p.suffix.lower() in EXTS and not p.name.startswith("."))
    total_files = len(files)
    pending_files = [p for p in files if norm_rel(p) not in done]

    print(f"[{ist_ts()}] Scan complete: {total_files} total photos found in library.", flush=True)
    print(f"[{ist_ts()}] {len(done)} already indexed. {len(pending_files)} pending AI face analysis.", flush=True)

    if not pending_files:
        print(f"[{ist_ts()}] All {total_files} photos are already indexed! Running duplicate audit check...", flush=True)
        try:
            dedup_res = scan_and_record_duplicates(event_slug)
            print(f"[{ist_ts()}] Duplicate audit: {dedup_res.get('exact_groups', 0)} exact groups, {dedup_res.get('perceptual_groups', 0)} perceptual groups identified.", flush=True)
        except Exception as e:
            print(f"[{ist_ts()}] Duplicate scan note: {e}", flush=True)

        if run_clustering:
            print(f"[{ist_ts()}] Running facial clustering...", flush=True)
            try:
                from database import cluster_and_sync_people
                cl_res = cluster_and_sync_people(event_slug)
                print(f"[{ist_ts()}] Clustering complete: {cl_res}", flush=True)
            except Exception as e:
                print(f"[{ist_ts()}] Clustering note: {e}", flush=True)

        # Release lock
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if lock_file_path.exists():
                lock_file_path.unlink()
        except Exception:
            pass
        return

    print(f"[{ist_ts()}] Loading InsightFace neural network model...", flush=True)
    face_app = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
    face_app.prepare(ctx_id=-1, det_size=(DET_SIZE, DET_SIZE))
    print(f"[{ist_ts()}] InsightFace model initialized successfully. Processing {len(pending_files)} photos...", flush=True)

    total_pending = len(pending_files)
    processed_new = 0
    last_log_time = time.time()
    last_save_time = time.time()

    conn = get_db()
    c = conn.cursor()

    for idx, path in enumerate(pending_files, 1):
        rel = norm_rel(path)
        detected_here = 0
        raw_img = None
        thumb = None
        work_img = None
        bgr = None
        try:
            # 1. Hashes & File size
            fsize = path.stat().st_size
            sha = compute_sha256(path)

            # 2. Image Loading & Exif handling
            raw_img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            orig_w, orig_h = raw_img.size
            phash = compute_dhash(raw_img)

            # 3. Save thumbnail
            thumb = raw_img.copy()
            thumb.thumbnail((480, 480))
            thumb_name = rel.replace("/", "__") + ".jpg"
            thumb.save(thumbs_dir / thumb_name, quality=80)
            if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
                (DATA / "thumbs").mkdir(parents=True, exist_ok=True)
                thumb.save(DATA / "thumbs" / thumb_name, quality=80)

            # 4. Face Detection
            work_img = raw_img.copy()
            work_img.thumbnail((1600, 1600))
            bgr = cv2.cvtColor(np.array(work_img), cv2.COLOR_RGB2BGR)
            detected_faces = face_app.get(bgr)
            detected_here = len(detected_faces)

            # 5. Quality Assessment
            quality = assess_photo_quality(bgr, raw_img, detected_faces)
            blur_score = quality["blur_score"]
            exposure_score = quality["exposure_score"]
            quality_flags = json.dumps(quality["quality_flags"])
            quality_grade = quality["quality_grade"]

            # 6. Safe Database Updates
            # Update photo record with dimensions, hashes, and quality scores
            c.execute("""
                UPDATE photos
                SET width = ?, height = ?, file_size_bytes = ?,
                    sha256_hash = ?, phash = ?,
                    blur_score = ?, exposure_score = ?,
                    quality_flags = ?, quality_grade = ?
                WHERE rel_path = ? OR filename = ?
            """, (orig_w, orig_h, fsize, sha, phash, blur_score, exposure_score, quality_flags, quality_grade, rel, path.name))

            # Find photo id to clear old face detections before appending
            c.execute("SELECT id FROM photos WHERE rel_path = ? OR filename = ? LIMIT 1", (rel, path.name))
            p_row = c.fetchone()
            if p_row:
                p_id = p_row["id"]
                c.execute("DELETE FROM face_detections WHERE photo_id = ?", (p_id,))

            for face in detected_faces:
                embs.append(face.normed_embedding)
                names.append(rel)

        except Exception as e:
            err_msg = str(e)
            print(f"[{ist_ts()}] Error processing {rel}: {err_msg}", flush=True)
            record_indexing_error(task_id, event_slug, rel, "indexing", err_msg)
        finally:
            # Explicitly release image objects and large arrays to prevent memory growth
            try:
                if raw_img: raw_img.close()
                if thumb: thumb.close()
                if work_img: work_img.close()
                del bgr
                del raw_img, thumb, work_img
            except Exception:
                pass

        done.add(rel)
        processed_new += 1

        # Periodic garbage collection every 50 photos to keep RSS memory bounded
        if processed_new % 50 == 0:
            gc.collect()

        # Yield CPU briefly so server remains completely responsive
        time.sleep(0.04)

        # Commit DB and log progress
        curr_time = time.time()
        if (processed_new % 5 == 0) or (detected_here > 0) or (curr_time - last_log_time >= 4) or (idx == total_pending):
            conn.commit()
            total_done_now = len(done)
            pct = int((total_done_now / max(total_files, 1)) * 100)
            print(f"[{ist_ts()}] Progress: {total_done_now}/{total_files} ({pct}%) [Batch: {idx}/{total_pending}] | Photo: {path.name} | Detected: {detected_here} face(s) | Total library faces: {len(embs)}", flush=True)
            last_log_time = curr_time

        # Save embeddings to disk every 20 photos or every 30 seconds
        if (processed_new % 20 == 0) or (curr_time - last_save_time >= 30) or (idx == total_pending):
            save()
            last_save_time = curr_time

    conn.commit()
    conn.close()
    save()

    print(f"[{ist_ts()}] AI Face Indexing finished for '{display_name}'! Total: {len(done)} photos, {len(embs)} faces indexed.", flush=True)

    # Run duplicate detection scan
    try:
        print(f"[{ist_ts()}] Running duplicate photo audit...", flush=True)
        dedup_res = scan_and_record_duplicates(event_slug)
        print(f"[{ist_ts()}] Duplicate audit: {dedup_res.get('exact_groups', 0)} exact groups, {dedup_res.get('perceptual_groups', 0)} perceptual groups identified.", flush=True)
    except Exception as e:
        print(f"[{ist_ts()}] Duplicate scan note: {e}", flush=True)

    if run_clustering:
        try:
            from database import cluster_and_sync_people
            print(f"[{ist_ts()}] Running facial clustering...", flush=True)
            cl_res = cluster_and_sync_people(event_slug)
            print(f"[{ist_ts()}] Clustering complete: {cl_res}", flush=True)
        except Exception as e:
            print(f"[{ist_ts()}] Clustering note: {e}", flush=True)

    # Release lock
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        if lock_file_path.exists():
            lock_file_path.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BRK Photos Multi-Event Face Indexer & Quality Inspector")
    parser.add_argument("--event", type=str, default="brk-sir", help="Event slug to index")
    parser.add_argument("--cluster", action="store_true", help="Run clustering after indexing (optional)")
    parser.add_argument("--task-id", type=str, default="", help="Task ID for tracking and error logging")
    args = parser.parse_args()
    index_event(args.event, run_clustering=args.cluster, task_id=args.task_id)
