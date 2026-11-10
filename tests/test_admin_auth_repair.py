"""
BRK Photos - Admin Authentication & Session Security Test Suite
Exercises real HTTP routes, real session lifecycles, migration upgrades,
fail-closed behaviors, and 2FA/passkey challenge flows against an isolated temporary database.
"""
import os
import sys
import time
import shutil
import tempfile
import sqlite3
import hashlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock heavy face processing dependencies before importing app
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

class TestAdminAuthRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="brk_auth_test_")
        cls.orig_db_path = database.DB_PATH
        cls.db_path = Path(cls.test_dir) / "test_auth.db"
        database.DB_PATH = cls.db_path
        cls.client = TestClient(app.app)
        cls.test_password = "SecureAdminTest@2026!"

    @classmethod
    def tearDownClass(cls):
        database.DB_PATH = cls.orig_db_path
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # Reset database file for each test
        if self.db_path.exists():
            self.db_path.unlink()
        database.init_db()
        # Seed test admin user with PBKDF2 hash
        pwd_hash = auth_security.hash_password(self.test_password)
        conn = database.get_db()
        try:
            with conn:
                conn.execute("DELETE FROM admin_users;")
                conn.execute("DELETE FROM admin_sessions;")
                conn.execute("DELETE FROM auth_challenges;")
                conn.execute("DELETE FROM admin_passkeys;")
                conn.execute("DELETE FROM security_logs;")
                conn.execute("""
                    INSERT INTO admin_users (id, username, password_hash, totp_enabled)
                    VALUES (1, 'admin', ?, 0)
                """, (pwd_hash,))
        finally:
            conn.close()

    # ------------------------------------------------------------------------
    # 1. Migration Tests
    # ------------------------------------------------------------------------
    def test_missing_column_upgrade(self):
        """Verifies migration adds is_revoked to an existing database lacking the column without data loss."""
        legacy_db_path = Path(self.test_dir) / "legacy.db"
        if legacy_db_path.exists():
            legacy_db_path.unlink()

        # Create legacy table without is_revoked
        conn = sqlite3.connect(str(legacy_db_path))
        conn.execute("""
            CREATE TABLE admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                totp_secret_enc TEXT,
                totp_enabled BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE admin_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Insert legacy session and user
        token_hash = hashlib.sha256(b"legacy_token").hexdigest()
        conn.execute("INSERT INTO admin_users (id, username, password_hash) VALUES (1, 'admin', 'hash_123')")
        conn.execute("INSERT INTO admin_sessions (token_hash, user_id, expires_at) VALUES (?, 1, '2099-01-01 00:00:00')", (token_hash,))
        conn.commit()
        conn.close()

        # Run init_db migration pointing to legacy_db_path
        orig = database.DB_PATH
        try:
            database.DB_PATH = legacy_db_path
            database.init_db()

            # Verify is_revoked column was added
            check_conn = sqlite3.connect(str(legacy_db_path))
            cols = [c[1] for c in check_conn.execute("PRAGMA table_info(admin_sessions)").fetchall()]
            self.assertIn("is_revoked", cols)

            # Verify legacy data preserved
            user_row = check_conn.execute("SELECT username, password_hash FROM admin_users WHERE id = 1").fetchone()
            self.assertEqual(user_row[0], "admin")
            self.assertEqual(user_row[1], "hash_123")

            session_row = check_conn.execute("SELECT token_hash, is_revoked FROM admin_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
            self.assertIsNotNone(session_row)
            self.assertEqual(session_row[1], 0)
            check_conn.close()
        finally:
            database.DB_PATH = orig
            if legacy_db_path.exists():
                legacy_db_path.unlink()

    def test_fresh_database_creation(self):
        """Verifies a brand new database immediately creates admin_sessions with is_revoked column."""
        fresh_db_path = Path(self.test_dir) / "fresh.db"
        if fresh_db_path.exists():
            fresh_db_path.unlink()

        orig = database.DB_PATH
        try:
            database.DB_PATH = fresh_db_path
            database.init_db()

            conn = sqlite3.connect(str(fresh_db_path))
            cols = [c[1] for c in conn.execute("PRAGMA table_info(admin_sessions)").fetchall()]
            self.assertIn("is_revoked", cols)
            conn.close()
        finally:
            database.DB_PATH = orig
            if fresh_db_path.exists():
                fresh_db_path.unlink()

    # ------------------------------------------------------------------------
    # 2. Login & Password Verification
    # ------------------------------------------------------------------------
    def test_login_incorrect_password(self):
        """Incorrect password returns 401 with remaining attempts count and generic JSON error."""
        res = self.client.post("/api/admin/login", json={"key": "WrongPassword123!"})
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertIn("detail", data)
        self.assertIn("remaining before temporary lockout", data["detail"])

    def test_login_correct_password(self):
        """Correct password returns 200 with session token and sets admin_token cookie."""
        res = self.client.post("/api/admin/login", json={"key": self.test_password})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("token", data)
        self.assertIn("admin_token", res.cookies)

    # ------------------------------------------------------------------------
    # 3. Two-Factor Authentication (TOTP) Challenge Flow
    # ------------------------------------------------------------------------
    def test_2fa_challenge_flow(self):
        """When 2FA is active, login prompts 2FA challenge and requires valid TOTP code."""
        totp_secret = auth_security.generate_totp_secret()
        enc_secret = auth_security.encrypt_secret(totp_secret)
        database.set_totp_state(1, enabled=True, secret_enc=enc_secret)

        # 1. Login with password -> returns 2fa_required
        res = self.client.post("/api/admin/login", json={"key": self.test_password})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "2fa_required")
        challenge_id = data["challenge_id"]

        # 2. Verify with wrong code -> returns 401
        res_bad = self.client.post("/api/admin/auth/2fa/verify", json={
            "challenge_id": challenge_id,
            "code": "000000"
        })
        self.assertEqual(res_bad.status_code, 401)

        # 3. Generate fresh challenge and verify with valid code
        res_login2 = self.client.post("/api/admin/login", json={"key": self.test_password})
        new_challenge_id = res_login2.json()["challenge_id"]
        valid_code = auth_security.generate_totp_code(totp_secret)

        res_good = self.client.post("/api/admin/auth/2fa/verify", json={
            "challenge_id": new_challenge_id,
            "code": valid_code
        })
        self.assertEqual(res_good.status_code, 200)
        good_data = res_good.json()
        self.assertEqual(good_data["status"], "ok")
        self.assertIn("token", good_data)

    # ------------------------------------------------------------------------
    # 4. Session Validity: Valid, Expired, and Revoked
    # ------------------------------------------------------------------------
    def test_valid_session_access(self):
        """Active session accesses protected endpoints with 200."""
        token = "valid_test_token_123"
        database.create_admin_session(token, user_id=1, expires_days=7)

        res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_expired_session_rejected(self):
        """Expired session is rejected with 401 fail-closed."""
        token = "expired_test_token_456"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = database.get_db()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO admin_sessions (token_hash, user_id, expires_at, is_revoked)
                    VALUES (?, 1, datetime('now', '-1 hour'), 0)
                """, (token_hash,))
        finally:
            conn.close()

        res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token})
        self.assertEqual(res.status_code, 401)

    def test_revoked_session_rejected(self):
        """Session marked as revoked (is_revoked = 1) is rejected with 401 fail-closed."""
        token = "revoked_test_token_789"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = database.get_db()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO admin_sessions (token_hash, user_id, expires_at, is_revoked)
                    VALUES (?, 1, datetime('now', '+1 hour'), 1)
                """, (token_hash,))
        finally:
            conn.close()

        res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token})
        self.assertEqual(res.status_code, 401)

    # ------------------------------------------------------------------------
    # 5. Session Listing and Revocation Endpoints
    # ------------------------------------------------------------------------
    def test_session_listing_and_revocation(self):
        """Session listing correctly identifies is_current and revokes other sessions."""
        token1 = "caller_token_alpha"
        token2 = "other_token_beta"
        database.create_admin_session(token1, user_id=1, expires_days=7)
        database.create_admin_session(token2, user_id=1, expires_days=7)

        # List sessions
        res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token1})
        self.assertEqual(res.status_code, 200)
        sessions = res.json()["sessions"]
        self.assertGreaterEqual(len(sessions), 2)

        # Verify is_current identification
        current_session = next(s for s in sessions if s["is_current"])
        other_session = next(s for s in sessions if not s["is_current"])
        self.assertTrue(current_session["is_current"])
        self.assertFalse(other_session["is_current"])

        # Revoke all other sessions
        res_revoke = self.client.post(
            "/api/admin/security/sessions/revoke",
            headers={"X-Admin-Token": token1},
            json={"revoke_all_others": True}
        )
        self.assertEqual(res_revoke.status_code, 200)

        # Other token should now be rejected (revoked / deleted)
        res_other = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token2})
        self.assertEqual(res_other.status_code, 401)

        # Caller token must still work
        res_caller = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token1})
        self.assertEqual(res_caller.status_code, 200)

    # ------------------------------------------------------------------------
    # 6. Passkey Challenges
    # ------------------------------------------------------------------------
    def test_passkey_registration_and_auth_challenges(self):
        """Passkey registration options and authentication challenge generation return structured WebAuthn data."""
        token = "passkey_admin_token"
        database.create_admin_session(token, user_id=1, expires_days=7)

        # 1. Registration challenge
        res_reg = self.client.post("/api/admin/security/passkey/register-challenge", headers={"X-Admin-Token": token})
        self.assertEqual(res_reg.status_code, 200)
        reg_data = res_reg.json()
        self.assertEqual(reg_data["status"], "ok")
        self.assertIn("challenge", reg_data)
        self.assertIn("rp", reg_data)
        self.assertIn("user", reg_data)

        # 2. Register mock passkey directly
        database.save_passkey(1, "mock_cred_id_123", "mock_public_key", "MacBook Pro Touch ID")

        # 3. Generate auth challenge (unauthenticated login endpoint)
        res_auth = self.client.post("/api/admin/auth/passkey/generate-challenge")
        self.assertEqual(res_auth.status_code, 200)
        auth_data = res_auth.json()
        self.assertEqual(auth_data["status"], "ok")
        self.assertIn("challenge", auth_data)
        self.assertTrue(any(c["id"] == "mock_cred_id_123" for c in auth_data["allowCredentials"]))

    def test_repeated_and_concurrent_migration(self):
        """Verifies repeated and concurrent init_db runs safely without lock errors, preserving data and revocation state."""
        import concurrent.futures

        # Seed initial session and a revoked session
        token_active = "active_token_concurrent"
        token_revoked = "revoked_token_concurrent"
        database.create_admin_session(token_active, user_id=1, expires_days=7)
        database.create_admin_session(token_revoked, user_id=1, expires_days=7)

        # Mark token_revoked as revoked
        conn = database.get_db()
        try:
            with conn:
                conn.execute(
                    "UPDATE admin_sessions SET is_revoked = 1 WHERE token_hash = ?",
                    (hashlib.sha256(token_revoked.encode()).hexdigest(),)
                )
        finally:
            conn.close()

        # Run init_db concurrently across 5 threads
        errors = []
        def run_init():
            try:
                database.init_db()
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(run_init) for _ in range(10)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Concurrent init_db errors: {errors}")

        # Verify active session still active
        self.assertIsNotNone(database.validate_admin_session(token_active))
        # Verify revoked session still revoked
        self.assertIsNone(database.validate_admin_session(token_revoked))

    def test_unmigrated_schema_fails_closed_with_503(self):
        """When an unmigrated database missing is_revoked is accessed, validation fails closed with 503."""
        unmigrated_path = Path(self.test_dir) / "unmigrated.db"
        if unmigrated_path.exists():
            unmigrated_path.unlink()

        conn = sqlite3.connect(str(unmigrated_path))
        conn.execute("""
            CREATE TABLE admin_users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                password_hash TEXT,
                totp_secret_enc TEXT,
                totp_enabled BOOLEAN DEFAULT 0
            );
        """)
        conn.execute("""
            CREATE TABLE admin_sessions (
                id INTEGER PRIMARY KEY,
                token_hash TEXT UNIQUE,
                user_id INTEGER,
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        token = "unmigrated_session_token"
        t_hash = hashlib.sha256(token.encode()).hexdigest()
        conn.execute("INSERT INTO admin_users (id, username, password_hash) VALUES (1, 'admin', 'hash')")
        conn.execute("INSERT INTO admin_sessions (token_hash, user_id, expires_at) VALUES (?, 1, '2099-01-01 00:00:00')", (t_hash,))
        conn.commit()
        conn.close()

        orig = database.DB_PATH
        try:
            database.DB_PATH = unmigrated_path
            app.active_tokens.discard(token)

            # Route should reject with 503 Service Unavailable, NEVER grant access
            res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token})
            self.assertEqual(res.status_code, 503)
            self.assertIn("detail", res.json())
            self.assertIn("temporarily unavailable", res.json()["detail"].lower())
        finally:
            database.DB_PATH = orig
            if unmigrated_path.exists():
                unmigrated_path.unlink()

    def test_malformed_passkey_registration(self):
        """Malformed passkey payloads return structured 400 errors and never crash with 500."""
        token = "passkey_malformed_test_token"
        database.create_admin_session(token, user_id=1, expires_days=7)

        # 1. Nonexistent challenge ID
        res1 = self.client.post("/api/admin/security/passkey/register-verify", headers={"X-Admin-Token": token}, json={
            "challenge_id": "non_existent_id",
            "credential_id": "cred_123",
            "public_key": "key_123",
            "device_name": "Test Device",
            "client_data_json": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIn0="
        })
        self.assertEqual(res1.status_code, 400)
        self.assertIn("expired or invalid", res1.json()["detail"])

        # Generate a real challenge
        res_chal = self.client.post("/api/admin/security/passkey/register-challenge", headers={"X-Admin-Token": token})
        chal_id = res_chal.json()["challenge_id"]

        # 2. Corrupted client_data_json (invalid base64)
        res2 = self.client.post("/api/admin/security/passkey/register-verify", headers={"X-Admin-Token": token}, json={
            "challenge_id": chal_id,
            "credential_id": "cred_123",
            "public_key": "key_123",
            "device_name": "Test Device",
            "client_data_json": "!!!NOT_VALID_BASE64!!!"
        })
        self.assertEqual(res2.status_code, 400)
        self.assertIn("detail", res2.json())

        # Generate fresh challenge
        res_chal2 = self.client.post("/api/admin/security/passkey/register-challenge", headers={"X-Admin-Token": token})
        chal_id2 = res_chal2.json()["challenge_id"]

        # 3. Invalid operation type (webauthn.get sent to register-verify)
        import base64, json as pyjson
        wrong_type_b64 = base64.urlsafe_b64encode(pyjson.dumps({"type": "webauthn.get", "challenge": "xyz"}).encode()).decode()
        res3 = self.client.post("/api/admin/security/passkey/register-verify", headers={"X-Admin-Token": token}, json={
            "challenge_id": chal_id2,
            "credential_id": "cred_123",
            "public_key": "key_123",
            "device_name": "Test Device",
            "client_data_json": wrong_type_b64
        })
        self.assertEqual(res3.status_code, 400)
        self.assertIn("Invalid WebAuthn operation", res3.json()["detail"])

    # ------------------------------------------------------------------------
    # 7. Fail-Closed On Database Error
    # ------------------------------------------------------------------------
    def test_fail_closed_on_database_error(self):
        """Database errors during token validation or login fail closed and never grant access."""
        token = "valid_token_for_db_error_test"
        database.create_admin_session(token, user_id=1, expires_days=7)
        app.active_tokens.discard(token)

        # Simulate database failure during token validation
        with patch("app.validate_admin_session", side_effect=sqlite3.DatabaseError("Disk I/O error")):
            res = self.client.get("/api/admin/security/sessions", headers={"X-Admin-Token": token})
            # MUST fail closed (401), NEVER allow access
            self.assertEqual(res.status_code, 401)
            self.assertIn("detail", res.json())

        # Simulate database failure during login
        with patch("app.get_admin_user", side_effect=sqlite3.DatabaseError("Database locked")):
            res_login = self.client.post("/api/admin/login", json={"key": self.test_password})
            # MUST return structured JSON 500 error, never bypass or crash with raw unformatted traceback
            self.assertEqual(res_login.status_code, 500)
            data = res_login.json()
            self.assertIn("detail", data)
            self.assertEqual(data["detail"], "Authentication service temporarily unavailable. Please try again.")

if __name__ == "__main__":
    unittest.main()
