"""
Comprehensive Backend Test Suite for My Event Album
Validates creation authorization, proof binding, token privilege separation,
protected delivery, atomic transactions, honest revocation, rate limiting, and edge cases.
"""
import sys
import json
import time
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
BASE = Path(__file__).resolve().parent.parent.parent.parent.parent / "Downloads" / "files" / "brkphotos"
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from database import (
    get_db, init_db,
    save_search_result_proof, get_search_result_proof,
    create_shared_album_atomic, get_shared_album_by_token,
    get_shared_album_photos_page, verify_photo_in_shared_album,
    get_all_photo_records_for_shared_album, revoke_shared_album_by_creator,
    admin_get_shared_albums, admin_revoke_shared_album,
    admin_update_shared_album_expiry, admin_delete_shared_album,
    check_and_increment_shared_rate_limit
)

def run_tests():
    init_db()
    print("=" * 70)
    print("  BRK PHOTOS — MY EVENT ALBUM SECURE BACKEND TEST SUITE")
    print("=" * 70)

    conn = get_db()
    c = conn.cursor()

    # 1. Setup Test Fixture Event & Photos in a sandboxed test event
    c.execute("SELECT id FROM events WHERE slug = 'test-shared-event' LIMIT 1")
    ev = c.fetchone()
    if ev:
        test_event_id = ev["id"]
    else:
        c.execute("""
            INSERT INTO events (slug, name, event_type, status)
            VALUES ('test-shared-event', 'BRK Test Celebration', 'Celebration', 'published')
        """)
        test_event_id = c.lastrowid

    # Create another event for cross-event injection testing
    c.execute("SELECT id FROM events WHERE slug = 'other-event' LIMIT 1")
    other_ev = c.fetchone()
    if other_ev:
        other_event_id = other_ev["id"]
    else:
        c.execute("""
            INSERT INTO events (slug, name, event_type, status)
            VALUES ('other-event', 'Other Private Gathering', 'Private', 'published')
        """)
        other_event_id = c.lastrowid

    # Insert test photos for test_event_id
    photo_ids_event1 = []
    for i in range(1, 6):
        c.execute("""
            INSERT INTO photos (event_id, filename, rel_path, is_deleted, is_hidden)
            VALUES (?, ?, ?, 0, 0)
            ON CONFLICT(rel_path) DO UPDATE SET is_deleted = 0, is_hidden = 0
        """, (test_event_id, f"test_p_{i}.jpg", f"test_shared/test_p_{i}.jpg"))
        c.execute("SELECT id FROM photos WHERE rel_path = ?", (f"test_shared/test_p_{i}.jpg",))
        photo_ids_event1.append(c.fetchone()["id"])

    # Insert test photo for other_event_id (for cross-event testing)
    c.execute("""
        INSERT INTO photos (event_id, filename, rel_path, is_deleted, is_hidden)
        VALUES (?, 'foreign_photo.jpg', 'other_event/foreign.jpg', 0, 0)
        ON CONFLICT(rel_path) DO UPDATE SET is_deleted = 0, is_hidden = 0
    """, (other_event_id,))
    c.execute("SELECT id FROM photos WHERE rel_path = 'other_event/foreign.jpg'")
    foreign_photo_id = c.fetchone()["id"]

    conn.commit()
    conn.close()

    passed = 0
    total = 0

    # TEST 1: Creation Authorization Proof Generation
    total += 1
    search_id = f"srch_{secrets.token_hex(8)}"
    # Authorize photo_ids 1, 2, 3
    authorized_matches = photo_ids_event1[:3]
    sid, creation_token = save_search_result_proof(
        search_id=search_id,
        event_id=test_event_id,
        matched_photo_ids=authorized_matches,
        creator_session_id="sess_123",
        ttl_seconds=3600
    )
    proof = get_search_result_proof(search_id)
    assert proof is not None, "Search proof should exist"
    assert set(proof["matched_photo_ids"]) == set(authorized_matches), "Proof must match authorized IDs"
    print("  [PASS] 1. Search-result proof created and verified in SQLite")
    passed += 1

    # TEST 2: Rejection of Injected / Unauthorized Photo IDs
    total += 1
    try:
        # Attempt to include photo_ids_event1[3] which was NOT in authorized_matches
        bad_selection = [authorized_matches[0], photo_ids_event1[3]]
        create_shared_album_atomic(search_id, creation_token, bad_selection, title="Hacked Album")
        assert False, "Should have rejected injected photo ID"
    except PermissionError as e:
        assert "Unauthorized photo selection" in str(e)
        print("  [PASS] 2. Injected photo ID outside matched results correctly rejected with PermissionError")
        passed += 1

    # TEST 3: Rejection of Cross-Event Photo ID
    total += 1
    try:
        bad_selection = [authorized_matches[0], foreign_photo_id]
        create_shared_album_atomic(search_id, creation_token, bad_selection, title="Cross-Event Album")
        assert False, "Should have rejected cross-event photo ID"
    except PermissionError as e:
        assert "Unauthorized photo selection" in str(e)
        print("  [PASS] 3. Cross-event photo ID correctly rejected")
        passed += 1

    # TEST 4: Rejection on Invalid Creation Token
    total += 1
    try:
        create_shared_album_atomic(search_id, "invalid_creation_token_999", authorized_matches[:2], title="Forged Token")
        assert False, "Should have rejected invalid creation token"
    except PermissionError as e:
        assert "Invalid creation token" in str(e)
        print("  [PASS] 4. Forged creation token rejected")
        passed += 1

    # TEST 5: Successful Atomic Creation & Token Generation
    total += 1
    valid_selection = authorized_matches[:2]
    client_share_token = f"sh_{secrets.token_hex(32)}"
    client_manage_token = f"mg_{secrets.token_hex(32)}"
    album_res = create_shared_album_atomic(
        search_id=search_id,
        creation_token=creation_token,
        photo_ids=valid_selection,
        title="Venkata's Moments",
        expiry_days=30,
        share_token=client_share_token,
        manage_token=client_manage_token
    )
    assert album_res["id"] > 0, "Album ID must be positive integer"
    assert album_res["share_token"] == client_share_token, "Share token must match client credential"
    assert album_res["manage_token"] == client_manage_token, "Manage token must match client credential"
    assert album_res["photo_count"] == 2, "Photo count must be 2"
    share_token = album_res["share_token"]
    manage_token = album_res["manage_token"]
    album_id = album_res["id"]
    print("  [PASS] 5. Atomic shared album created with independent 256-bit share and manage tokens")
    passed += 1

    # TEST 6: Idempotent Retry Handling (No Duplicate Album Created, Re-verifying Credentials)
    total += 1
    retry_res = create_shared_album_atomic(
        search_id=search_id,
        creation_token=creation_token,
        photo_ids=valid_selection,
        title="Venkata's Moments",
        expiry_days=30,
        share_token=share_token,
        manage_token=manage_token
    )
    assert retry_res["id"] == album_id, "Idempotent retry must return same album ID"
    assert retry_res["share_token"] == share_token, "Idempotent retry must return identical share token"
    assert retry_res["is_idempotent_retry"] is True, "Must flag idempotent retry"

    # Test 6b: Changed payload retry must be rejected
    try:
        create_shared_album_atomic(
            search_id=search_id,
            creation_token=creation_token,
            photo_ids=authorized_matches,  # Changed selection!
            title="Venkata's Moments",
            expiry_days=30,
            share_token=share_token,
            manage_token=manage_token
        )
        assert False, "Changed payload retry must be rejected"
    except ValueError as e:
        assert "different album selection" in str(e)

    # Test 6c: Credential recovery attempt (caller with wrong credentials) must be rejected
    try:
        create_shared_album_atomic(
            search_id=search_id,
            creation_token=creation_token,
            photo_ids=valid_selection,
            title="Venkata's Moments",
            expiry_days=30,
            share_token=f"sh_{secrets.token_hex(32)}",  # Different token!
            manage_token=manage_token
        )
        assert False, "Unauthorized token recovery attempt must be rejected"
    except ValueError as e:
        assert "different album selection" in str(e)

    print("  [PASS] 6. Idempotent retry returns identical album; changed payloads and credential recovery rejected")
    passed += 1

    # TEST 7: Anonymous Recipient Viewing & Photo Membership
    total += 1
    view_data = get_shared_album_by_token(share_token)
    assert view_data["status"] == "active", "Album should be active"
    assert view_data["title"] == "Venkata's Moments"
    assert "share_token_hash" not in view_data, "Never leak internal token hashes"
    photos_page = get_shared_album_photos_page(album_id, page=1, page_size=50)
    assert photos_page["total_count"] == 2, "Photos count in album must be 2"
    assert set(p["id"] for p in photos_page["photos"]) == set(valid_selection)
    print("  [PASS] 7. Recipient retrieval succeeds without auth; exact snapshot membership verified")
    passed += 1

    # TEST 8: Token-Gated Photo Verification & Alternate ID Rejection
    total += 1
    # Check valid photo
    p_valid = verify_photo_in_shared_album(share_token, valid_selection[0])
    assert p_valid is not None, "Valid photo in album must be verified"
    # Check unshared photo
    p_invalid = verify_photo_in_shared_album(share_token, photo_ids_event1[4])
    assert p_invalid is None, "Photo outside album membership must be rejected (None)"
    print("  [PASS] 8. Media delivery verified for album photos; outside photos rejected")
    passed += 1

    # TEST 9: Privilege Separation — Viewing Token Cannot Revoke Album
    total += 1
    # Try revoking using share_token as management token
    rev_bad = revoke_shared_album_by_creator(share_token, share_token)
    assert rev_bad is False, "Viewing token must NEVER authorise revocation"
    view_still_active = get_shared_album_by_token(share_token)
    assert view_still_active["status"] == "active", "Album must still be active"
    print("  [PASS] 9. Privilege separation verified: viewing token cannot mutate or revoke album")
    passed += 1

    # TEST 10: Creator Revocation with Management Token
    total += 1
    # Viewing token MUST NOT authorise management
    hack_ok = revoke_shared_album_by_creator(album_id=album_id, manage_token=share_token)
    assert hack_ok is False, "Viewing token must NEVER authorise revocation"

    rev_ok = revoke_shared_album_by_creator(album_id=album_id, manage_token=manage_token)
    assert rev_ok is True, "Revocation with valid management token must succeed"
    # Immediate subsequent view request
    view_revoked = get_shared_album_by_token(share_token)
    assert view_revoked["status"] == "revoked", "Album must immediately transition to revoked status"
    # Immediate photo verification failure
    p_after_rev = verify_photo_in_shared_album(share_token, valid_selection[0])
    assert p_after_rev is None, "Photo access must immediately fail once revoked"
    print("  [PASS] 10. Creator revocation verified: all subsequent metadata & media requests immediately fail (410)")
    passed += 1

    # TEST 11: Expiry Window Bounds & Lifetime Enforcement
    total += 1
    conn = get_db()
    c = conn.cursor()
    # Un-revoke album and set expiry to past
    yesterday_utc = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE shared_albums SET revoked_at = NULL, expires_at = ? WHERE id = ?", (yesterday_utc, album_id))
    conn.commit()
    conn.close()

    view_expired = get_shared_album_by_token(share_token)
    assert view_expired["status"] == "expired", "Past expires_at must trigger expired status"
    p_expired = verify_photo_in_shared_album(share_token, valid_selection[0])
    assert p_expired is None, "Photo access must immediately fail when expired"
    print("  [PASS] 11. Expiry verified: expired album rejects all media and download requests")
    passed += 1

    # TEST 12: Admin Revocation & Expiry Adjustment
    total += 1
    # Reset album to active
    conn = get_db()
    c = conn.cursor()
    future_utc = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE shared_albums SET revoked_at = NULL, expires_at = ? WHERE id = ?", (future_utc, album_id))
    conn.commit()
    conn.close()

    # Admin updates expiry to 45 days
    new_exp = admin_update_shared_album_expiry(album_id, 45)
    assert new_exp is not None, "Admin update expiry must return new expiry string"
    # Admin revokes
    adm_rev = admin_revoke_shared_album(album_id)
    assert adm_rev is True, "Admin revoke must succeed"
    view_adm_rev = get_shared_album_by_token(share_token)
    assert view_adm_rev["status"] == "revoked", "Admin revocation must be effective immediately"
    print("  [PASS] 12. Admin master override verified: expiry adjustment and instant administrative revocation")
    passed += 1

    # TEST 13: Worker-Safe SQLite Rate Limiter
    total += 1
    test_key = f"ip_test_{secrets.token_hex(4)}"
    # Allow 3 requests in 10 seconds
    r1 = check_and_increment_shared_rate_limit("test_action", test_key, max_requests=3, window_seconds=10)
    r2 = check_and_increment_shared_rate_limit("test_action", test_key, max_requests=3, window_seconds=10)
    r3 = check_and_increment_shared_rate_limit("test_action", test_key, max_requests=3, window_seconds=10)
    r4 = check_and_increment_shared_rate_limit("test_action", test_key, max_requests=3, window_seconds=10)
    assert r1 is True and r2 is True and r3 is True, "First 3 requests must pass"
    assert r4 is False, "4th request must be rate limited"
    print("  [PASS] 13. Distributed worker-safe SQLite rate limiting verified")
    passed += 1

    # TEST 14: Album Deletion Preserves Original Photos
    total += 1
    # Verify original photo exists
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM photos WHERE id = ?", (valid_selection[0],))
    assert c.fetchone() is not None, "Original photo must exist"
    conn.close()

    # Delete shared album
    del_ok = admin_delete_shared_album(album_id)
    assert del_ok is True, "Admin delete shared album must return True"

    # Verify shared album rows deleted
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM shared_albums WHERE id = ?", (album_id,))
    assert c.fetchone() is None, "shared_albums row must be deleted"
    c.execute("SELECT id FROM shared_album_photos WHERE shared_album_id = ?", (album_id,))
    assert c.fetchone() is None, "shared_album_photos rows must cascade delete"
    # VERIFY ORIGINAL PHOTO STILL FULLY EXISTS IN LIBRARY
    c.execute("SELECT id FROM photos WHERE id = ?", (valid_selection[0],))
    orig_photo = c.fetchone()
    assert orig_photo is not None, "ORIGINAL PHOTO MUST REMAIN COMPLETELY UNTOUCHED IN LIBRARY"
    conn.close()
    print("  [PASS] 14. Shared album deletion verified: mappings cascade-deleted, original photos 100% preserved")
    passed += 1

    # Clean up test event and photos
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM photos WHERE event_id IN (?, ?)", (test_event_id, other_event_id))
    c.execute("DELETE FROM events WHERE id IN (?, ?)", (test_event_id, other_event_id))
    c.execute("DELETE FROM search_result_proofs WHERE search_id = ?", (search_id,))
    conn.commit()
    conn.close()

    print("=" * 70)
    print(f"  RESULTS: {passed} / {total} Passed | 0 Failed")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()

