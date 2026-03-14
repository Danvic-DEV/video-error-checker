import os
import threading
import time
from collections import deque
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.models import ScanResult
from app.core.scanner import check_video_file, run_full_scan


class ScanState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.last_started: datetime | None = None
        self.last_completed: datetime | None = None
        self.last_summary: dict[str, int] = {}
        self.files_total: int = 0
        self.files_done: int = 0
        self.current_file: str = ""
        self.current_file_path: str = ""
        self.current_file_started_at: datetime | None = None
        self.current_target: str = ""
        self.recent_logs: list[dict[str, str]] = []
        self.persisted_results_count: int = 0


class RescanQueueState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: deque[int] = deque()
        self.queued_ids: set[int] = set()
        self.active_result_id: int | None = None
        self.active_file_path: str = ""
        self.active_started_at: datetime | None = None
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()


scan_state = ScanState()
rescan_state = RescanQueueState()
scheduler = BackgroundScheduler(timezone="UTC")
MAX_SCAN_LOGS = 200


def _append_log(level: str, message: str, source: str = "scan") -> None:
    now = datetime.utcnow().isoformat()
    with scan_state.lock:
        scan_state.recent_logs.append(
            {"timestamp": now, "level": level, "message": message, "source": source}
        )
        if len(scan_state.recent_logs) > MAX_SCAN_LOGS:
            scan_state.recent_logs = scan_state.recent_logs[-MAX_SCAN_LOGS:]


def _read_persisted_results_count() -> int:
    try:
        with SessionLocal() as session:
            return int(session.query(func.count(ScanResult.id)).scalar() or 0)
    except Exception:
        return -1


def _refresh_persisted_results_count() -> None:
    count = _read_persisted_results_count()
    if count >= 0:
        with scan_state.lock:
            scan_state.persisted_results_count = count


def _get_result_path_map(result_ids: list[int]) -> dict[int, str]:
    if not result_ids:
        return {}
    with SessionLocal() as session:
        rows = session.query(ScanResult.id, ScanResult.file_path).filter(ScanResult.id.in_(result_ids)).all()
    return {int(row_id): str(file_path) for row_id, file_path in rows}


def _process_rescan_result(result_id: int) -> None:
    with SessionLocal() as session:
        result = session.query(ScanResult).filter(ScanResult.id == result_id).first()
        if not result:
            _append_log("warn", f"Queued rescan skipped: result {result_id} not found", "rescan")
            return

        with rescan_state.lock:
            rescan_state.active_file_path = result.file_path
            if rescan_state.active_started_at is None:
                rescan_state.active_started_at = datetime.utcnow()

        file_path = result.file_path
        _append_log("info", f"Rescan started for result {result_id}", "rescan")
        if not os.path.exists(file_path):
            result.status = "File Missing"
            result.details = "File no longer exists at recorded path"
            result.scan_duration_seconds = 0.0
            result.scanned_at = datetime.utcnow()
            session.commit()
            _append_log("warn", f"Rescan missing file: {file_path}", "rescan")
            return

        result.status = "Rescanning"
        result.details = "Manual rescan in progress"
        result.scanned_at = datetime.utcnow()
        session.commit()

        started = time.perf_counter()
        try:
            check = check_video_file(file_path)
            duration = time.perf_counter() - started

            result.status = check["status"]
            result.details = check["details"]
            result.scan_duration_seconds = duration
            result.scanned_at = datetime.utcnow()
            try:
                result.last_modified = os.path.getmtime(file_path)
            except OSError:
                pass
            session.commit()
            _append_log("info", f"Rescan complete for result {result_id}: {result.status}", "rescan")
        except Exception as exc:
            session.rollback()
            result = session.query(ScanResult).filter(ScanResult.id == result_id).first()
            if result:
                result.status = "Rescan Failed"
                result.details = f"Manual rescan failed: {exc}"
                result.scanned_at = datetime.utcnow()
                session.commit()
            _append_log("error", f"Rescan failed for result {result_id}: {exc}", "rescan")


def _rescan_worker_loop() -> None:
    _append_log("info", "Rescan worker started", "rescan")
    while not rescan_state.stop_event.is_set():
        result_id: int | None = None
        with rescan_state.lock:
            if rescan_state.queue:
                result_id = rescan_state.queue.popleft()
                rescan_state.queued_ids.discard(result_id)
                rescan_state.active_result_id = result_id
                rescan_state.active_started_at = datetime.utcnow()
                rescan_state.active_file_path = ""

        if result_id is None:
            time.sleep(0.2)
            continue

        _process_rescan_result(result_id)

        with rescan_state.lock:
            if rescan_state.active_result_id == result_id:
                rescan_state.active_result_id = None
                rescan_state.active_file_path = ""
                rescan_state.active_started_at = None

    _append_log("info", "Rescan worker stopped", "rescan")


def start_rescan_worker() -> None:
    with rescan_state.lock:
        if rescan_state.worker_thread and rescan_state.worker_thread.is_alive():
            return
        rescan_state.stop_event.clear()
        rescan_state.worker_thread = threading.Thread(target=_rescan_worker_loop, daemon=True)
        rescan_state.worker_thread.start()


def stop_rescan_worker() -> None:
    rescan_state.stop_event.set()


def enqueue_rescan(result_id: int) -> str:
    with rescan_state.lock:
        if rescan_state.active_result_id == result_id or result_id in rescan_state.queued_ids:
            return "duplicate"

        immediate = rescan_state.active_result_id is None and not rescan_state.queue
        rescan_state.queue.append(result_id)
        rescan_state.queued_ids.add(result_id)
        queue_depth = len(rescan_state.queue)
    if immediate:
        _append_log("info", f"Rescan accepted for immediate processing: result {result_id}", "rescan")
        return "started"

    _append_log("info", f"Rescan queued for result {result_id} (queue: {queue_depth})", "rescan")
    return "queued"


def add_system_log(level: str, message: str) -> None:
    _append_log(level, message, "system")


def _progress_callback(target_label: str, file_path: str, done: int, total: int) -> None:
    with scan_state.lock:
        scan_state.current_target = target_label
        scan_state.current_file = os.path.basename(file_path) if file_path else ""
        if file_path:
            scan_state.current_file_path = file_path
            scan_state.current_file_started_at = datetime.utcnow()
        scan_state.files_done = done
        scan_state.files_total = total


def _log_callback(level: str, message: str) -> None:
    _append_log(level, message, "scan")


def _run_scan_job() -> None:
    with scan_state.lock:
        if scan_state.running:
            return
        scan_state.running = True
        scan_state.last_started = datetime.utcnow()
        scan_state.files_total = 0
        scan_state.files_done = 0
        scan_state.current_file = ""
        scan_state.current_file_path = ""
        scan_state.current_file_started_at = None
        scan_state.current_target = ""
        scan_state.recent_logs = []

    _append_log("info", "Scan job started", "scan")

    try:
        with SessionLocal() as session:
            summary = run_full_scan(
                session,
                progress_callback=_progress_callback,
                log_callback=_log_callback,
            )
        with scan_state.lock:
            scan_state.last_summary = summary

        with SessionLocal() as session:
            persisted_count = int(session.query(func.count(ScanResult.id)).scalar() or 0)
        with scan_state.lock:
            scan_state.persisted_results_count = persisted_count
        _append_log("info", f"DB persisted rows: {persisted_count}", "scan")
    except Exception as exc:
        _append_log("error", f"Scan failed: {exc}", "scan")
    finally:
        with scan_state.lock:
            scan_state.running = False
            scan_state.last_completed = datetime.utcnow()
            scan_state.current_file = ""
            scan_state.current_file_path = ""
            scan_state.current_file_started_at = None
            scan_state.current_target = ""
        _append_log("info", "Scan job finished", "scan")


def start_scheduler(interval_seconds: int) -> None:
    if not scheduler.running:
        scheduler.start()
        _append_log("info", "Scheduler started", "system")
    _refresh_persisted_results_count()
    reschedule_scan_job(interval_seconds)
    _append_log("info", f"Scan interval set to {max(interval_seconds, 60)} seconds", "system")


def reschedule_scan_job(interval_seconds: int) -> None:
    scheduler.add_job(
        _run_scan_job,
        "interval",
        seconds=max(interval_seconds, 60),
        id="video_scan_job",
        replace_existing=True,
        max_instances=1,
    )


def trigger_manual_scan() -> bool:
    with scan_state.lock:
        if scan_state.running:
            return False
    thread = threading.Thread(target=_run_scan_job, daemon=True)
    thread.start()
    return True


def trigger_startup_scan() -> bool:
    with scan_state.lock:
        if scan_state.running:
            _append_log("warn", "Startup scan skipped: scan already running", "scan")
            return False
    _append_log("info", "Container restart detected: running startup scan", "scan")
    thread = threading.Thread(target=_run_scan_job, daemon=True)
    thread.start()
    return True


def get_scan_status() -> dict:
    _refresh_persisted_results_count()
    now = datetime.utcnow()

    with rescan_state.lock:
        queued_ids = list(rescan_state.queue)
        active_rescan_id = rescan_state.active_result_id
        active_rescan_path = rescan_state.active_file_path
        active_rescan_started_at = rescan_state.active_started_at

    queue_path_map = _get_result_path_map(queued_ids)
    queued_rescans = [
        {"result_id": result_id, "file_path": queue_path_map.get(result_id, "")}
        for result_id in queued_ids
    ]

    with scan_state.lock:
        current_elapsed_seconds = (
            max(0.0, (now - scan_state.current_file_started_at).total_seconds())
            if scan_state.current_file_started_at
            else 0.0
        )
        active_rescan_elapsed_seconds = (
            max(0.0, (now - active_rescan_started_at).total_seconds())
            if active_rescan_started_at
            else 0.0
        )
        return {
            "running": scan_state.running,
            "last_started": scan_state.last_started.isoformat() if scan_state.last_started else None,
            "last_completed": (
                scan_state.last_completed.isoformat() if scan_state.last_completed else None
            ),
            "last_summary": scan_state.last_summary,
            "files_total": scan_state.files_total,
            "files_done": scan_state.files_done,
            "current_file": scan_state.current_file,
            "current_file_path": scan_state.current_file_path,
            "current_file_started_at": (
                scan_state.current_file_started_at.isoformat()
                if scan_state.current_file_started_at
                else None
            ),
            "current_file_elapsed_seconds": round(current_elapsed_seconds, 2),
            "current_target": scan_state.current_target,
            "recent_logs": scan_state.recent_logs,
            "persisted_results_count": scan_state.persisted_results_count,
            "db_target": f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}",
            "active_rescan": {
                "result_id": active_rescan_id,
                "file_path": active_rescan_path,
                "started_at": active_rescan_started_at.isoformat() if active_rescan_started_at else None,
                "elapsed_seconds": round(active_rescan_elapsed_seconds, 2),
            },
            "queued_rescans": queued_rescans,
        }
