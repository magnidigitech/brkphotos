from backend.db.connection import get_db

def record_match_review(
    search_id: str,
    event_slug: str,
    query_crop_path: str,
    matched_photo_id: int,
    matched_rel_path: str,
    similarity_score: float,
    confidence_tier: str
):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO match_reviews (
                search_id, event_slug, query_crop_path, matched_photo_id,
                matched_rel_path, similarity_score, confidence_tier, feedback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (search_id, event_slug, query_crop_path, matched_photo_id, matched_rel_path, round(float(similarity_score), 4), confidence_tier))
        conn.commit()
    except Exception as e:
        print(f"Error recording match review: {e}")
    finally:
        conn.close()

def get_match_reviews(event_slug: str = "brk-sir", status: str = None, tier: str = None, limit: int = 100) -> list:
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT mr.*, p.filename, p.width, p.height, p.file_size_bytes, a.name as album_name
        FROM match_reviews mr
        LEFT JOIN photos p ON mr.matched_photo_id = p.id
        LEFT JOIN albums a ON p.album_id = a.id
        WHERE (mr.event_slug = ? OR ? = '' OR mr.event_slug IS NULL)
    """
    params = [event_slug, event_slug]
    if status and status != "all":
        query += " AND mr.feedback = ?"
        params.append(status)
    if tier and tier != "all":
        query += " AND mr.confidence_tier = ?"
        params.append(tier)
    query += " ORDER BY mr.id DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def update_match_feedback(review_id: int, feedback: str, notes: str = None, reviewer_ip: str = None) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE match_reviews
        SET feedback = ?, notes = COALESCE(?, notes), reviewer_ip = COALESCE(?, reviewer_ip)
        WHERE id = ?
    """, (feedback, notes, reviewer_ip, review_id))
    rows_affected = c.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

def record_duplicate_pair(group_id: str, match_type: str, similarity_score: float, primary_photo_id: int, duplicate_photo_id: int):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR REPLACE INTO duplicate_groups (
                group_id, match_type, similarity_score, primary_photo_id, duplicate_photo_id, status
            ) VALUES (?, ?, ?, ?, ?, 'pending')
        """, (group_id, match_type, round(float(similarity_score), 4), primary_photo_id, duplicate_photo_id))
        conn.commit()
    except Exception as e:
        print(f"Error recording duplicate pair: {e}")
    finally:
        conn.close()

def get_duplicate_groups(event_slug: str = "brk-sir", status: str = "pending") -> list:
    conn = get_db()
    c = conn.cursor()
    status_filter = "AND dg.status = ?" if status != "all" else ""
    params = [event_slug, event_slug]
    if status != "all":
        params.append(status)

    c.execute(f"""
        SELECT DISTINCT dg.group_id, dg.match_type, dg.similarity_score, dg.status, dg.created_at
        FROM duplicate_groups dg
        JOIN photos p1 ON dg.primary_photo_id = p1.id
        JOIN events e ON p1.event_id = e.id
        WHERE (e.slug = ? OR ? = '') {status_filter}
        ORDER BY dg.id DESC
    """, params)
    group_rows = c.fetchall()

    result = []
    for gr in group_rows:
        gid = gr["group_id"]
        c.execute("""
            SELECT primary_photo_id, duplicate_photo_id, similarity_score, match_type
            FROM duplicate_groups
            WHERE group_id = ?
        """, (gid,))
        pairs = c.fetchall()
        photo_ids = set()
        for p in pairs:
            photo_ids.add(p["primary_photo_id"])
            photo_ids.add(p["duplicate_photo_id"])

        if not photo_ids:
            continue

        placeholders = ",".join("?" for _ in photo_ids)
        c.execute(f"""
            SELECT p.id, p.filename, p.rel_path, p.width, p.height, p.file_size_bytes,
                   p.blur_score, p.exposure_score, p.quality_grade, p.quality_flags,
                   p.captured_at, p.created_at, p.is_deleted,
                   a.name as album_name, a.slug as album_slug
            FROM photos p
            LEFT JOIN albums a ON p.album_id = a.id
            WHERE p.id IN ({placeholders}) AND p.is_deleted = 0
        """, list(photo_ids))
        photos = [dict(pr) for pr in c.fetchall()]
        if len(photos) < 2:
            continue

        def quality_rank(item):
            w = item.get("width") or 0
            h = item.get("height") or 0
            sz = item.get("file_size_bytes") or 0
            grade_weight = 0 if item.get("quality_grade") == "good" else (1 if item.get("quality_grade") == "acceptable" else 2)
            return (-(w * h), -sz, grade_weight)

        photos.sort(key=quality_rank)
        rec_id = photos[0]["id"]
        for p in photos:
            p["is_recommended"] = (p["id"] == rec_id)
            p["recommended_original"] = (p["id"] == rec_id)

        result.append({
            "group_id": gid,
            "match_type": gr["match_type"],
            "similarity_score": round(float(gr["similarity_score"]), 3),
            "status": gr["status"],
            "created_at": gr["created_at"],
            "recommended_photo_id": rec_id,
            "photos": photos
        })

    conn.close()
    return result

def resolve_duplicate_group(group_id: str, action: str, photo_ids: list = None) -> dict:
    conn = get_db()
    c = conn.cursor()
    if action == "ignore":
        c.execute("UPDATE duplicate_groups SET status = 'ignored' WHERE group_id = ?", (group_id,))
        conn.commit()
        conn.close()
        return {"status": "ok", "message": "Duplicate group ignored."}

    if action in ("delete_duplicates", "delete_selected"):
        if not photo_ids:
            c.execute("SELECT duplicate_photo_id FROM duplicate_groups WHERE group_id = ?", (group_id,))
            photo_ids = [r["duplicate_photo_id"] for r in c.fetchall()]

        if photo_ids:
            placeholders = ",".join("?" for _ in photo_ids)
            c.execute(f"""
                UPDATE photos
                SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
            """, photo_ids)
        c.execute("UPDATE duplicate_groups SET status = 'resolved' WHERE group_id = ?", (group_id,))
        conn.commit()
        conn.close()
        return {"status": "ok", "deleted_count": len(photo_ids or []), "message": f"{len(photo_ids or [])} duplicate(s) moved to Trash."}

    conn.close()
    return {"status": "error", "message": "Unknown action."}
