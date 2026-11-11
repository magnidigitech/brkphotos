import os
import sys
import types
from pathlib import Path

# Limit CPU threads to prevent memory and CPU spikes on VPS
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import backend.config as config
from backend.config import (
    STORAGE, PHOTOS, DATA, MODELS, QUEUE_DIR, THRESHOLD, MAX_UPLOAD, ADMIN_KEY,
    logger, redact_token_from_string, TokenRedactingFilter,
    current_task, task_lock, log_buffer, task_cancel_event, append_log,
    thumb_semaphore, zip_semaphore, invalidate_public_event_cache
)
import backend.dependencies as dependencies
from backend.dependencies import get_client_ip, get_auth_token, active_tokens
from backend.middleware import add_security_and_cache_headers

from backend.db import (
    get_db, init_db, migrate_existing_data,
    cluster_and_sync_people, list_people_for_event, get_person_photos,
    create_task_record, update_task_progress, get_latest_task, list_task_history, get_task_by_id,
    stop_running_tasks,
    enqueue_upload_task, claim_next_queued_task, update_queue_task,
    get_queue_tasks, get_queue_task_by_id, get_queue_task_logs, cancel_queue_task, retry_queue_task,
    clear_completed_queue_tasks, get_queue_stats, reset_stale_running_queue_tasks,
    create_automation, get_automations, get_automation_by_id, update_automation,
    delete_automation, toggle_automation, record_automation_history, get_automation_history,
    get_due_automations, advance_automation_schedule, is_automation_task_pending, now_ist, extract_photo_capture_date,
    parse_date_string,
    get_setting, set_setting, get_all_settings,
    record_indexing_error, get_indexing_errors,
    record_match_review, get_match_reviews, update_match_feedback,
    record_duplicate_pair, get_duplicate_groups, resolve_duplicate_group,
    get_admin_user, update_admin_password, set_totp_state,
    save_recovery_codes, verify_and_burn_recovery_code, get_recovery_codes_status,
    save_passkey, get_user_passkeys, get_passkey_by_credential_id,
    update_passkey_last_used, delete_passkey, rename_passkey,
    save_auth_challenge, consume_auth_challenge,
    create_admin_session, validate_admin_session, revoke_admin_session,
    revoke_all_admin_sessions, get_active_admin_sessions,
    record_security_event, get_security_events, count_recent_failed_logins,
    save_search_result_proof, get_search_result_proof, create_shared_album_atomic,
    get_shared_album_by_token, get_shared_album_photos_page, verify_photo_in_shared_album,
    get_all_photo_records_for_shared_album, revoke_shared_album_by_creator,
    admin_get_shared_albums, admin_get_shared_album_photos, admin_revoke_shared_album, admin_update_shared_album_expiry,
    admin_delete_shared_album, check_and_increment_shared_rate_limit, record_shared_album_access_log
)

from backend.services.media import (
    resolve_photo_path, optimize_photo_inplace, download_gdrive_file,
    reload_drive_map, drive_map
)
from backend.services.face_search import (
    crop_face_thumb, store_search_session, get_search_session,
    normalize_base_stem, phash_hamming, parse_photo_date_info,
    aggregate_photo_dates, query_matched_photos, perform_face_search,
    get_event_index, get_face_app
)
from backend.services.indexing import (
    run_indexing_task, run_reclustering_task, sync_photos_to_database,
    sync_event_folders_and_photos
)
from backend.services.uploads import (
    run_gdrive_download, execute_queued_job
)
from backend.services.scheduler import (
    trigger_queue_worker, start_background_workers
)

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.routers import (
    search, media, gallery, shared_albums, admin_auth, admin_events,
    admin_photos, admin_uploads, admin_people, admin_analytics,
    admin_settings, pages
)

# Initialize database on startup
init_db()
migrate_existing_data()
reset_stale_running_queue_tasks()
start_background_workers()

app = FastAPI(title="BRK Photos")

# Middleware for security and cache headers
@app.middleware("http")
async def security_and_cache_middleware(request: Request, call_next):
    return await add_security_and_cache_headers(request, call_next)

# Include modular routers in order
app.include_router(search.router)
app.include_router(media.router)
app.include_router(gallery.router)
app.include_router(shared_albums.router)
app.include_router(admin_auth.router)
app.include_router(admin_events.router)
app.include_router(admin_photos.router)
app.include_router(admin_uploads.router)
app.include_router(admin_people.router)
app.include_router(admin_analytics.router)
app.include_router(admin_settings.router)
app.include_router(pages.router)

# Static assets
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static_dir")
app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")


class _AppFacadeModule(types.ModuleType):
    """
    Transparent backwards-compatible module wrapper for `app.py`.
    Ensures tests and scripts modifying `app.STORAGE`, `app.PHOTOS`, or patching
    `app.validate_admin_session` continue to work without breaking decoupled services.
    """
    @property
    def STORAGE(self):
        return config.STORAGE

    @STORAGE.setter
    def STORAGE(self, val):
        p = Path(val) if val else val
        config.STORAGE = p
        self.__dict__["STORAGE"] = p

    @property
    def PHOTOS(self):
        return config.PHOTOS

    @PHOTOS.setter
    def PHOTOS(self, val):
        p = Path(val) if val else val
        config.PHOTOS = p
        self.__dict__["PHOTOS"] = p

    @property
    def DATA(self):
        return config.DATA

    @DATA.setter
    def DATA(self, val):
        p = Path(val) if val else val
        config.DATA = p
        self.__dict__["DATA"] = p

    @property
    def QUEUE_DIR(self):
        return config.QUEUE_DIR

    @QUEUE_DIR.setter
    def QUEUE_DIR(self, val):
        p = Path(val) if val else val
        config.QUEUE_DIR = p
        self.__dict__["QUEUE_DIR"] = p

    @property
    def active_tokens(self):
        return dependencies.active_tokens

    @active_tokens.setter
    def active_tokens(self, val):
        dependencies.active_tokens = val
        self.__dict__["active_tokens"] = val


_this_module = sys.modules[__name__]
_wrapper = _AppFacadeModule(__name__)
_wrapper.__dict__.update(_this_module.__dict__)
sys.modules[__name__] = _wrapper
