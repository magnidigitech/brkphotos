"""
BRK Photos - My Event Album Complete Feature Test Suite
Tests creation proofs, client credentials, atomic creation, retries,
recipient route /s/{token}, media delivery, creator revocation,
admin management, and security invariants.
"""
import os
import sys
import shutil
import tempfile
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import MagicMock

# Mock heavy ML dependencies before importing app
sys.modules['insightface'] = MagicMock()
sys.modules['insightface.app'] = MagicMock()

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
import auth_security
import app
from fastapi.testclient import TestClient

class TestSharedAlbumsFeature(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="brk_album_test_")
        cls.orig_db_path = database.DB_PATH
        cls.orig_storage = app.STORAGE
        cls.orig_photos = app.PHOTOS
        
        cls.db_path = Path(cls.test_dir) / "test_album.db"
        cls.storage_dir = Path(cls.test_dir) / "storage"
        cls.photos_dir = cls.storage_dir / "photos"
        cls.photos_dir.mkdir(parents=True, exist_ok=True)
        
        database.DB_PATH = cls.db_path
        app.STORAGE = cls.storage_dir
        app.PHOTOS = cls.photos_dir
        
        cls.client = TestClient(app.app)

    @classmethod
    def tearDownClass(cls):
        database.DB_PATH = cls.orig_db_path
        app.STORAGE = cls.orig_storage
        app.PHOTOS = cls.orig_photos
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        if self.db_path.exists():
            self.db_path.unlink()
        database.init_db()

        # Seed event and photos
        conn = database.get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO events (id, name, slug) VALUES (1, 'Test Event', 'test-event')")
        c.execute("INSERT OR REPLACE INTO albums (id, event_id, name, slug) VALUES (1, 1, 'Default Album', 'default')")
        
        # Create dummy photo files in storage
        for i in range(1, 6):
            rel = f"test-event/photo_{i}.jpg"
            full_path = self.photos_dir / rel
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(b"JPEG_DATA_" + str(i).encode())
            
            c.execute("""
                INSERT INTO photos (id, event_id, album_id, filename, rel_path, is_deleted)
                VALUES (?, 1, 1, ?, ?, 0)
            """, (i, f"photo_{i}.jpg", rel))
            
        conn.commit()
        conn.close()

    def _login_admin(self):
        token = "test_admin_" + secrets.token_hex(16)
        conn = database.get_db()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO admin_users (id, username, password_hash) VALUES (1, 'admin', 'hash')")
        conn.commit()
        conn.close()
        database.create_admin_session(token, user_id=1, expires_days=7)
        return token

    def test_01_create_album_end_to_end_and_retry(self):
        # 1. Generate search-result proof
        search_id = "srch_" + secrets.token_hex(8)
        _, creation_token = database.save_search_result_proof(
            search_id=search_id,
            event_id=1,
            matched_photo_ids=[1, 2, 3],
            creator_session_id="sess_123"
        )
        self.assertTrue(creation_token)

        # 2. Client creates album with 256-bit credentials
        share_tok = "sh_" + secrets.token_hex(32)
        manage_tok = "mg_" + secrets.token_hex(32)
        payload = {
            "search_id": search_id,
            "creation_token": creation_token,
            "photo_ids": [1, 2],
            "title": "My Test Album",
            "expiry_days": 30,
            "share_token": share_tok,
            "manage_token": manage_tok
        }

        res = self.client.post("/api/shared-albums", json=payload, headers={"X-Session-Id": "sess_123"})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["title"], "My Test Album")
        self.assertEqual(data["share_token"], share_tok)
        self.assertEqual(data["manage_token"], manage_tok)
        self.assertEqual(data["photo_count"], 2)
        self.assertFalse(data.get("is_retry", False))

        # 3. Retry with identical payload and credentials succeeds idempotently (200)
        res_retry = self.client.post("/api/shared-albums", json=payload, headers={"X-Session-Id": "sess_123"})
        self.assertEqual(res_retry.status_code, 200)
        data_retry = res_retry.json()
        self.assertEqual(data_retry["id"], data["id"])
        self.assertEqual(data_retry["share_token"], share_tok)
        self.assertTrue(data_retry.get("is_retry", True))

        # 4. Retry with changed payload is rejected with 409 Conflict
        changed_payload = dict(payload)
        changed_payload["photo_ids"] = [1, 3]
        res_changed = self.client.post("/api/shared-albums", json=changed_payload, headers={"X-Session-Id": "sess_123"})
        self.assertEqual(res_changed.status_code, 409)

        # 5. Attacker trying to recover album with wrong manage_token is rejected
        wrong_cred_payload = dict(payload)
        wrong_cred_payload["manage_token"] = "mg_" + secrets.token_hex(32)
        res_wrong = self.client.post("/api/shared-albums", json=wrong_cred_payload, headers={"X-Session-Id": "sess_123"})
        self.assertIn(res_wrong.status_code, (403, 409))

    def test_02_creation_unauthorized_photo_rejected(self):
        search_id = "srch_" + secrets.token_hex(8)
        _, creation_token = database.save_search_result_proof(
            search_id=search_id,
            event_id=1,
            matched_photo_ids=[1, 2],
            creator_session_id="sess_123"
        )
        
        # Photo ID 5 is valid in DB but was NOT matched in this search result
        payload = {
            "search_id": search_id,
            "creation_token": creation_token,
            "photo_ids": [1, 5],
            "title": "Exploit Album",
            "share_token": "sh_" + secrets.token_hex(32),
            "manage_token": "mg_" + secrets.token_hex(32)
        }
        res = self.client.post("/api/shared-albums", json=payload)
        self.assertEqual(res.status_code, 403)

    def test_03_recipient_route_and_api(self):
        search_id = "srch_" + secrets.token_hex(8)
        _, creation_token = database.save_search_result_proof(
            search_id=search_id,
            event_id=1,
            matched_photo_ids=[1, 2],
            creator_session_id="sess_456"
        )
        share_tok = "sh_" + secrets.token_hex(32)
        manage_tok = "mg_" + secrets.token_hex(32)
        self.client.post("/api/shared-albums", json={
            "search_id": search_id,
            "creation_token": creation_token,
            "photo_ids": [1, 2],
            "title": "Family Moments",
            "share_token": share_tok,
            "manage_token": manage_tok
        })

        # Recipient HTML page /s/{token}
        res_page = self.client.get(f"/s/{share_tok}")
        self.assertEqual(res_page.status_code, 200)
        self.assertIn("Family Moments", res_page.text)
        self.assertIn("noindex, nofollow", res_page.text)
        self.assertIn("no-store", res_page.headers.get("Cache-Control", ""))

        # Recipient JSON metadata /api/shared/{token}
        res_meta = self.client.get(f"/api/shared/{share_tok}")
        self.assertEqual(res_meta.status_code, 200)
        meta = res_meta.json()
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["album"]["title"], "Family Moments")
        # Ensure security: manage_token, token_hash, request_hash are NOT leaked
        self.assertNotIn("manage_token", meta["album"])
        self.assertNotIn("share_token_hash", meta["album"])
        self.assertNotIn("manage_token_hash", meta["album"])
        self.assertNotIn("request_hash", meta["album"])

        # Photo list endpoint
        res_photos = self.client.get(f"/api/shared/{share_tok}/photos")
        self.assertEqual(res_photos.status_code, 200)
        self.assertEqual(len(res_photos.json()["photos"]), 2)

    def test_04_creator_revocation_lifecycle(self):
        search_id = "srch_" + secrets.token_hex(8)
        _, creation_token = database.save_search_result_proof(
            search_id=search_id,
            event_id=1,
            matched_photo_ids=[1],
            creator_session_id="sess_789"
        )
        share_tok = "sh_" + secrets.token_hex(32)
        manage_tok = "mg_" + secrets.token_hex(32)
        self.client.post("/api/shared-albums", json={
            "search_id": search_id,
            "creation_token": creation_token,
            "photo_ids": [1],
            "title": "Temporary Album",
            "share_token": share_tok,
            "manage_token": manage_tok
        })

        # Revoke via manage token
        res_revoke = self.client.post(f"/api/shared-albums/manage/{manage_tok}/revoke")
        self.assertEqual(res_revoke.status_code, 200)

        # Subsequent recipient HTML request returns 410 Gone
        res_page = self.client.get(f"/s/{share_tok}")
        self.assertEqual(res_page.status_code, 410)
        self.assertIn("Album Unavailable", res_page.text)
        self.assertIn("revoked by its creator", res_page.text)

        # Subsequent metadata API returns 410
        res_api = self.client.get(f"/api/shared/{share_tok}")
        self.assertEqual(res_api.status_code, 410)

    def test_05_admin_endpoints(self):
        token = self._login_admin()
        headers = {"X-Admin-Token": token}

        # Create an album
        search_id = "srch_" + secrets.token_hex(8)
        _, creation_token = database.save_search_result_proof(
            search_id=search_id,
            event_id=1,
            matched_photo_ids=[1, 2],
            creator_session_id="sess_admin_test"
        )
        share_tok = "sh_" + secrets.token_hex(32)
        manage_tok = "mg_" + secrets.token_hex(32)
        create_res = self.client.post("/api/shared-albums", json={
            "search_id": search_id,
            "creation_token": creation_token,
            "photo_ids": [1, 2],
            "title": "Admin Inspection Album",
            "share_token": share_tok,
            "manage_token": manage_tok
        })
        album_id = create_res.json()["id"]

        # Admin lists shared albums
        res_list = self.client.get("/api/admin/shared-albums", headers=headers)
        self.assertEqual(res_list.status_code, 200)
        list_data = res_list.json()
        self.assertIn("albums", list_data)
        self.assertIn("metrics", list_data)
        self.assertGreaterEqual(list_data["metrics"]["total_albums"], 1)

        # Admin inspects photos in album without recovering tokens
        res_photos = self.client.get(f"/api/admin/shared-albums/{album_id}/photos", headers=headers)
        self.assertEqual(res_photos.status_code, 200)
        self.assertEqual(res_photos.json()["total_count"], 2)

        # Admin adjusts expiry
        res_exp = self.client.put(f"/api/admin/shared-albums/{album_id}/expiry", json={"new_expiry_days": 45}, headers=headers)
        self.assertEqual(res_exp.status_code, 200)

        # Admin revokes album
        res_rev = self.client.post(f"/api/admin/shared-albums/{album_id}/revoke", headers=headers)
        self.assertEqual(res_rev.status_code, 200)

        # Admin deletes album record (non-destructive)
        res_del = self.client.delete(f"/api/admin/shared-albums/{album_id}", headers=headers)
        self.assertEqual(res_del.status_code, 200)

        # Verify original photo records in database are strictly preserved
        conn = database.get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM photos WHERE id IN (1, 2)")
        cnt = c.fetchone()["cnt"]
        conn.close()
        self.assertEqual(cnt, 2)

        # Admin settings: update max_shared_album_photos
        res_set = self.client.post("/api/admin/settings", json={"max_shared_album_photos": 150}, headers=headers)
        self.assertEqual(res_set.status_code, 200)
        self.assertEqual(res_set.json()["updated"]["max_shared_album_photos"], "150")

if __name__ == "__main__":
    unittest.main()
