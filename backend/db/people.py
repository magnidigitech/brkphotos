from pathlib import Path
from backend.db.connection import STORAGE, get_db

def cluster_and_sync_people(event_slug="brk-sir", threshold=0.48, cancel_check=None):
    """
    High-speed vectorized leader clustering and person profile synchronization.
    Runs in seconds using NumPy matrix operations and batch SQLite inserts.
    """
    try:
        import numpy as np
    except ImportError:
        return {"status": "error", "message": "NumPy not available"}

    import json
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM events WHERE slug = ? OR ? IN ('wedding', 'default', 'brk-sir', 'brksir')", (event_slug, event_slug))
    event_row = cursor.fetchone()
    if not event_row:
        conn.close()
        return {"status": "error", "message": "Event not found"}
        
    event_id = event_row["id"]
    
    if event_slug in {"default", "wedding", "brk-sir", "brksir"}:
        data_dir = STORAGE / "data"
    else:
        data_dir = STORAGE / "data" / "events" / event_slug
        
    emb_file = data_dir / "embeddings.npy"
    names_file = data_dir / "names.json"
    
    if not emb_file.exists() or not names_file.exists():
        conn.close()
        return {"status": "ok", "people_count": 0, "message": "No embeddings found"}
        
    embs = np.load(emb_file)
    names = json.loads(names_file.read_text())
    
    if len(embs) == 0:
        conn.close()
        return {"status": "ok", "people_count": 0}
        
    # 1. Normalize embeddings for lightning-fast vectorized cosine similarity
    embs = np.array(embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    # 2. Vectorized leader clustering
    N = len(embs_norm)
    unassigned = np.ones(N, dtype=bool)
    initial_clusters = []

    iter_count = 0
    while np.any(unassigned):
        iter_count += 1
        if iter_count % 100 == 0 and cancel_check and cancel_check():
            conn.close()
            return {"status": "cancelled", "message": "Clustering cancelled by administrator"}
        unassigned_indices = np.where(unassigned)[0]
        leader_idx = unassigned_indices[0]
        sims = np.dot(embs_norm[unassigned_indices], embs_norm[leader_idx])
        matched = unassigned_indices[sims >= threshold]
        initial_clusters.append(matched.tolist())
        unassigned[matched] = False

    # 3. High-speed Pre-allocated Centroid Merging (merge clusters whose centroids are close >= 0.52)
    clusters = []
    centroids_matrix = np.empty((len(initial_clusters), 512), dtype=np.float32)
    num_centroids = 0

    for cl in initial_clusters:
        if cancel_check and cancel_check():
            conn.close()
            return {"status": "cancelled", "message": "Clustering cancelled by administrator"}
        c_emb = np.mean(embs_norm[cl], axis=0)
        norm_c = np.linalg.norm(c_emb)
        c_emb = c_emb / (norm_c if norm_c > 0 else 1.0)
        merged = False
        if num_centroids > 0:
            c_sims = np.dot(centroids_matrix[:num_centroids], c_emb)
            max_idx = int(np.argmax(c_sims))
            if c_sims[max_idx] >= 0.52:
                clusters[max_idx].extend(cl)
                new_c = np.mean(embs_norm[clusters[max_idx]], axis=0)
                norm_new = np.linalg.norm(new_c)
                centroids_matrix[max_idx] = new_c / (norm_new if norm_new > 0 else 1.0)
                merged = True
        if not merged:
            clusters.append(cl)
            centroids_matrix[num_centroids] = c_emb
            num_centroids += 1

    if cancel_check and cancel_check():
        conn.close()
        return {"status": "cancelled", "message": "Clustering cancelled by administrator"}

    # Sort clusters by number of unique photo appearances
    clusters.sort(key=lambda c: len(set(names[idx] for idx in c)), reverse=True)
    
    # 4. Preserve existing person profiles and custom names to prevent ID churn and broken avatars
    cursor.execute("SELECT id, name, avatar_thumb_path FROM person_profiles WHERE event_id = ? ORDER BY id ASC", (event_id,))
    existing_profiles = [dict(r) for r in cursor.fetchall()]

    # Clear old face_detections for clean re-linking
    cursor.execute("DELETE FROM face_detections WHERE event_id = ?", (event_id,))

    # Map photos rel_path, filename and relative subpath to photo id
    cursor.execute("SELECT id, rel_path, filename FROM photos WHERE event_id = ?", (event_id,))
    photo_map = {}
    for r in cursor.fetchall():
        photo_map[r["rel_path"]] = r["id"]
        photo_map[r["filename"]] = r["id"]
        parts = r["rel_path"].split("/")
        if len(parts) >= 2:
            photo_map["/".join(parts[1:])] = r["id"]
        if len(parts) >= 3:
            photo_map["/".join(parts[2:])] = r["id"]

    assigned_person_ids = []
    face_detections_batch = []

    for idx, cl in enumerate(clusters):
        sample_raw = names[cl[0]]
        if event_slug not in {"default", "wedding", "brk-sir", "brksir"} and not sample_raw.startswith("events/"):
            sample_rel = f"events/{event_slug}/{sample_raw}"
        else:
            sample_rel = sample_raw

        # If reusing existing profile at same rank
        if idx < len(existing_profiles):
            ep = existing_profiles[idx]
            person_id = ep["id"]
            name = ep["name"]
            avatar = ep["avatar_thumb_path"] if ep.get("avatar_thumb_path") and ep["avatar_thumb_path"].startswith("faces/") else sample_rel
            if idx == 0 and (not name or name.startswith("Person #")):
                name = "Bhashyam Ramakrishna Garu (BRK Sir)"
            cursor.execute("UPDATE person_profiles SET name = ?, avatar_thumb_path = ? WHERE id = ?", (name, avatar, person_id))
        else:
            default_name = "Bhashyam Ramakrishna Garu (BRK Sir)" if idx == 0 else f"Person #{idx + 1}"
            cursor.execute("""
                INSERT INTO person_profiles (event_id, name, avatar_thumb_path)
                VALUES (?, ?, ?)
            """, (event_id, default_name, sample_rel))
            person_id = cursor.lastrowid

        assigned_person_ids.append(person_id)

        # Batch face detections for high-speed insertion
        for face_idx in cl:
            photo_rel = names[face_idx]
            fname = Path(photo_rel).name
            p_id = photo_map.get(photo_rel) or photo_map.get(fname)
            if p_id:
                face_detections_batch.append((event_id, p_id, person_id, int(face_idx), 0.95))

    if face_detections_batch:
        cursor.executemany("""
            INSERT INTO face_detections (event_id, photo_id, person_id, embedding_offset, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, face_detections_batch)

    # Clean up excess unassigned default profiles if cluster count decreased (preserve named profiles)
    if len(existing_profiles) > len(clusters):
        excess_ids = [
            p["id"] for p in existing_profiles[len(clusters):]
            if not p["name"] or p["name"].startswith("Person #") or p["name"] == "Unnamed Guest"
        ]
        if excess_ids:
            cursor.execute(f"DELETE FROM person_profiles WHERE id IN ({','.join('?' for _ in excess_ids)})", excess_ids)

    conn.commit()
    conn.close()

    # Clustering and profile syncing completed in seconds without blocking avatar crops
    return {
        "status": "ok",
        "total_faces": len(embs),
        "people_count": len(clusters),
        "created": len(assigned_person_ids)
    }

def list_people_for_event(event_slug: str):
    """Returns all identified people for an event with thumbnail and photo count."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.avatar_thumb_path, p.is_hidden,
               COUNT(DISTINCT ph.filename) as photo_count
        FROM person_profiles p
        JOIN events e ON p.event_id = e.id
        LEFT JOIN face_detections fd ON p.id = fd.person_id
        LEFT JOIN photos ph ON fd.photo_id = ph.id AND ph.is_deleted = 0
        WHERE e.slug = ? AND p.is_hidden = 0
        GROUP BY p.id
        HAVING photo_count > 0
        ORDER BY photo_count DESC, p.id ASC
    """, (event_slug,))
    people = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return people

def get_person_photos(event_slug: str, person_id: int):
    """Returns all photos containing a specific person, deduplicated by filename, sorted latest date first."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ph.id, ph.filename, ph.rel_path, ph.drive_file_id, ph.is_featured,
               ph.captured_at, ph.created_at,
               a.name as album_name, a.slug as album_slug,
               e.name as event_name, e.slug as event_slug
        FROM photos ph
        JOIN face_detections fd ON ph.id = fd.photo_id
        JOIN events e ON ph.event_id = e.id
        LEFT JOIN albums a ON ph.album_id = a.id
        WHERE e.slug = ? AND fd.person_id = ? AND ph.is_deleted = 0 AND ph.is_hidden = 0
        GROUP BY ph.filename
        ORDER BY ph.is_featured DESC, COALESCE(ph.captured_at, ph.created_at) DESC, ph.id DESC
    """, (event_slug, person_id))
    photos = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return photos

