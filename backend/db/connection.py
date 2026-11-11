import sqlite3
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
DATA_DIR = STORAGE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS = STORAGE / "photos"
DB_PATH = DATA_DIR / "magniwedding.db"

IST = timezone(timedelta(hours=5, minutes=30))

def set_db_path(path: Union[str, Path]):
    global DB_PATH
    DB_PATH = Path(path)

def now_ist() -> datetime:
    return datetime.now(IST)

def parse_date_string(text: str) -> Optional[str]:
    """Extracts an ISO date YYYY-MM-DD from various folder, album, or filename strings."""
    if not text:
        return None
    # 1. Matches DD-MM-YYYY or DD-MM-YY (e.g. 29-10-2026, 29-10-26, 01-10-26, 28_10_2026)
    m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[-/._](0?[1-9]|1[0-2])[-/._](20\d\d|\d\d)\b', text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            datetime(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # 2. Matches YYYY-MM-DD (e.g. 2026-10-29, 2026_10_29)
    m = re.search(r'\b(20\d\d)[-/._](0?[1-9]|1[0-2])[-/._](0?[1-9]|[12][0-9]|3[01])\b', text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            datetime(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # 3. Matches YYYYMMDD in phone/camera filenames (e.g. IMG_20261028_123456, PXL_20261028)
    m = re.search(r'(?:IMG_|PXL_|VID_|DSC_|PHOTO_)?(20\d\d)(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])', text, re.IGNORECASE)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            datetime(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # 4. Matches DD Month YYYY or Month DD YYYY
    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
              'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
              'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12}
    m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?[\s_-]+([a-zA-Z]{3,9})(?:[\s_,-]+(20\d\d|\d\d))?\b', text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        mon_str = m.group(2).lower()
        if mon_str in months:
            month = months[mon_str]
            yr = int(m.group(3)) if m.group(3) else 2026
            if yr < 100:
                yr += 2000
            try:
                datetime(yr, month, day)
                return f"{yr:04d}-{month:02d}-{day:02d}"
            except ValueError:
                pass

    m2 = re.search(r'\b([a-zA-Z]{3,9})[\s_-]+(0?[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?(?:[\s_,-]+(20\d\d|\d\d))?\b', text, re.IGNORECASE)
    if m2:
        mon_str = m2.group(1).lower()
        day = int(m2.group(2))
        if mon_str in months:
            month = months[mon_str]
            yr = int(m2.group(3)) if m2.group(3) else 2026
            if yr < 100:
                yr += 2000
            try:
                datetime(yr, month, day)
                return f"{yr:04d}-{month:02d}-{day:02d}"
            except ValueError:
                pass

    return None

def extract_photo_capture_date(file_path: Optional[Path] = None, rel_path: str = "", album_hint: str = "") -> str:
    """
    Extracts the most accurate ISO timestamp (YYYY-MM-DD HH:MM:SS) for a photograph:
    1. EXIF DateTimeOriginal / DateTimeDigitized from file (including Exif IFD 0x8769)
    2. Reliable date in album name or slug (e.g. 28-10-2026, 28 Oct 2026)
    3. Date in rel_path or filename (e.g. 28-10-2026/DSC_001.jpg, IMG_20261028_123456.jpg)
    4. Fallback to file creation timestamp or now
    """
    # 1. EXIF metadata
    if file_path and file_path.exists():
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                exif = img.getexif()
                if exif:
                    sub_ifd = {}
                    try:
                        sub_ifd = exif.get_ifd(0x8769)
                    except Exception:
                        pass
                    for tag_id in (36867, 36868, 306):
                        val = sub_ifd.get(tag_id) or exif.get(tag_id)
                        if val and isinstance(val, str) and len(val) >= 10:
                            parts = val.strip().split()
                            date_part = parts[0].replace(":", "-")
                            time_part = parts[1] if len(parts) > 1 else "12:00:00"
                            try:
                                y, m, d = [int(v) for v in date_part.split("-")[:3]]
                                if 1990 <= y <= 2035 and 1 <= m <= 12 and 1 <= d <= 31:
                                    return f"{y:04d}-{m:02d}-{d:02d} {time_part}"
                            except Exception:
                                pass
        except Exception:
            pass

    # 2. Date in album hint
    d = parse_date_string(album_hint)
    if d:
        return f"{d} 12:00:00"

    # 3. Date in rel_path or filename
    d = parse_date_string(rel_path)
    if d:
        return f"{d} 12:00:00"

    # 4. Fallback to mtime or current time
    if file_path and file_path.exists():
        try:
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            return mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return now_ist().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    import backend.db.connection as conn_mod
    conn = sqlite3.connect(str(conn_mod.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA cache_size = -64000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
