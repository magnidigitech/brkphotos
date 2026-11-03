"""
Accurate Face Cropping & Avatar Generator for BRK Photos.
Extracts square face crops for person profiles using InsightFace.
Saves avatars to storage/data/faces/person_{id}.jpg.
"""
import sqlite3
import cv2
import numpy as np
from PIL import Image, ImageOps
from pathlib import Path
import os
import json

BASE = Path(__file__).parent
STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
MODELS = STORAGE / "models"
DATA = STORAGE / "data"
PHOTOS = STORAGE / "photos"
FACES = DATA / "faces"
FACES.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "magniwedding.db"

_crop_app = None

def get_face_analysis_app():
    global _crop_app
    if _crop_app is None:
        try:
            from insightface.app import FaceAnalysis
            _crop_app = FaceAnalysis(name="buffalo_l", root=str(MODELS), providers=["CPUExecutionProvider"])
            _crop_app.prepare(ctx_id=-1, det_size=(640, 640))
        except Exception as e:
            print(f"Warning: InsightFace app initialization in crop_faces: {e}")
            _crop_app = None
    return _crop_app

def crop_people_faces(target_event_slug=None, logger=None):
    def log(msg):
        if logger:
            logger(msg)
        else:
            print(f"[FaceCrop] {msg}")

    app = get_face_analysis_app()
    if app is None:
        log("InsightFace not available for face cropping; skipping crop generation.")
        return {"status": "error", "message": "InsightFace not available"}

    if not DB_PATH.exists():
        log(f"Database file not found at {DB_PATH}")
        return {"status": "error", "message": "Database not found"}

    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if target_event_slug and target_event_slug not in {"all", "*"}:
        c.execute("""
            SELECT p.id, p.event_id, p.name, p.avatar_thumb_path, e.slug as event_slug 
            FROM person_profiles p
            JOIN events e ON p.event_id = e.id
            WHERE e.slug = ?
            ORDER BY p.id ASC
        """, (target_event_slug,))
    else:
        c.execute("""
            SELECT p.id, p.event_id, p.name, p.avatar_thumb_path, e.slug as event_slug 
            FROM person_profiles p
            JOIN events e ON p.event_id = e.id
            ORDER BY p.id ASC
        """)
    profiles = c.fetchall()
    log(f"Processing {len(profiles)} person profiles for face crop avatars...")

    cache_img_faces = {}
    event_embs_cache = {}
    cropped_count = 0
    existing_count = 0

    for p in profiles:
        pid = p["id"]
        ev_slug = p["event_slug"]
        out_file = FACES / f"person_{pid}.jpg"

        # Check if already exists and valid
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
            SELECT fd.embedding_offset, ph.rel_path, ph.filename
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
            offsets = [d["embedding_offset"] for d in detections if d["embedding_offset"] < len(embs)]
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
                    img.thumbnail((1600, 1600))
                    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                    faces = app.get(bgr)
                    cache_img_faces[cache_key] = (img, faces)
                except Exception:
                    continue
            else:
                img, faces = cache_img_faces[cache_key]

            if not faces:
                continue

            if person_emb is not None:
                for f in faces:
                    sim = float(np.dot(f.normed_embedding, person_emb))
                    if sim > best_sim:
                        best_sim = sim
                        best_crop_face = f
                        best_crop_img = img
            else:
                best_crop_face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
                best_crop_img = img
                break

            if best_sim >= 0.60:
                break

        if best_crop_face is None or best_crop_img is None:
            continue

        face = best_crop_face
        img = best_crop_img
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        pad_x = int(w * 0.35)
        pad_y = int(h * 0.35)

        box = (
            max(0, x1 - pad_x),
            max(0, y1 - pad_y),
            min(img.width, x2 + pad_x),
            min(img.height, y2 + pad_y)
        )
        face_img = img.crop(box)
        face_img.thumbnail((240, 240))
        face_img.save(out_file, quality=90)

        new_rel = f"faces/person_{pid}.jpg"
        c.execute("UPDATE person_profiles SET avatar_thumb_path = ? WHERE id = ?", (new_rel, pid))
        cropped_count += 1

    conn.commit()
    conn.close()
    log(f"Face avatars complete: {cropped_count} generated, {existing_count} already existed.")
    return {"status": "ok", "cropped": cropped_count, "existing": existing_count}

if __name__ == "__main__":
    crop_people_faces()
