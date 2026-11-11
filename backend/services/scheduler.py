import time
import secrets
import threading

from backend.config import current_task, task_lock, append_log
from backend.db.connection import now_ist
from backend.db.tasks import claim_next_queued_task, enqueue_upload_task
from backend.db.automations import (
    get_due_automations, is_automation_task_pending, advance_automation_schedule
)
from backend.services.uploads import execute_queued_job

queue_worker_running = False
queue_worker_lock = threading.Lock()
_workers_started = False

def is_queue_worker_running() -> bool:
    global queue_worker_running
    return queue_worker_running

def trigger_queue_worker():
    global queue_worker_running
    with queue_worker_lock:
        if not queue_worker_running:
            queue_worker_running = True
            threading.Thread(target=_queue_worker_loop, daemon=True, name="UploadQueueWorker").start()

def _queue_worker_loop():
    global queue_worker_running
    append_log(f"[{now_ist().strftime('%H:%M:%S')}] Upload task queue worker loop started.")
    try:
        while True:
            # Check if any manual/active task is currently running in current_task
            with task_lock:
                busy = current_task.get("status") == "running"
            if busy:
                time.sleep(1.5)
                continue

            # Atomically claim the next queued task in FIFO order
            job = claim_next_queued_task()
            if not job:
                # No pending queued jobs
                break

            try:
                execute_queued_job(job)
            except Exception as ex:
                append_log(f"[{now_ist().strftime('%H:%M:%S')}] Queue worker uncaught exception on task #{job.get('task_id')}: {ex}")
            time.sleep(1)
    finally:
        with queue_worker_lock:
            queue_worker_running = False
        append_log(f"[{now_ist().strftime('%H:%M:%S')}] Upload task queue worker idle (all tasks processed).")

def _automation_scheduler_loop():
    """Background worker checking due automations every 30 seconds with idempotency protection."""
    while True:
        try:
            time.sleep(30)
            now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
            due_list = get_due_automations(now_str)
            for auto in due_list:
                auto_id = auto["id"]
                auto_name = auto["name"]
                event_slug = auto.get("event_slug", "brk-sir")
                gdrive_url = auto.get("gdrive_url", "")
                target_album = auto.get("target_album_slug", "auto")

                # Idempotency guard: prevent duplicate queuing if already running or queued
                if is_automation_task_pending(gdrive_url, auto_name):
                    continue

                task_id = f"auto_{secrets.token_hex(4)}"
                enqueue_upload_task(
                    task_id=task_id,
                    task_name=f"🤖 {auto_name}",
                    task_type="gdrive_sync",
                    event_slug=event_slug,
                    target_album_slug=target_album,
                    target_album_name="Auto Ingestion",
                    source_info=gdrive_url,
                    staged_dir="",
                    file_count=0
                )
                advance_automation_schedule(auto_id, task_id=task_id, status="queued")
                trigger_queue_worker()
                append_log(f"[{now_ist().strftime('%H:%M:%S')}] Automation '{auto_name}' triggered. Task #{task_id} added to upload queue.")
        except Exception:
            time.sleep(10)

def start_background_workers():
    global _workers_started
    if not _workers_started:
        _workers_started = True
        threading.Thread(target=_automation_scheduler_loop, daemon=True, name="AutomationScheduler").start()

