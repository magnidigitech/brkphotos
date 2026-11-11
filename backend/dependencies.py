import sys
import sqlite3
import logging
from fastapi import Request, HTTPException
from backend.config import logger
from backend.db.auth import validate_admin_session

active_tokens = set()

def get_client_ip(req: Request) -> str:
    """Safely extracts real client IP behind Coolify, Docker, Nginx, or Cloudflare proxies."""
    forwarded = req.headers.get("CF-Connecting-IP") or req.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = req.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if req.client and req.client.host:
        return req.client.host
    return "127.0.0.1"

def get_auth_token(req: Request) -> str:
    token = req.headers.get("X-Admin-Token") or req.cookies.get("admin_token")
    if not token:
        raise HTTPException(401, "Unauthorized. Authentication credentials required.")
    # 1. Validate persistent session from database (fail closed)
    try:
        app_mod = sys.modules.get("app")
        validator = getattr(app_mod, "validate_admin_session", validate_admin_session) if app_mod else validate_admin_session
        session = validator(token)
        if session:
            return token
    except sqlite3.OperationalError as op_err:
        logger.error(f"get_auth_token database schema error: {op_err}")
        raise HTTPException(503, "Authentication service temporarily unavailable. Please retry after system maintenance.")
    except Exception as e:
        logger.error(f"get_auth_token database validation error: {type(e).__name__}")
        raise HTTPException(401, "Authentication credentials invalid or session expired.")
    # 2. Backward compatibility with in-memory tokens
    app_mod = sys.modules.get("app")
    tokens_set = getattr(app_mod, "active_tokens", active_tokens) if app_mod else active_tokens
    if token in tokens_set:
        return token
    raise HTTPException(401, "Unauthorized or session expired. Please log in again.")
