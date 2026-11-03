"""
Event-aware Face Indexer for BRK Photos.
Can index a specific event (--event <slug>) or all events.
Saves per-event face embeddings, names, and thumbnails.
"""
import argparse, json, os, sys, time, warnings
from datetime import datetime
from pathlib import Path

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

BASE = Path(__file__).parent
STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
PHOTOS = STORAGE / "photos"
DATA = STORAGE / "data"
MODELS = STORAGE / "models"
DET_SIZE = int(os.environ.get("DET_SIZE", "960"))
EXTS = {".jpg", ".jpeg", ".png", ".webp"}

def index_event(event_slug="brk-sir"):
    display_name = "BRK Sir" if event_slug in {"default", "wedding", "brk-sir", "brksir"} else event_slug
    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{now_str}] === Starting AI Face Indexing for: '{display_name}' ===", flush=True)
    
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

    emb_file = event_data_dir / "embeddings.npy"
    names_file = event_data_dir / "names.json"
    done_file = event_data_dir / "done.json"

    embs = list(np.load(emb_file)) if emb_file.exists() else []
    names = json.loads(names_file.read_text()) if names_file.exists() else []
    done = set(json.loads(done_file.read_text())) if done_file.exists() else set()

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
    pending_files = [p for p in files if str(p.relative_to(event_photos_dir)) not in done]
    
    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{now_str}] Scan complete: {total_files} total photos found in library.", flush=True)
    print(f"[{now_str}] {len(done)} already indexed. {len(pending_files)} pending AI face analysis.", flush=True)

    if not pending_files:
        print(f"[{now_str}] All {total_files} photos are already indexed! Running clustering check...", flush=True)
        try:
            from database import cluster_and_sync_people
            cl_res = cluster_and_sync_people(event_slug)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Clustering complete: {cl_res}", flush=True)
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Clustering note: {e}", flush=True)
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Loading InsightFace neural network model...", flush=True)
    face_app = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
    face_app.prepare(ctx_id=-1, det_size=(DET_SIZE, DET_SIZE))
    print(f"[{datetime.now().strftime('%H:%M:%S')}] InsightFace model initialized successfully. Processing {len(pending_files)} photos...", flush=True)

    processed_new = 0
    last_log_time = time.time()
    last_save_time = time.time()

    for i, path in enumerate(files, 1):
        rel = str(path.relative_to(event_photos_dir))
        if rel in done:
            continue
        
        detected_here = 0
        try:
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            
            # Save thumbnail
            thumb = img.copy()
            thumb.thumbnail((480, 480))
            thumb_name = rel.replace("/", "__") + ".jpg"
            thumb.save(thumbs_dir / thumb_name, quality=80)
            if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
                (DATA / "thumbs").mkdir(parents=True, exist_ok=True)
                thumb.save(DATA / "thumbs" / thumb_name, quality=80)

            # Detect faces
            img.thumbnail((2000, 2000))
            bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            detected_faces = face_app.get(bgr)
            detected_here = len(detected_faces)
            for face in detected_faces:
                embs.append(face.normed_embedding)
                names.append(rel)
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Skipped {rel}: {e}", flush=True)
            
        done.add(rel)
        processed_new += 1
        
        # Yield CPU briefly so server remains completely responsive
        time.sleep(0.04)
        
        # Live progress log every 5 photos, or if faces detected, or at least every 4 seconds
        curr_time = time.time()
        if (processed_new % 5 == 0) or (detected_here > 0) or (curr_time - last_log_time >= 4):
            pct = int((i / max(total_files, 1)) * 100)
            now_ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{now_ts}] Progress: {i}/{total_files} ({pct}%) | Photo: {path.name} | Detected: {detected_here} face(s) | Total library faces: {len(embs)}", flush=True)
            last_log_time = curr_time

        # Save to disk every 20 photos or every 30 seconds
        if (processed_new % 20 == 0) or (curr_time - last_save_time >= 30):
            save()
            last_save_time = curr_time

    save()
    now_ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{now_ts}] AI Face Indexing finished for '{display_name}'! Total: {len(done)} photos, {len(embs)} faces indexed.", flush=True)
    try:
        from database import cluster_and_sync_people
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running facial clustering...", flush=True)
        cl_res = cluster_and_sync_people(event_slug)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Clustering complete: {cl_res}", flush=True)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Clustering note: {e}", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BRK Photos Multi-Event Face Indexer")
    parser.add_argument("--event", type=str, default="brk-sir", help="Event slug to index")
    args = parser.parse_args()
    index_event(args.event)
