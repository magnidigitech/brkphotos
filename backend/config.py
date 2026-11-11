import os
import sys
import re
import logging
import threading
from pathlib import Path

# Limit CPU threads to prevent memory and CPU spikes on VPS
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

logger = logging.getLogger("uvicorn.error")

def redact_token_from_string(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    text = re.sub(r"/s/[a-zA-Z0-9_\-]{16,128}", "/s/[REDACTED]", text)
    text = re.sub(r"/api/shared/[a-zA-Z0-9_\-]{16,128}", "/api/shared/[REDACTED]", text)
    text = re.sub(r"/api/shared-albums/manage/[a-zA-Z0-9_\-]{16,128}", "/api/shared-albums/manage/[REDACTED]", text)
    text = re.sub(r"mg_[a-zA-Z0-9]{32,128}", "[REDACTED_MANAGE_TOKEN]", text)
    text = re.sub(r"sh_[a-zA-Z0-9]{32,128}", "[REDACTED_SHARE_TOKEN]", text)
    text = re.sub(r"(?i)x-manage-token:\s*([^\s,]+)", "X-Manage-Token: [REDACTED]", text)
    return text

class TokenRedactingFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, "args") and isinstance(record.args, tuple):
            args_list = list(record.args)
            for idx, arg in enumerate(args_list):
                if isinstance(arg, str):
                    args_list[idx] = redact_token_from_string(arg)
            record.args = tuple(args_list)
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = redact_token_from_string(record.msg)
        return True

logging.getLogger("uvicorn.access").addFilter(TokenRedactingFilter())
logging.getLogger("uvicorn.error").addFilter(TokenRedactingFilter())

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

STORAGE = Path(os.environ.get("STORAGE_DIR", BASE / "storage"))
PHOTOS = STORAGE / "photos"
DATA = STORAGE / "data"
MODELS = STORAGE / "models"
QUEUE_DIR = STORAGE / "queue"

PHOTOS.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.40"))
MAX_UPLOAD = 10 * 1024 * 1024
ADMIN_KEY = os.environ.get("ADMIN_KEY", "Magni@3939")

# Lightweight in-memory metadata caches with TTL
_public_event_cache = {}
_public_event_cache_time = {}
_system_metrics_cache = {}
_system_metrics_cache_time = 0

def invalidate_public_event_cache(slug: str = None):
    """Invalidates cached event and album metadata when changes occur."""
    if slug:
        _public_event_cache.pop(slug, None)
        _public_event_cache_time.pop(slug, None)
    else:
        _public_event_cache.clear()
        _public_event_cache_time.clear()

# Concurrency semaphores
thumb_semaphore = threading.Semaphore(2)
zip_semaphore = threading.Semaphore(2)

# Global task state & lock
task_lock = threading.Lock()
current_task = {"status": "idle", "type": None, "message": "Ready", "event": None}
log_buffer = []
active_process = None
task_cancel_event = threading.Event()

def append_log(msg: str):
    global log_buffer
    log_buffer.append(msg)
    if len(log_buffer) > 500:
        log_buffer.pop(0)
    try:
        with open(STORAGE / "index.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

