import secrets
import base64
import hashlib
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

import auth_security
from backend.config import ADMIN_KEY, logger
from backend.dependencies import get_client_ip, get_auth_token, active_tokens
from backend.db.connection import get_db
from backend.db.auth import (
    get_admin_user, update_admin_password, set_totp_state,
    save_recovery_codes, verify_and_burn_recovery_code, get_recovery_codes_status,
    save_passkey, get_user_passkeys, get_passkey_by_credential_id,
    update_passkey_last_used, delete_passkey, rename_passkey,
    save_auth_challenge, consume_auth_challenge,
    create_admin_session, validate_admin_session, revoke_admin_session,
    revoke_all_admin_sessions, get_active_admin_sessions,
    record_security_event, get_security_events, count_recent_failed_logins
)

router = APIRouter(tags=["admin_auth"])

@router.post("/api/admin/login")
def admin_login(payload: dict, req: Request):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    
    try:
        # 1. Rate limiting: 5 failed attempts in 15 minutes window
        fails = count_recent_failed_logins(ip, minutes=15)
        if fails >= 5:
            record_security_event("login_blocked", f"Rate limit reached ({fails} failed attempts). IP temporarily blocked.", ip=ip, user_agent=ua, status="blocked")
            raise HTTPException(429, "Too many failed login attempts. For security reasons, please wait 15 minutes before trying again.")
            
        key = (payload.get("key") or payload.get("password") or "").strip()
        username = payload.get("username", "admin").strip()
        
        import sys
        app_mod = sys.modules.get("app")
        getter = getattr(app_mod, "get_admin_user", get_admin_user) if app_mod else get_admin_user
        user = getter(username) or getter("admin")
        
        valid = False
        if user and user.get("password_hash"):
            valid = auth_security.verify_password(key, user["password_hash"])
        if not valid and key == ADMIN_KEY:
            valid = True
            
        if not valid:
            record_security_event("login_failed", f"Failed login attempt for '{username}'", ip=ip, user_agent=ua, status="failed")
            remaining = max(0, 5 - (fails + 1))
            raise HTTPException(401, f"Incorrect security key or password. {remaining} attempt(s) remaining before temporary lockout.")
            
        # Check if 2FA (TOTP) is enabled
        if user and user.get("totp_enabled") and user.get("totp_secret"):
            challenge_id = secrets.token_hex(16)
            save_auth_challenge(challenge_id, "2fa_login", "totp_pending", user_id=user["id"], expires_in_seconds=300)
            record_security_event("2fa_challenge_prompted", f"Password verified for '{user['username']}'; awaiting 2FA code", ip=ip, user_agent=ua)
            return {
                "status": "2fa_required",
                "challenge_id": challenge_id,
                "message": "Enter the 6-digit code from your authenticator app."
            }
            
        # Successful password-only login
        session_token = secrets.token_urlsafe(32)
        user_id = user["id"] if user else 1
        create_admin_session(session_token, user_id=user_id, ip_address=ip, user_agent=ua, expires_days=7)
        active_tokens.add(session_token)
        record_security_event("login_success", f"Password login successful for '{user['username'] if user else 'admin'}'", ip=ip, user_agent=ua)
        
        response = JSONResponse({"status": "ok", "token": session_token})
        response.set_cookie("admin_token", session_token, httponly=True, samesite="lax", max_age=86400 * 7)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"admin_login unexpected database exception: {type(e).__name__}")
        raise HTTPException(500, "Authentication service temporarily unavailable. Please try again.")

@router.post("/api/admin/logout")
def admin_logout(req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    active_tokens.discard(token)
    revoke_admin_session(token)
    record_security_event("logout", "Administrator logged out", ip=ip, user_agent=ua)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("admin_token")
    return response

@router.post("/api/admin/auth/2fa/verify")
def admin_auth_2fa_verify(payload: dict, req: Request):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    challenge_id = payload.get("challenge_id", "").strip()
    code = payload.get("code", "").strip()
    
    challenge = consume_auth_challenge(challenge_id, "2fa_login")
    if not challenge:
        record_security_event("2fa_challenge_expired", "2FA challenge expired or invalid", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(400, "2FA verification challenge expired. Please log in again.")
        
    user_id = challenge["user_id"] or 1
    u = get_admin_user(user_id)
    if not u or not u.get("totp_secret"):
        raise HTTPException(400, "2FA is not configured for this account.")
        
    secret = auth_security.decrypt_secret(u["totp_secret"])
    if not auth_security.verify_totp_code(secret, code, window=1):
        record_security_event("2fa_code_failed", f"Invalid 2FA code entered for user {u['username']}", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(401, "Invalid 6-digit authenticator code. Check your device clock and try again.")
        
    session_token = secrets.token_urlsafe(32)
    create_admin_session(session_token, user_id=u["id"], ip_address=ip, user_agent=ua, expires_days=7)
    active_tokens.add(session_token)
    record_security_event("2fa_login_success", f"2FA TOTP verification succeeded for {u['username']}", ip=ip, user_agent=ua)
    response = JSONResponse({"status": "ok", "token": session_token})
    response.set_cookie("admin_token", session_token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response

@router.post("/api/admin/auth/recovery-code/verify")
def admin_auth_recovery_code_verify(payload: dict, req: Request):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    challenge_id = payload.get("challenge_id", "").strip()
    recovery_code = payload.get("recovery_code", "").strip()
    
    challenge = consume_auth_challenge(challenge_id, "2fa_login")
    if not challenge:
        record_security_event("recovery_challenge_expired", "Recovery code challenge expired", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(400, "Session expired. Please enter your password again.")
        
    admin_user = get_admin_user("admin")
    if not admin_user:
        raise HTTPException(404, "Admin user not found.")
        
    success = verify_and_burn_recovery_code(admin_user["id"], recovery_code)
    if not success:
        record_security_event("recovery_code_failed", "Invalid or already used recovery code", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(401, "Invalid or already-consumed recovery code. Each code can only be used once.")
        
    session_token = secrets.token_urlsafe(32)
    create_admin_session(session_token, user_id=admin_user["id"], ip_address=ip, user_agent=ua, expires_days=7)
    active_tokens.add(session_token)
    record_security_event("recovery_code_used", f"Emergency recovery code used to authenticate {admin_user['username']}", ip=ip, user_agent=ua)
    
    status_info = get_recovery_codes_status(admin_user["id"])
    remaining = status_info["unused_count"]
    
    response = JSONResponse({
        "status": "ok",
        "token": session_token,
        "warning": f"Recovery code verified and consumed. {remaining} unused recovery code(s) remaining."
    })
    response.set_cookie("admin_token", session_token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response

@router.post("/api/admin/auth/passkey/generate-challenge")
def admin_passkey_auth_challenge(req: Request):
    ip = get_client_ip(req)
    
    if count_recent_failed_logins(ip, minutes=15) >= 5:
        raise HTTPException(429, "Too many failed login attempts. Please wait 15 minutes.")
        
    admin_user = get_admin_user("admin")
    if not admin_user:
        raise HTTPException(404, "Admin account not found.")
        
    passkeys = get_user_passkeys(admin_user["id"])
    if not passkeys:
        raise HTTPException(400, "No Passkeys registered yet on this server. Please sign in with your password first to register a passkey in Settings.")
        
    challenge_data = auth_security.generate_webauthn_challenge()
    challenge_id = challenge_data["challenge_id"]
    save_auth_challenge(challenge_id, "passkey_login", challenge_data["challenge"], user_id=admin_user["id"], expires_in_seconds=300)
    
    rp_id = req.url.hostname or "photos.bhashyamramakrishna.in"
    if rp_id in ("localhost", "127.0.0.1"):
        rp_id = req.url.hostname
        
    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "challenge": challenge_data["challenge"],
        "rpId": rp_id,
        "allowCredentials": [
            {"type": "public-key", "id": p["credential_id"], "transports": ["internal", "hybrid", "usb", "ble", "nfc"]}
            for p in passkeys
        ],
        "userVerification": "preferred",
        "timeout": 60000
    }

@router.post("/api/admin/auth/passkey/verify")
def admin_passkey_auth_verify(payload: dict, req: Request):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    
    challenge_id = payload.get("challenge_id", "").strip()
    credential_id = payload.get("credential_id", "").strip()
    client_data_json = payload.get("client_data_json", "")
    
    challenge = consume_auth_challenge(challenge_id, "passkey_login")
    if not challenge:
        record_security_event("passkey_challenge_expired", "Passkey challenge expired or invalid", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(400, "Passkey session expired. Please try again.")
        
    passkey = get_passkey_by_credential_id(credential_id)
    if not passkey:
        record_security_event("passkey_not_found", f"Unknown credential ID attempted: {credential_id[:16]}...", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(401, "Passkey device not recognized on this server.")
        
    parsed = auth_security.parse_client_data_json(client_data_json)
    if not parsed or parsed.get("type") != "webauthn.get":
        raise HTTPException(400, "Invalid WebAuthn operation type or corrupted client data.")
    
    expected_chal_raw = challenge.get("challenge") or ""
    if not expected_chal_raw:
        raise HTTPException(400, "Invalid or missing challenge in session.")
    client_chal = parsed.get("challenge", "").replace("-", "+").replace("_", "/")
    expected_chal = expected_chal_raw.replace("-", "+").replace("_", "/")
    while len(client_chal) % 4 != 0: client_chal += "="
    while len(expected_chal) % 4 != 0: expected_chal += "="
    if client_chal != expected_chal:
        record_security_event("passkey_challenge_mismatch", "WebAuthn challenge mismatch", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(401, "Passkey authentication challenge mismatch.")
        
    update_passkey_last_used(passkey["id"])
    session_token = secrets.token_urlsafe(32)
    create_admin_session(session_token, user_id=passkey["user_id"], ip_address=ip, user_agent=ua, expires_days=7)
    active_tokens.add(session_token)
    record_security_event("passkey_login_success", f"Passkey login successful via '{passkey['device_name']}'", ip=ip, user_agent=ua)
    
    response = JSONResponse({"status": "ok", "token": session_token, "device": passkey["device_name"]})
    response.set_cookie("admin_token", session_token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response

# ---------------------------------------------------------------------------
# Admin Security & Two-Factor Authentication Settings APIs
# ---------------------------------------------------------------------------

@router.get("/api/admin/security/status")
def admin_security_status_endpoint(req: Request, token: str = Depends(get_auth_token)):
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
    
    user_id = user["id"]
    totp_enabled = bool(user.get("totp_enabled"))
    recovery_status = get_recovery_codes_status(user_id)
    passkeys = get_user_passkeys(user_id)
    sessions = get_active_admin_sessions(user_id)
    
    current_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for s in sessions:
        s["is_current"] = (s.get("token_hash") == current_token_hash)
        
    return {
        "status": "ok",
        "username": user["username"],
        "totp_enabled": totp_enabled,
        "has_password": bool(user.get("password_hash")),
        "recovery_codes": recovery_status,
        "passkeys": passkeys,
        "active_sessions": sessions,
        "last_login": user.get("last_login_at")
    }

@router.post("/api/admin/security/password/update")
def admin_update_password_endpoint(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    current_pwd = payload.get("current_password", "").strip()
    new_pwd = payload.get("new_password", "").strip()
    
    if len(new_pwd) < 8:
        raise HTTPException(400, "New password must be at least 8 characters long.")
        
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    pwd_valid = auth_security.verify_password(current_pwd, user["password_hash"]) or (current_pwd == ADMIN_KEY)
    if not pwd_valid:
        record_security_event("password_change_failed", "Incorrect current password during password update", ip=ip, user_agent=ua, status="failed")
        raise HTTPException(401, "Current password is incorrect.")
        
    update_admin_password(user["id"], new_pwd)
    record_security_event("password_changed", "Admin password successfully updated", ip=ip, user_agent=ua)
    return {"status": "ok", "message": "Password updated successfully."}

@router.post("/api/admin/security/totp/setup")
def admin_totp_setup_endpoint(req: Request, token: str = Depends(get_auth_token)):
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    secret = auth_security.generate_totp_secret()
    host = req.url.hostname or "photos.bhashyamramakrishna.in"
    otpauth_uri = auth_security.generate_totp_uri(secret, username=f"admin@{host}", issuer="BRK Photos")
    qr_svg = auth_security.generate_qr_svg(otpauth_uri)
    recovery_codes = auth_security.generate_recovery_codes(10)
    
    return {
        "status": "ok",
        "secret": secret,
        "otpauth_uri": otpauth_uri,
        "qr_svg": qr_svg,
        "recovery_codes": recovery_codes
    }

@router.post("/api/admin/security/totp/enable")
def admin_totp_enable_endpoint(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    
    secret = payload.get("secret", "").strip()
    code = payload.get("code", "").strip()
    recovery_codes = payload.get("recovery_codes", [])
    
    if not secret or not code:
        raise HTTPException(400, "Secret and 6-digit code are required.")
        
    if not auth_security.verify_totp_code(secret, code, valid_window=1):
        raise HTTPException(400, "Verification code is incorrect. Please check your authenticator app and try again.")
        
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    encrypted_secret = auth_security.encrypt_secret(secret)
    set_totp_state(user["id"], True, encrypted_secret)
    
    if recovery_codes and isinstance(recovery_codes, list):
        save_recovery_codes(user["id"], recovery_codes)
        
    record_security_event("totp_enabled", "Two-step verification activated with Authenticator app", ip=ip, user_agent=ua)
    return {"status": "ok", "message": "Two-factor authentication enabled successfully!"}

@router.post("/api/admin/security/totp/disable")
def admin_totp_disable_endpoint(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    current_pwd = payload.get("password", "").strip()
    
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    pwd_valid = auth_security.verify_password(current_pwd, user["password_hash"]) or (current_pwd == ADMIN_KEY)
    if not pwd_valid:
        raise HTTPException(401, "Incorrect password. Required to disable 2FA.")
        
    set_totp_state(user["id"], False, "")
    record_security_event("totp_disabled", "Two-step verification disabled", ip=ip, user_agent=ua)
    return {"status": "ok", "message": "Two-factor authentication disabled."}

@router.post("/api/admin/security/recovery-codes/regenerate")
def admin_regenerate_recovery_codes(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    current_pwd = payload.get("password", "").strip()
    
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    pwd_valid = auth_security.verify_password(current_pwd, user["password_hash"]) or (current_pwd == ADMIN_KEY)
    if not pwd_valid:
        raise HTTPException(401, "Incorrect password. Required to generate new recovery codes.")
        
    new_codes = auth_security.generate_recovery_codes(10)
    save_recovery_codes(user["id"], new_codes)
    record_security_event("recovery_codes_regenerated", "New set of 10 recovery codes generated", ip=ip, user_agent=ua)
    return {"status": "ok", "recovery_codes": new_codes}

@router.post("/api/admin/security/passkey/register-challenge")
def admin_passkey_register_challenge(req: Request, token: str = Depends(get_auth_token)):
    user = get_admin_user("admin")
    if not user:
        raise HTTPException(404, "Admin user not found.")
        
    challenge_data = auth_security.generate_webauthn_challenge()
    challenge_id = challenge_data["challenge_id"]
    save_auth_challenge(challenge_id, "passkey_register", challenge_data["challenge"], user_id=user["id"], expires_in_seconds=300)
    
    existing = get_user_passkeys(user["id"])
    rp_id = req.url.hostname or "photos.bhashyamramakrishna.in"
    if rp_id in ("localhost", "127.0.0.1"):
        rp_id = req.url.hostname
        
    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "challenge": challenge_data["challenge"],
        "rp": {"name": "BRK Photos", "id": rp_id},
        "user": {
            "id": base64.b64encode(str(user["id"]).encode("utf-8")).decode("utf-8"),
            "name": user["username"],
            "displayName": "BRK Photos Admin"
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},   # ES256
            {"type": "public-key", "alg": -257}  # RS256
        ],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",
            "residentKey": "preferred",
            "userVerification": "preferred"
        },
        "excludeCredentials": [{"type": "public-key", "id": p["credential_id"]} for p in existing],
        "timeout": 60000
    }

@router.post("/api/admin/security/passkey/register-verify")
def admin_passkey_register_verify(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    
    challenge_id = payload.get("challenge_id", "").strip()
    credential_id = payload.get("credential_id", "").strip()
    public_key = payload.get("public_key", "").strip()
    device_name = payload.get("device_name", "Passkey Device").strip()
    client_data_json = payload.get("client_data_json", "")
    
    challenge = consume_auth_challenge(challenge_id, "passkey_register")
    if not challenge:
        raise HTTPException(400, "Registration challenge expired or invalid.")
        
    user_id = challenge.get("user_id") or 1
    parsed = auth_security.parse_client_data_json(client_data_json)
    if not parsed or parsed.get("type") != "webauthn.create":
        raise HTTPException(400, "Invalid WebAuthn operation.")
        
    save_passkey(user_id, credential_id, public_key, device_name)
    record_security_event("passkey_registered", f"New passkey enrolled: '{device_name}'", ip=ip, user_agent=ua)
    return {"status": "ok", "message": f"Passkey '{device_name}' registered successfully."}

@router.put("/api/admin/security/passkeys/{passkey_id}")
def admin_rename_passkey_endpoint(passkey_id: int, payload: dict, token: str = Depends(get_auth_token)):
    user = get_admin_user("admin")
    new_name = payload.get("device_name", "").strip()
    if not new_name:
        raise HTTPException(400, "Device name cannot be empty.")
    rename_passkey(passkey_id, user["id"], new_name)
    return {"status": "ok"}

@router.delete("/api/admin/security/passkeys/{passkey_id}")
def admin_delete_passkey_endpoint(passkey_id: int, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    user = get_admin_user("admin")
    delete_passkey(passkey_id, user["id"])
    record_security_event("passkey_deleted", f"Passkey #{passkey_id} deleted", ip=ip, user_agent=ua)
    return {"status": "ok"}

@router.get("/api/admin/security/sessions")
def admin_get_sessions_endpoint(token: str = Depends(get_auth_token)):
    user = get_admin_user("admin")
    sessions = get_active_admin_sessions(user["id"])
    current_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for s in sessions:
        s["is_current"] = (s.get("token_hash") == current_hash)
    return {"status": "ok", "sessions": sessions}

@router.post("/api/admin/security/sessions/revoke")
def admin_revoke_session_endpoint(payload: dict, req: Request, token: str = Depends(get_auth_token)):
    ip = get_client_ip(req)
    ua = req.headers.get("User-Agent", "Unknown")
    session_id = payload.get("session_id")
    revoke_all_others = payload.get("revoke_all_others", False)
    user = get_admin_user("admin")
    user_id = user["id"] if user else 1
    
    if revoke_all_others:
        current_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        revoke_all_admin_sessions(user_id=user_id, keep_token_hash=current_hash)
        record_security_event("sessions_revoked", "Revoked all other active sessions", ip=ip, user_agent=ua)
    elif session_id:
        conn = get_db()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM admin_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        finally:
            conn.close()
        record_security_event("session_revoked", f"Revoked active session #{session_id}", ip=ip, user_agent=ua)
    return {"status": "ok"}

@router.get("/api/admin/security/logs")
def admin_get_security_logs_endpoint(limit: int = 50, token: str = Depends(get_auth_token)):
    events = get_security_events(limit=limit)
    return {"status": "ok", "events": events}

# ===========================================================================
# ADMIN SHARED ALBUM APIS
# ===========================================================================

