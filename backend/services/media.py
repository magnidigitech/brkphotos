import os
import re
import json
import requests
from pathlib import Path
from typing import Optional
from PIL import Image, ImageOps

from backend.config import PHOTOS, DATA, STORAGE, task_cancel_event
from backend.db.connection import get_db

drive_map = {}
_drive_map_mtime = 0

def reload_drive_map():
    global drive_map, _drive_map_mtime
    map_file = DATA / "drive_map.json"
    if map_file.exists():
        try:
            mtime = map_file.stat().st_mtime
            if mtime != _drive_map_mtime:
                drive_map = json.loads(map_file.read_text())
                _drive_map_mtime = mtime
        except Exception:
            pass

reload_drive_map()

def resolve_photo_path(name: str) -> Optional[Path]:
    """Fast indexed resolution of a photo file path without recursive disk scans."""
    if not name:
        return None
    fname = Path(name).name
    photos_root = PHOTOS.resolve()
    # 1. Direct path check
    try:
        target = (PHOTOS / name).resolve()
        if str(target).startswith(str(photos_root)) and target.is_file():
            return target
    except Exception:
        pass

    # 2. Fast database indexed lookup by filename or rel_path
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT rel_path FROM photos WHERE (filename = ? OR rel_path = ?) AND is_deleted = 0 LIMIT 1", (fname, name))
        row = c.fetchone()
        conn.close()
        if row and row["rel_path"]:
            p = (PHOTOS / row["rel_path"]).resolve()
            if str(p).startswith(str(photos_root)) and p.is_file():
                return p
    except Exception:
        pass

    # 3. Direct match in root photos dir
    try:
        direct = (PHOTOS / fname).resolve()
        if str(direct).startswith(str(photos_root)) and direct.is_file():
            return direct
    except Exception:
        pass
    return None

def optimize_photo_inplace(path: Path, max_dim=1920, quality=82):
    try:
        if path.stat().st_size <= 900 * 1024:
            return
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            img.save(path, "JPEG", quality=quality, optimize=True)
    except Exception:
        pass

def download_gdrive_file(url: str, dest_path: Path) -> tuple[bool, str]:
    """Downloads a single Google Drive file directly via HTTP stream with confirmation token support."""
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        resp = session.get(url, stream=True, timeout=45)
        
        # Check for virus scan confirmation token if returned
        for k, v in resp.cookies.items():
            if k.startswith("download_warning"):
                sep = "&" if "?" in url else "?"
                resp = session.get(f"{url}{sep}confirm={v}", stream=True, timeout=45)
                break
                
        # Also check html body for confirm link if returned as html
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type:
            text = resp.text[:2000]
            m = re.search(r'href="(/uc\?export=download[^"]+confirm=([^"&]+)[^"]*)"', text)
            if m:
                confirm_url = "https://drive.google.com" + m.group(1).replace("&amp;", "&")
                resp = session.get(confirm_url, stream=True, timeout=45)

        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if task_cancel_event.is_set():
                    if tmp_path.exists():
                        try: tmp_path.unlink()
                        except Exception: pass
                    return False, "Cancelled"
                if chunk:
                    f.write(chunk)

        if tmp_path.exists():
            tmp_path.replace(dest_path)
            return True, "OK"
        return False, "File missing after write"
    except Exception as e:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        return False, str(e)
