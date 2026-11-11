import os
import sys
import sqlite3
import json
from pathlib import Path
from backend.db.connection import (
    DB_PATH, STORAGE, PHOTOS, get_db,
    parse_date_string, extract_photo_capture_date
)

def init_db():
    import backend.db.connection as conn_mod
    raw_conn = sqlite3.connect(str(conn_mod.DB_PATH), timeout=30.0)
    raw_conn.execute("PRAGMA journal_mode = WAL;")
    raw_conn.close()
    conn = get_db()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            event_type TEXT DEFAULT 'Wedding',
            event_date TEXT,
            location TEXT,
            cover_image_path TEXT,
            description TEXT,
            status TEXT DEFAULT 'published',
            passcode TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            cover_image_path TEXT,
            sort_order INTEGER DEFAULT 0,
            is_published BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_id, slug)
        );

        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
            filename TEXT NOT NULL,
            rel_path TEXT UNIQUE NOT NULL,
            drive_file_id TEXT,
            file_size_bytes INTEGER DEFAULT 0,
            width INTEGER,
            height INTEGER,
            is_featured BOOLEAN DEFAULT 0,
            is_hidden BOOLEAN DEFAULT 0,
            is_deleted BOOLEAN DEFAULT 0,
            deleted_at TIMESTAMP,
            captured_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS person_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name TEXT DEFAULT 'Unnamed Guest',
            avatar_thumb_path TEXT,
            is_hidden BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS face_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            person_id INTEGER REFERENCES person_profiles(id) ON DELETE SET NULL,
            bbox_json TEXT,
            crop_path TEXT,
            embedding_offset INTEGER,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            task_type TEXT NOT NULL,
            event_slug TEXT NOT NULL,
            target_folder TEXT,
            source_url TEXT,
            status TEXT DEFAULT 'running',
            progress_pct INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            processed_files INTEGER DEFAULT 0,
            storage_saved_mb REAL DEFAULT 0,
            current_step TEXT,
            log_text TEXT DEFAULT '',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            task_name TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'direct_upload',
            event_slug TEXT NOT NULL,
            target_album_slug TEXT NOT NULL,
            target_album_name TEXT,
            source_info TEXT,
            staged_dir TEXT,
            file_count INTEGER DEFAULT 0,
            processed_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            progress_pct INTEGER DEFAULT 0,
            current_step TEXT DEFAULT 'Waiting in queue...',
            error_message TEXT,
            log_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS automations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_slug TEXT NOT NULL DEFAULT 'brk-sir',
            gdrive_url TEXT NOT NULL,
            target_album_slug TEXT DEFAULT 'auto',
            formats_json TEXT DEFAULT '["jpg","png","webp"]',
            trigger_date TEXT NOT NULL,
            trigger_time TEXT NOT NULL,
            repeat_interval TEXT NOT NULL DEFAULT 'none',
            is_active BOOLEAN NOT NULL DEFAULT 1,
            last_triggered_at TIMESTAMP,
            last_status TEXT,
            last_task_id TEXT,
            next_trigger_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS automation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id INTEGER REFERENCES automations(id) ON DELETE CASCADE,
            automation_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'queued',
            files_count INTEGER DEFAULT 0,
            result_message TEXT,
            error_details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug);
        CREATE INDEX IF NOT EXISTS idx_albums_event ON albums(event_id);
        CREATE INDEX IF NOT EXISTS idx_photos_event_album ON photos(event_id, album_id);
        CREATE INDEX IF NOT EXISTS idx_photos_deleted ON photos(is_deleted);
        CREATE INDEX IF NOT EXISTS idx_task_history_id ON task_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_task_history_status ON task_history(status, id);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status, id);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_event ON upload_queue(event_slug, status);
        CREATE INDEX IF NOT EXISTS idx_upload_queue_task_id ON upload_queue(task_id);
        CREATE INDEX IF NOT EXISTS idx_automations_active ON automations(is_active, next_trigger_at);
        CREATE INDEX IF NOT EXISTS idx_automations_event ON automations(event_slug, is_active);
        CREATE INDEX IF NOT EXISTS idx_automation_history_task ON automation_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_automation_history_auto ON automation_history(automation_id, id);

        -- Analytics & Telemetry Layer
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visitor_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            search_id TEXT,
            photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
            photo_path TEXT,
            album_name TEXT,
            device_type TEXT DEFAULT 'desktop',
            duration_ms REAL DEFAULT 0,
            matches_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_category TEXT,
            metadata_json TEXT,
            ip_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS download_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            job_type TEXT NOT NULL DEFAULT 'zip',
            session_id TEXT,
            visitor_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            photo_count INTEGER DEFAULT 0,
            zip_size_bytes INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'processing',
            error_message TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS duplicate_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            match_type TEXT NOT NULL DEFAULT 'exact',
            similarity_score REAL DEFAULT 1.0,
            primary_photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            duplicate_photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(primary_photo_id, duplicate_photo_id)
        );

        CREATE TABLE IF NOT EXISTS match_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id TEXT,
            event_slug TEXT DEFAULT 'brk-sir',
            query_crop_path TEXT,
            matched_photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
            matched_rel_path TEXT,
            similarity_score REAL,
            confidence_tier TEXT,
            feedback TEXT DEFAULT 'pending',
            notes TEXT,
            reviewer_ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS indexing_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            event_slug TEXT,
            photo_rel_path TEXT,
            error_stage TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_dup_group_id ON duplicate_groups(group_id);
        CREATE INDEX IF NOT EXISTS idx_dup_status ON duplicate_groups(status);
        CREATE INDEX IF NOT EXISTS idx_match_reviews_feedback ON match_reviews(feedback);
        CREATE INDEX IF NOT EXISTS idx_match_reviews_tier ON match_reviews(confidence_tier);
        CREATE INDEX IF NOT EXISTS idx_indexing_errors_task ON indexing_errors(task_id);

        CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_type_created ON analytics_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_session ON analytics_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_analytics_visitor ON analytics_events(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_analytics_photo_path ON analytics_events(photo_path);
        CREATE INDEX IF NOT EXISTS idx_analytics_status ON analytics_events(status);
        CREATE INDEX IF NOT EXISTS idx_download_jobs_started ON download_jobs(started_at);
        CREATE INDEX IF NOT EXISTS idx_download_jobs_status ON download_jobs(status);

        -- Security, 2FA, Passkeys & Session Layer
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL DEFAULT 'admin',
            password_hash TEXT NOT NULL,
            totp_secret_enc TEXT,
            totp_enabled BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES admin_users(id) ON DELETE CASCADE,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_revoked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS admin_recovery_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES admin_users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL,
            is_used BOOLEAN DEFAULT 0,
            used_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_passkeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES admin_users(id) ON DELETE CASCADE,
            credential_id TEXT UNIQUE NOT NULL,
            public_key TEXT NOT NULL,
            sign_count INTEGER DEFAULT 0,
            device_name TEXT NOT NULL,
            transports TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS auth_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_type TEXT NOT NULL,
            challenge_id TEXT UNIQUE NOT NULL,
            user_id INTEGER DEFAULT 1,
            data_json TEXT,
            expires_at REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS security_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            status TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_token ON admin_sessions(token_hash);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON admin_sessions(expires_at);
        CREATE INDEX IF NOT EXISTS idx_rec_codes_user ON admin_recovery_codes(user_id, is_used);
        CREATE INDEX IF NOT EXISTS idx_passkeys_cred ON admin_passkeys(credential_id);
        CREATE INDEX IF NOT EXISTS idx_challenges_id ON auth_challenges(challenge_id);
        CREATE INDEX IF NOT EXISTS idx_sec_logs_created ON security_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_sec_logs_type ON security_logs(event_type, status);

        -- My Event Album: Private Shareable Albums & Proofs
        CREATE TABLE IF NOT EXISTS search_result_proofs (
            search_id TEXT PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            creator_session_id TEXT,
            creation_token_hash TEXT NOT NULL,
            matched_photo_ids_json TEXT NOT NULL,
            created_album_id INTEGER REFERENCES shared_albums(id) ON DELETE SET NULL,
            request_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shared_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            share_token_hash TEXT UNIQUE NOT NULL,
            manage_token_hash TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            photo_count INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0,
            max_photos_allowed INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP,
            last_viewed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shared_album_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shared_album_id INTEGER NOT NULL REFERENCES shared_albums(id) ON DELETE CASCADE,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(shared_album_id, photo_id)
        );

        CREATE TABLE IF NOT EXISTS shared_album_access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shared_album_id INTEGER NOT NULL REFERENCES shared_albums(id) ON DELETE CASCADE,
            access_type TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            user_agent_short TEXT,
            accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shared_rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_albums_token_hash ON shared_albums(share_token_hash);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_albums_manage_hash ON shared_albums(manage_token_hash);
        CREATE INDEX IF NOT EXISTS idx_shared_albums_event_id ON shared_albums(event_id);
        CREATE INDEX IF NOT EXISTS idx_shared_albums_expires_at ON shared_albums(expires_at);
        CREATE INDEX IF NOT EXISTS idx_shared_album_photos_album ON shared_album_photos(shared_album_id);
        CREATE INDEX IF NOT EXISTS idx_shared_album_photos_photo ON shared_album_photos(photo_id);
        CREATE INDEX IF NOT EXISTS idx_proofs_expires ON search_result_proofs(expires_at);
        CREATE INDEX IF NOT EXISTS idx_shared_access_logs_album ON shared_album_access_logs(shared_album_id, accessed_at);
        CREATE INDEX IF NOT EXISTS idx_shared_rate_limits ON shared_rate_limits(action, key_hash, created_at);
        """)

        # One-time schema migrations
        try:
            conn.execute("ALTER TABLE albums ADD COLUMN is_deleted BOOLEAN DEFAULT 0;")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM photos WHERE id NOT IN (SELECT MIN(id) FROM photos GROUP BY rel_path);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_event_rel ON photos(event_id, rel_path);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_rel_unique ON photos(rel_path);")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE photos ADD COLUMN captured_at TIMESTAMP;")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_captured ON photos(captured_at);")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE face_detections ADD COLUMN crop_path TEXT;")
            conn.commit()
        except Exception:
            pass

        # AI Matching & Photo Quality migrations
        for col_def in [
            "sha256_hash TEXT",
            "phash TEXT",
            "blur_score REAL",
            "exposure_score REAL",
            "quality_flags TEXT",
            "quality_grade TEXT"
        ]:
            try:
                conn.execute(f"ALTER TABLE photos ADD COLUMN {col_def};")
                conn.commit()
            except Exception:
                pass

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_quality_grade ON photos(quality_grade);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_face_detections_photo ON face_detections(photo_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_face_detections_person ON face_detections(person_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_face_detections_event ON face_detections(event_id);")
            conn.commit()
        except Exception:
            pass

        # Safe idempotent schema migrations for admin_sessions
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(admin_sessions);")
            session_cols = {col[1] for col in cur.fetchall()}
            if "is_revoked" not in session_cols:
                conn.execute("ALTER TABLE admin_sessions ADD COLUMN is_revoked INTEGER DEFAULT 0;")
                conn.commit()
        except Exception as e:
            print(f"Migration note (admin_sessions.is_revoked): {e}")

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_person_profiles_event ON person_profiles(event_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_filename ON photos(filename);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_event_deleted ON photos(event_id, is_deleted);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_album_deleted ON photos(album_id, is_deleted);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_is_featured ON photos(is_featured);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dup_primary_photo ON duplicate_groups(primary_photo_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dup_duplicate_photo ON duplicate_groups(duplicate_photo_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_match_reviews_event ON match_reviews(event_slug);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_match_reviews_matched_photo ON match_reviews(matched_photo_id);")
            conn.commit()
        except Exception as e:
            print(f"Index migration note: {e}")

        # Safe idempotent schema migrations for search_result_proofs
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(search_result_proofs);")
            proof_cols = {col[1] for col in cur.fetchall()}
            if "cached_share_token" in proof_cols or "cached_manage_token" in proof_cols:
                conn.execute("""
                    UPDATE search_result_proofs
                    SET cached_share_token = NULL, cached_manage_token = NULL
                    WHERE cached_share_token IS NOT NULL OR cached_manage_token IS NOT NULL;
                """)
                conn.commit()
            if "request_hash" not in proof_cols and proof_cols:
                conn.execute("ALTER TABLE search_result_proofs ADD COLUMN request_hash TEXT;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_proofs_request_hash ON search_result_proofs(request_hash);")
                conn.commit()
        except Exception as e:
            print(f"Migration note (search_result_proofs): {e}")

        # Default system settings
        default_settings = [
            ("match_threshold_high", "0.60"),
            ("match_threshold_medium", "0.48"),
            ("match_threshold_min", "0.40"),
            ("default_event_slug", "brk-sir"),
            ("dedup_dhash_threshold", "4"),
            ("max_shared_album_photos", "100")
        ]
        for k, v in default_settings:
            try:
                conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))
            except Exception:
                pass
        # Seed default admin user with PBKDF2 hash if not exists
        try:
            from auth_security import hash_password
            c = conn.cursor()
            c.execute("SELECT id FROM admin_users LIMIT 1")
            if not c.fetchone():
                admin_pass = os.environ.get("ADMIN_KEY") or os.environ.get("ADMIN_PASSWORD", "Magni@3939")
                hashed_pw = hash_password(admin_pass)
                c.execute("""
                    INSERT INTO admin_users (id, username, password_hash, totp_enabled)
                    VALUES (1, 'admin', ?, 0)
                """, (hashed_pw,))
                conn.commit()
        except Exception as e:
            print(f"Admin seeding note: {e}")

        conn.commit()
    conn.close()
    backfill_photo_dates()

def backfill_photo_dates():
    """Extracts, populates, and aligns captured_at for photos with consistent event dates."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT p.id, p.rel_path, p.filename, p.captured_at, p.created_at, a.name as album_name, a.slug as album_slug
            FROM photos p
            LEFT JOIN albums a ON p.album_id = a.id
            WHERE p.is_deleted = 0
        """)
        rows = c.fetchall()
        if not rows:
            conn.close()
            return 0
        updated = 0
        for r in rows:
            album_hint = r["album_name"] or r["album_slug"] or ""
            expected_date = parse_date_string(album_hint) or parse_date_string(r["rel_path"]) or parse_date_string(r["filename"])
            current_cap = (r["captured_at"] or "").strip()

            needs_update = False
            if not current_cap:
                needs_update = True
            elif expected_date and not current_cap.startswith(expected_date):
                created = (r["created_at"] or "").strip()
                if not created or current_cap.startswith(created[:10]):
                    needs_update = True

            if needs_update:
                file_p = PHOTOS / r["rel_path"] if (PHOTOS / r["rel_path"]).exists() else None
                d = extract_photo_capture_date(
                    file_path=file_p,
                    rel_path=r["rel_path"],
                    album_hint=album_hint
                )
                if d and d != current_cap:
                    c.execute("UPDATE photos SET captured_at = ? WHERE id = ?", (d, r["id"]))
                    updated += 1
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        print("backfill_photo_dates error:", e)
        conn.close()
        return 0

def migrate_existing_data():
    """Migrates existing single-event data into the BRK Photos platform structure seamlessly."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if default event exists
    cursor.execute("SELECT id, name, slug FROM events WHERE slug IN ('brk-sir', 'brksir', 'wedding', 'default')")
    row = cursor.fetchone()
    if not row:
        cursor.execute("""
            INSERT INTO events (slug, name, event_type, event_date, location, description, status)
            VALUES ('brk-sir', 'BRK Sir', 'Public Gathering', '2026-11-02', 'Andhra Pradesh', 'Official Photo Portal for Bhashyam Ramakrishna Garu, Member of Parliament, Rajya Sabha (BRK Sir)', 'published')
        """)
        event_id = cursor.lastrowid
    else:
        event_id = row["id"]
        cursor.execute("""
            UPDATE events 
            SET name = 'BRK Sir', 
                slug = 'brk-sir', 
                event_type = 'Public Gathering', 
                description = 'Official Photo Portal for Bhashyam Ramakrishna Garu, Member of Parliament, Rajya Sabha (BRK Sir)' 
            WHERE id = ?
        """, (event_id,))
        cursor.execute("UPDATE task_history SET event_slug = 'BRK Sir' WHERE event_slug IN ('wedding', 'default', 'brk-sir', 'brksir')")
        cursor.execute("UPDATE task_history SET log_text = REPLACE(REPLACE(log_text, 'wedding', 'BRK Sir'), 'The Wedding Celebration', 'Photo with BRK Sir') WHERE log_text LIKE '%wedding%'")
        conn.commit()
        
    # Check if default album exists
    cursor.execute("SELECT id, name FROM albums WHERE event_id = ? AND (slug = 'brk-photos' OR slug = 'ceremony')", (event_id,))
    album_row = cursor.fetchone()
    if not album_row:
        cursor.execute("""
            INSERT INTO albums (event_id, slug, name, sort_order, is_published)
            VALUES (?, 'brk-photos', 'BRK Photos', 1, 1)
        """, (event_id,))
        album_id = cursor.lastrowid
    else:
        album_id = album_row["id"]
        if "Wedding" in album_row["name"] or album_row["name"] == "Ceremony":
            cursor.execute("UPDATE albums SET name = 'BRK Photos', slug = 'brk-photos' WHERE id = ?", (album_id,))
            conn.commit()
        
    # Migrate any photos on disk
    photos_dir = STORAGE / "photos"
    data_dir = STORAGE / "data"
    drive_map_file = data_dir / "drive_map.json"
    drive_map = {}
    if drive_map_file.exists():
        try:
            drive_map = json.loads(drive_map_file.read_text())
        except Exception:
            pass

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    if photos_dir.exists():
        for p in photos_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in valid_exts:
                rel = str(p.relative_to(photos_dir))
                cursor.execute("SELECT id FROM photos WHERE rel_path = ?", (rel,))
                if not cursor.fetchone():
                    file_size = p.stat().st_size
                    drive_id = drive_map.get(rel)
                    cursor.execute("""
                        INSERT OR IGNORE INTO photos (event_id, album_id, filename, rel_path, drive_file_id, file_size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (event_id, album_id, p.name, rel, drive_id, file_size))
                    
    conn.commit()
    conn.close()
