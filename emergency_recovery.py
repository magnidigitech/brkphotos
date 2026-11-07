#!/usr/bin/env python3
"""
BRK Photos — Emergency Account Recovery CLI
Authorized Server-Side Recovery Procedure for VPS / Coolify Administrators.

Usage (run directly inside Docker container or via SSH on VPS):
    python3 emergency_recovery.py
    python3 emergency_recovery.py --reset-password
    python3 emergency_recovery.py --disable-2fa
    python3 emergency_recovery.py --one-time-login
    python3 emergency_recovery.py --revoke-all-sessions
"""

import argparse
import os
import sys
import secrets
import hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import database
import auth_security

def prompt_confirm(msg: str) -> bool:
    res = input(f"{msg} (yes/no): ").strip().lower()
    return res in ("yes", "y")

def main():
    parser = argparse.ArgumentParser(description="BRK Photos Emergency Account Recovery")
    parser.add_argument("--status", action="store_true", help="Display security and authentication status")
    parser.add_argument("--reset-password", action="store_true", help="Set a new admin password")
    parser.add_argument("--disable-2fa", action="store_true", help="Disable TOTP 2FA and clear passkeys")
    parser.add_argument("--one-time-login", action="store_true", help="Generate an emergency 15-minute login token")
    parser.add_argument("--revoke-all-sessions", action="store_true", help="Invalidate all active admin sessions")
    args = parser.parse_args()

    database.init_db()
    admin = database.get_admin_user(1)
    if not admin:
        print("[-] Error: No admin user record found in database.")
        sys.exit(1)

    print("\n" + "="*65)
    print("   BRK PHOTOS — AUTHORIZED SERVER-SIDE EMERGENCY RECOVERY")
    print("="*65)
    print(f"[*] Admin Username : {admin.get('username')}")
    print(f"[*] 2FA Active     : {'YES (TOTP Authenticator)' if admin.get('totp_enabled') else 'NO'}")
    passkeys = database.get_user_passkeys(1)
    print(f"[*] Enrolled Keys  : {len(passkeys)} Passkey device(s)")
    rec_status = database.get_recovery_codes_status(1)
    print(f"[*] Recovery Codes : {rec_status['unused']} unused / {rec_status['total']} total")
    print("="*65 + "\n")

    if args.status:
        return

    # If flags passed directly
    if args.reset_password:
        return do_reset_password()
    if args.disable_2fa:
        return do_disable_2fa()
    if args.one_time_login:
        return do_one_time_login()
    if args.revoke_all_sessions:
        return do_revoke_sessions()

    # Interactive menu
    print("Select an emergency recovery action:")
    print("  [1] Generate Emergency One-Time 15-Minute Login Token")
    print("  [2] Reset Administrator Password")
    print("  [3] Disable 2FA / Clear Lost Passkeys & Generate Fresh Recovery Codes")
    print("  [4] Revoke All Active Sessions (Force Re-Authentication)")
    print("  [5] Exit\n")

    choice = input("Enter choice [1-5]: ").strip()
    if choice == "1":
        do_one_time_login()
    elif choice == "2":
        do_reset_password()
    elif choice == "3":
        do_disable_2fa()
    elif choice == "4":
        do_revoke_sessions()
    else:
        print("Recovery aborted.")

def do_one_time_login():
    raw_token = "emrg_" + secrets.token_hex(24)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    # Create 15-minute emergency session
    database.create_admin_session(token_hash, user_id=1, ip="127.0.0.1", ua="Emergency CLI", duration_days=0.0104) # ~15 mins
    database.record_security_event("emergency_recovery", ip="127.0.0.1", status="success", details="Generated 15-minute emergency login token via CLI")

    print("\n[+] EMERGENCY LOGIN TOKEN CREATED (Valid for 15 minutes):")
    print("-" * 65)
    print(f"Token: {raw_token}")
    print("-" * 65)
    print("Use this token in your browser: open admin panel, enter token or set cookie 'admin_token'.")
    print("[!] Audit trail logged to security_logs table.\n")

def do_reset_password():
    import getpass
    pw1 = getpass.getpass("Enter new admin password: ").strip()
    if len(pw1) < 8:
        print("[-] Error: Password must be at least 8 characters.")
        return
    pw2 = getpass.getpass("Confirm new admin password: ").strip()
    if pw1 != pw2:
        print("[-] Error: Passwords do not match.")
        return

    new_hash = auth_security.hash_password(pw1)
    database.update_admin_password(1, new_hash)
    database.revoke_all_admin_sessions(1)
    database.record_security_event("emergency_recovery", ip="127.0.0.1", status="success", details="Password reset via emergency recovery CLI")
    print("\n[+] SUCCESS: Administrator password updated.")
    print("[*] All prior sessions have been revoked. Log in with your new password.\n")

def do_disable_2fa():
    if not prompt_confirm("Are you sure you want to disable 2FA and regenerate recovery codes?"):
        print("Cancelled.")
        return

    # Disable TOTP
    database.set_totp_state(1, enabled=False, secret_enc="")
    # Generate 10 new emergency recovery codes
    fresh_codes = auth_security.generate_recovery_codes(10)
    hashes = [auth_security.hash_recovery_code(c) for c in fresh_codes]
    database.save_recovery_codes(1, hashes)
    database.revoke_all_admin_sessions(1)
    database.record_security_event("emergency_recovery", ip="127.0.0.1", status="success", details="2FA disabled and recovery codes regenerated via emergency CLI")

    print("\n[+] SUCCESS: 2FA has been disabled.")
    print("[*] 10 Fresh Recovery Codes generated (store these safely):")
    print("-" * 35)
    for i, c in enumerate(fresh_codes, 1):
        print(f"  {i:2d}. {c}")
    print("-" * 35)
    print("[!] Re-enable 2FA inside Admin Settings immediately after logging in.\n")

def do_revoke_sessions():
    count = database.revoke_all_admin_sessions(1)
    database.record_security_event("emergency_recovery", ip="127.0.0.1", status="success", details=f"Revoked {count} sessions via CLI")
    print(f"\n[+] SUCCESS: Revoked {count} active sessions. All devices must re-authenticate.\n")

if __name__ == "__main__":
    main()
