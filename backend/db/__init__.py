"""
Database layer for MagniWedding / BRK Photos multi-event architecture.
Uses SQLite in WAL mode for microsecond reads and concurrent safety.
"""
from backend.db.connection import (
    BASE, STORAGE, DATA_DIR, PHOTOS, DB_PATH, IST,
    set_db_path, now_ist, parse_date_string, extract_photo_capture_date, get_db
)
from backend.db.schema import (
    init_db, backfill_photo_dates, migrate_existing_data
)
from backend.db.tasks import (
    buffer_task_log, flush_task_logs, create_task_record, update_task_progress,
    stop_running_tasks, get_latest_task, list_task_history, get_task_by_id,
    enqueue_upload_task, claim_next_queued_task, update_queue_task,
    get_queue_tasks, get_queue_task_by_id, get_queue_task_logs,
    cancel_queue_task, retry_queue_task, clear_completed_queue_tasks,
    get_queue_stats, reset_stale_running_queue_tasks
)
from backend.db.people import (
    cluster_and_sync_people, list_people_for_event, get_person_photos
)
from backend.db.automations import (
    compute_next_trigger, create_automation, get_automations, get_automation_by_id,
    update_automation, delete_automation, toggle_automation, record_automation_history,
    get_automation_history, get_due_automations, is_automation_task_pending,
    advance_automation_schedule
)
from backend.db.settings import (
    get_setting, set_setting, get_all_settings,
    record_indexing_error, get_indexing_errors
)
from backend.db.reviews import (
    record_match_review, get_match_reviews, update_match_feedback,
    record_duplicate_pair, get_duplicate_groups, resolve_duplicate_group
)
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
from backend.db.shared_albums import (
    save_search_result_proof, get_search_result_proof, create_shared_album_atomic,
    get_shared_album_by_token, get_shared_album_photos_page, verify_photo_in_shared_album,
    get_all_photo_records_for_shared_album, revoke_shared_album_by_creator,
    admin_get_shared_albums, admin_revoke_shared_album, admin_update_shared_album_expiry,
    admin_delete_shared_album, admin_get_shared_album_photos,
    check_and_increment_shared_rate_limit, record_shared_album_access_log
)
