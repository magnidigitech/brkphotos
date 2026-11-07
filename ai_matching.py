"""
AI Matching, Perceptual Hashing, Photo Quality Assessment and Duplicate Detection
for BRK Photos (photos.bhashyamramakrishna.in).
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

# Configurable defaults
DEFAULT_HIGH_THRESHOLD = 0.60
DEFAULT_MEDIUM_THRESHOLD = 0.48
DEFAULT_MIN_THRESHOLD = 0.40
DEFAULT_DHASH_HAMMING_THRESHOLD = 4


def compute_sha256(source: Union[str, Path, bytes]) -> str:
    """Computes SHA-256 hash of a file or byte buffer."""
    h = hashlib.sha256()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.is_file():
            return ""
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    elif isinstance(source, (bytes, bytearray)):
        h.update(source)
    return h.hexdigest()


def compute_dhash(source: Union[Image.Image, str, Path, np.ndarray], hash_size: int = 8) -> str:
    """
    Computes a 64-bit difference hash (dHash) for an image.
    Resizes to (hash_size + 1, hash_size) grayscale, compares adjacent horizontal pixels,
    and returns a 16-character hexadecimal string.
    """
    try:
        if isinstance(source, (str, Path)):
            img = Image.open(source)
        elif isinstance(source, np.ndarray):
            if len(source.shape) == 3 and source.shape[2] == 3:
                img = Image.fromarray(cv2.cvtColor(source, cv2.COLOR_BGR2RGB))
            else:
                img = Image.fromarray(source)
        elif isinstance(source, Image.Image):
            img = source
        else:
            return ""

        # Resize to 9x8 grayscale using bilinear filtering
        gray = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
        pixels = np.array(gray, dtype=np.int32)
        diff = pixels[:, 1:] > pixels[:, :-1]
        
        # Pack 64 booleans into 64-bit integer
        h = 0
        for bit in diff.flatten():
            h = (h << 1) | int(bit)
        return f"{h:016x}"
    except Exception:
        return ""


def dhash_hamming_distance(hash1: str, hash2: str) -> int:
    """Calculates bitwise Hamming distance between two 16-hex-char dHashes."""
    if not hash1 or not hash2 or len(hash1) != 16 or len(hash2) != 16:
        return 64
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        return bin(val1 ^ val2).count("1")
    except Exception:
        return 64


def assess_photo_quality(
    cv_img_bgr: Optional[Union[np.ndarray, str, Path]] = None,
    pil_img: Optional[Image.Image] = None,
    detected_faces: Optional[List] = None
) -> Dict:
    """
    Assesses photo quality during indexing:
    - Blurriness using Laplacian variance
    - Exposure (mean luminance, dark clipping, highlight blowouts)
    - Low-resolution or border-clipped faces
    - Detector confidence

    Returns a dict with:
      - blur_score: float (higher is sharper)
      - exposure_score: float (0-255 luminance)
      - quality_flags: list[str] (e.g. ['blurry', 'underexposed'])
      - quality_grade: 'good' | 'acceptable' | 'low'
    """
    flags = []
    blur_score = 0.0
    exposure_score = 128.0

    if isinstance(cv_img_bgr, (str, Path)):
        p = str(cv_img_bgr)
        cv_img_bgr = cv2.imread(p)
        if pil_img is None:
            try:
                pil_img = Image.open(p)
            except Exception:
                pass

    if cv_img_bgr is not None and cv_img_bgr.size > 0:
        if len(cv_img_bgr.shape) == 3:
            gray = cv2.cvtColor(cv_img_bgr, cv2.COLOR_BGR2GRAY)
        else:
            gray = cv_img_bgr

        # Laplacian variance for focus/sharpness
        try:
            blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            if blur_score < 60.0:
                flags.append("blurry")
        except Exception:
            blur_score = 0.0

        # Exposure metrics
        try:
            exposure_score = float(np.mean(gray))
            clipped_low = float(np.mean(gray <= 8))
            clipped_high = float(np.mean(gray >= 248))

            if exposure_score < 45.0 or clipped_low > 0.35:
                flags.append("underexposed")
            elif exposure_score > 215.0 or clipped_high > 0.20:
                flags.append("overexposed")
        except Exception:
            pass

    # Inspect detected faces for size, borders, and confidence
    if detected_faces and pil_img is not None:
        img_w, img_h = pil_img.size
        for face in detected_faces:
            try:
                bbox = getattr(face, "bbox", None)
                if bbox is not None and len(bbox) >= 4:
                    x1, y1, x2, y2 = bbox[:4]
                    fw = x2 - x1
                    fh = y2 - y1
                    if (fw < 50 or fh < 50) and "low_res_face" not in flags:
                        flags.append("low_res_face")
                    if (x1 < 5 or y1 < 5 or x2 > (img_w - 5) or y2 > (img_h - 5)) and "border_face" not in flags:
                        flags.append("border_face")

                det_score = getattr(face, "det_score", None)
                if det_score is not None and det_score < 0.65 and "low_det_confidence" not in flags:
                    flags.append("low_det_confidence")
            except Exception:
                pass

    # Determine overall quality grade
    if any(f in flags for f in ("blurry", "underexposed", "overexposed")):
        grade = "low"
    elif any(f in flags for f in ("low_res_face", "border_face", "low_det_confidence")):
        grade = "acceptable"
    else:
        grade = "good"

    return {
        "blur_score": round(blur_score, 2),
        "exposure_score": round(exposure_score, 2),
        "quality_flags": flags,
        "quality_grade": grade
    }


def classify_confidence(
    similarity: float,
    high_thresh: float = DEFAULT_HIGH_THRESHOLD,
    med_thresh: float = DEFAULT_MEDIUM_THRESHOLD,
    min_thresh: float = DEFAULT_MIN_THRESHOLD
) -> Dict:
    """
    Calibrates ArcFace cosine similarity into clear confidence categories.
    Never displays similarity as a percentage probability.
    """
    score = round(float(similarity), 3)
    if score >= high_thresh:
        tier = "high"
        label = "High Confidence"
    elif score >= med_thresh:
        tier = "medium"
        label = "Medium Confidence"
    elif score >= min_thresh:
        tier = "low"
        label = "Low Confidence"
    else:
        tier = "none"
        label = "Uncertain"

    return {
        "similarity": score,
        "display_score": f"{score:.3f}",
        "tier": tier,
        "label": label
    }


def scan_and_record_duplicates(event_slug: str = "brk-sir", hamming_threshold: int = DEFAULT_DHASH_HAMMING_THRESHOLD) -> Dict:
    """
    Scans indexed photos for exact SHA-256 and perceptual dHash duplicates.
    Records identified duplicate groups in the database without deleting anything.
    Recommends keeping the highest-quality original based on dimensions and file size.
    """
    from database import get_db, record_duplicate_pair

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.filename, p.rel_path, p.file_size_bytes, p.width, p.height,
               p.sha256_hash, p.phash, p.quality_grade
        FROM photos p
        JOIN events e ON p.event_id = e.id
        WHERE (e.slug = ? OR ? IN ('brk-sir', 'brksir', 'wedding', 'default', ''))
          AND p.is_deleted = 0
    """, (event_slug, event_slug))
    photos = [dict(r) for r in c.fetchall()]
    conn.close()

    if len(photos) < 2:
        return {"exact_groups": 0, "perceptual_groups": 0, "total_duplicate_photos": 0}

    def quality_rank(item):
        w = item.get("width") or 0
        h = item.get("height") or 0
        sz = item.get("file_size_bytes") or 0
        grade_w = 0 if item.get("quality_grade") == "good" else (1 if item.get("quality_grade") == "acceptable" else 2)
        return (-(w * h), -sz, grade_w)

    exact_groups_count = 0
    perceptual_groups_count = 0
    photos_in_exact_groups = set()

    # 1. Exact duplicates via SHA-256
    sha_map = {}
    for p in photos:
        h = p.get("sha256_hash")
        if h and len(h) == 64:
            sha_map.setdefault(h, []).append(p)

    for sha, group in sha_map.items():
        if len(group) > 1:
            exact_groups_count += 1
            group.sort(key=quality_rank)
            primary = group[0]
            group_id = f"exact_{sha[:12]}"
            for dup in group[1:]:
                photos_in_exact_groups.add(dup["id"])
                photos_in_exact_groups.add(primary["id"])
                record_duplicate_pair(group_id, "exact", 1.0, primary["id"], dup["id"])

    # 2. Perceptual duplicates via dHash
    # Filter photos that have valid phash
    phash_photos = [p for p in photos if p.get("phash") and len(p["phash"]) == 16]

    # Pairwise comparison
    for i in range(len(phash_photos)):
        p1 = phash_photos[i]
        for j in range(i + 1, len(phash_photos)):
            p2 = phash_photos[j]
            # If already in the same exact group, skip
            if p1["id"] in photos_in_exact_groups and p2["id"] in photos_in_exact_groups:
                if p1.get("sha256_hash") == p2.get("sha256_hash"):
                    continue

            dist = dhash_hamming_distance(p1["phash"], p2["phash"])
            if dist <= hamming_threshold:
                # Perceptual match!
                score = round(1.0 - (dist / 64.0), 3)
                pair = [p1, p2]
                pair.sort(key=quality_rank)
                primary = pair[0]
                duplicate = pair[1]
                gid = f"perc_{min(p1['id'], p2['id'])}_{max(p1['id'], p2['id'])}"
                record_duplicate_pair(gid, "perceptual", score, primary["id"], duplicate["id"])
                perceptual_groups_count += 1

    return {
        "exact_groups": exact_groups_count,
        "perceptual_groups": perceptual_groups_count,
        "total_duplicate_photos": len(photos_in_exact_groups)
    }

