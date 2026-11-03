"""
Accurate Face Cropping, Validation & Avatar Generator for BRK Photos.
Extracts square face crops for person profiles and indexed photographs using InsightFace
with automatic OpenCV Haar Cascade fallback.
Saves avatars to storage/data/faces/person_{id}.jpg and crops to storage/data/faces/crop_{photo_id}_{idx}.jpg.
"""
import sqlite3
import cv2
import numpy as np
from PIL import Image, ImageOps
from pathlib import Path
import os
import sys
import json
from datetime import datetime

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
MODELS = STORAGE / "models"
DATA = STORAGE / "data"
PHOTOS = STORAGE / "photos"
FACES = DATA / "faces"
FACES.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "magniwedding.db"

_crop_app = None

class OpenCVFaceFallback:
    """Lightweight built-in OpenCV fallback if InsightFace deep-learning model is unavailable."""
    def __init__(self):
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.cascade = cv2.CascadeClassifier(cascade_path)

    def get(self, bgr):
        try:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            rects = self.cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            detections = []
            class Detection:
                pass
            for (x, y, w, h) in rects:
                d = Detection()
                d.bbox = np.array([x, y, x + w, y + h])
                d.normed_embedding = None
                detections.append(d)
            return detections
        except Exception:
            return []

def get_face_analysis_app(face_app=None):
    global _crop_app
    if face_app is not None:
        return face_app
    if _crop_app is not None:
        return _crop_app

    try:
        from insightface.app import FaceAnalysis
        _crop_app = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
        _crop_app.prepare(ctx_id=-1, det_size=(640, 640))
        return _crop_app
    except Exception as e:
        print(f"[FaceCrop] InsightFace not loaded ({e}), using OpenCV detector fallback.")
        _crop_app = OpenCVFaceFallback()
        return _crop_app

def crop_face_bounding_box(img, bbox, pad_ratio=0.35, target_size=(240, 240)):
    """Crops a square face from PIL Image given bbox [x1, y1, x2, y2] with margin padding."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    pad_x = int(w * pad_ratio)
    pad_y = int(h * pad_ratio)

    box = (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(img.width, x2 + pad_x),
        min(img.height, y2 + pad_y)
    )
    face_img = img.crop(box)
    face_img.thumbnail(target_size)
    return face_img

def crop_people_faces(target_event_slug=None, logger=None, face_app=None):
    """
    Ensures every identified person profile in the event has a cropped avatar image in
    storage/data/faces/person_{id}.jpg.
    """
    def log(msg):
        t_str = datetime.now().strftime("%H:%M:%S")
        if logger:
            logger(f"[{t_str}] {msg}")
        else:
            print(f"[{t_str}] [FaceCrop] {msg}", flush=True)

    app = get_face_analysis_app(face_app)

    if not DB_PATH.exists():
        log(f"Database file not found at {DB_PATH}")
        return {"status": "error", "message": "Database not found"}

    conn = sqlite3.connect(str(DB_PATH), timeout=25.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if target_event_slug and target_event_slug not in {"all", "*"}:
        c.execute("""
            SELECT p.id, p.event_id, p.name, p.avatar_thumb_path, e.slug as event_slug 
            FROM person_profiles p
            JOIN events e ON p.event_id = e.id
            WHERE e.slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')
            ORDER BY p.id ASC
        """, (target_event_slug, target_event_slug))
    else:
        c.execute("""
            SELECT p.id, p.event_id, p.name, p.avatar_thumb_path, e.slug as event_slug 
            FROM person_profiles p
            JOIN events e ON p.event_id = e.id
            ORDER BY p.id ASC
        """)
    profiles = c.fetchall()

    cache_img_faces = {}
    event_embs_cache = {}
    cropped_count = 0
    existing_count = 0

    for p in profiles:
        pid = p["id"]
        ev_slug = p["event_slug"]
        out_file = FACES / f"person_{pid}.jpg"

        # Check if already exists and valid (> 500 bytes)
        if out_file.exists() and out_file.stat().st_size > 500:
            existing_count += 1
            if p["avatar_thumb_path"] != f"faces/person_{pid}.jpg":
                c.execute("UPDATE person_profiles SET avatar_thumb_path = ? WHERE id = ?", (f"faces/person_{pid}.jpg", pid))
            continue

        # Load event embeddings if not already in memory
        if ev_slug not in event_embs_cache:
            if ev_slug in {"default", "wedding", "brk-sir", "brksir"}:
                e_path = DATA / "embeddings.npy"
            else:
                e_path = DATA / "events" / ev_slug / "embeddings.npy"
                if not e_path.exists():
                    e_path = DATA / "embeddings.npy"
            if e_path.exists():
                try:
                    event_embs_cache[ev_slug] = np.load(e_path)
                except Exception:
                    event_embs_cache[ev_slug] = None
            else:
                event_embs_cache[ev_slug] = None

        embs = event_embs_cache.get(ev_slug)

        # Get all detections for this person
        c.execute("""
            SELECT fd.id as fd_id, fd.embedding_offset, ph.id as photo_id, ph.rel_path, ph.filename, fd.bbox_json
            FROM face_detections fd
            JOIN photos ph ON fd.photo_id = ph.id
            WHERE fd.person_id = ? AND ph.is_deleted = 0
            ORDER BY fd.confidence DESC
            LIMIT 10
        """, (pid,))
        detections = c.fetchall()
        if not detections:
            continue

        # Compute centroid embedding of this person
        person_emb = None
        if embs is not None:
            offsets = [d["embedding_offset"] for d in detections if d["embedding_offset"] is not None and d["embedding_offset"] < len(embs)]
            if offsets:
                p_embs = embs[offsets]
                mean_e = np.mean(p_embs, axis=0)
                norm = np.linalg.norm(mean_e)
                if norm > 0:
                    person_emb = mean_e / norm

        # Find best photo to crop from
        best_crop_face = None
        best_crop_img = None
        best_sim = -1.0

        for d in detections[:6]:
            rel = d["rel_path"]
            img_path = PHOTOS / rel
            if not img_path.exists():
                matches = list(PHOTOS.glob(f"**/{Path(rel).name}"))
                if matches:
                    img_path = matches[0]
                else:
                    continue

            cache_key = str(img_path)
            if cache_key not in cache_img_faces:
                try:
                    img = ImageOps.exif_transpose(Image.open(img_path)).convert("RGB")
                    thumb_img = img.copy()
                    thumb_img.thumbnail((1600, 1600))
                    bgr = cv2.cvtColor(np.array(thumb_img), cv2.COLOR_RGB2BGR)
                    faces = app.get(bgr)
                    cache_img_faces[cache_key] = (thumb_img, faces)
                except Exception:
                    continue
            else:
                thumb_img, faces = cache_img_faces[cache_key]

            if not faces:
                continue

            if person_emb is not None:
                for f in faces:
                    if getattr(f, 'normed_embedding', None) is not None:
                        sim = float(np.dot(f.normed_embedding, person_emb))
                        if sim > best_sim:
                            best_sim = sim
                            best_crop_face = f
                            best_crop_img = thumb_img
                    else:
                        best_crop_face = f
                        best_crop_img = thumb_img
                        break
            else:
                best_crop_face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                best_crop_img = thumb_img
                break

            if best_sim >= 0.55:
                break

        if best_crop_face is None or best_crop_img is None:
            continue

        try:
            face_img = crop_face_bounding_box(best_crop_img, best_crop_face.bbox)
            face_img.save(out_file, quality=92)
            c.execute("UPDATE person_profiles SET avatar_thumb_path = ? WHERE id = ?", (f"faces/person_{pid}.jpg", pid))
            cropped_count += 1
        except Exception as e:
            continue

    conn.commit()
    conn.close()
    return {"status": "ok", "cropped": cropped_count, "existing": existing_count}

def validate_and_recover_face_crops(target_event_slug="brk-sir", logger=None, face_app=None):
    """
    Backfill and recovery operation for indexed photographs with missing face crops.
    Validates all photographs in library, identifies missing crops, extracts faces,
    saves crop files, updates database, and prints comprehensive validation logs.
    """
    def log(msg):
        t_str = datetime.now().strftime("%H:%M:%S")
        if logger:
            logger(f"[{t_str}] {msg}")
        else:
            print(f"[{t_str}] {msg}", flush=True)

    log("Starting face crop validation...")

    if not DB_PATH.exists():
        log(f"Database not found at {DB_PATH}")
        return {"status": "error", "message": "Database not found"}

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Ensure schema has crop_path in face_detections
    try:
        c.execute("ALTER TABLE face_detections ADD COLUMN crop_path TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Resolve event
    c.execute("SELECT id, slug FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir') LIMIT 1", (target_event_slug, target_event_slug))
    ev_row = c.fetchone()
    if ev_row:
        event_id = ev_row["id"]
        c.execute("SELECT id, rel_path, filename FROM photos WHERE event_id = ? AND is_deleted = 0 ORDER BY id ASC", (event_id,))
    else:
        event_id = None
        c.execute("SELECT id, rel_path, filename FROM photos WHERE is_deleted = 0 ORDER BY id ASC")

    photos = c.fetchall()
    total_photos = len(photos)
    log(f"Found {total_photos} indexed photographs.")

    log("Checking existing face crop records...")

    # Fetch all face detections mapped by photo_id
    c.execute("SELECT id, photo_id, person_id, bbox_json, crop_path FROM face_detections")
    detections_by_photo = {}
    for d in c.fetchall():
        detections_by_photo.setdefault(d["photo_id"], []).append(dict(d))

    valid_photos_count = 0
    photos_requiring_crops = []

    for p in photos:
        pid = p["id"]
        dets = detections_by_photo.get(pid, [])
        
        # Check if photo already has valid physical crop files on disk
        has_valid_crops = False
        if dets:
            crops_exist = True
            for d in dets:
                cp = d.get("crop_path")
                if cp:
                    cfile = DATA / cp
                else:
                    cfile = FACES / f"crop_{pid}_{d['id']}.jpg"
                if not (cfile.exists() and cfile.stat().st_size > 500):
                    crops_exist = False
                    break
            if crops_exist:
                has_valid_crops = True

        if has_valid_crops:
            valid_photos_count += 1
        else:
            photos_requiring_crops.append(p)

    log(f"Found {valid_photos_count} photographs with valid face crops.")
    log(f"Found {len(photos_requiring_crops)} photographs requiring face crop generation.")

    if not photos_requiring_crops:
        # Also ensure person profile avatars exist
        crop_people_faces(target_event_slug, logger=logger, face_app=face_app)
        log("Face crop recovery completed. All photographs have valid crops.")
        conn.close()
        return {
            "status": "ok",
            "total_photos": total_photos,
            "valid_crops": valid_photos_count,
            "generated_crops": 0,
            "failed_crops": 0
        }

    log("Starting missing face crop generation...")
    app = get_face_analysis_app(face_app)

    generated_crops = 0
    failed_crops = 0

    # Process photos requiring crops
    for p in photos_requiring_crops:
        pid = p["id"]
        rel = p["rel_path"]
        img_path = PHOTOS / rel
        if not img_path.exists():
            matches = list(PHOTOS.glob(f"**/{Path(rel).name}"))
            if matches:
                img_path = matches[0]
            else:
                failed_crops += 1
                continue

        try:
            img = ImageOps.exif_transpose(Image.open(img_path)).convert("RGB")
            thumb_img = img.copy()
            thumb_img.thumbnail((1600, 1600))
            bgr = cv2.cvtColor(np.array(thumb_img), cv2.COLOR_RGB2BGR)
            detected_faces = app.get(bgr)

            if not detected_faces:
                # No faces detected in this photo
                continue

            dets = detections_by_photo.get(pid, [])

            for f_idx, face in enumerate(detected_faces):
                crop_rel = f"faces/crop_{pid}_{f_idx}.jpg"
                crop_file = DATA / crop_rel

                face_crop = crop_face_bounding_box(thumb_img, face.bbox)
                face_crop.save(crop_file, quality=90)
                generated_crops += 1

                bbox_json = json.dumps([int(v) for v in face.bbox])

                if f_idx < len(dets):
                    det_id = dets[f_idx]["id"]
                    c.execute("""
                        UPDATE face_detections 
                        SET bbox_json = ?, crop_path = ?
                        WHERE id = ?
                    """, (bbox_json, crop_rel, det_id))
                    person_id = dets[f_idx].get("person_id")
                    if person_id:
                        p_avatar = FACES / f"person_{person_id}.jpg"
                        if not p_avatar.exists() or p_avatar.stat().st_size <= 500:
                            face_crop.save(p_avatar, quality=92)
                            c.execute("UPDATE person_profiles SET avatar_thumb_path = ? WHERE id = ?", (f"faces/person_{person_id}.jpg", person_id))
                else:
                    c.execute("""
                        INSERT INTO face_detections (event_id, photo_id, bbox_json, crop_path, confidence)
                        VALUES (?, ?, ?, ?, ?)
                    """, (event_id or 1, pid, bbox_json, crop_rel, 0.95))
        except Exception as e:
            failed_crops += 1
            log(f"Error cropping photo #{pid} ({p['filename']}): {e}")

    conn.commit()

    # Sync any remaining missing person profiles avatars
    try:
        crop_people_faces(target_event_slug, logger=logger, face_app=app)
    except Exception as e:
        log(f"Profile avatar sync note: {e}")

    conn.close()

    log(f"Generated {generated_crops} face crops.")
    log(f"Failed: {failed_crops} face crop operations.")
    log("Face crop recovery completed.")

    return {
        "status": "ok",
        "total_photos": total_photos,
        "valid_crops": valid_photos_count + (len(photos_requiring_crops) - failed_crops),
        "generated_crops": generated_crops,
        "failed_crops": failed_crops
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Accurate Face Cropping & Recovery for BRK Photos")
    parser.add_argument("--event", type=str, default="brk-sir", help="Event slug")
    parser.add_argument("--validate", action="store_true", help="Run full face crop validation and recovery")
    args = parser.parse_args()

    validate_and_recover_face_crops(args.event)
