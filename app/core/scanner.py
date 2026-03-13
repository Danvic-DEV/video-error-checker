import os
import subprocess
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.discord import send_discord_message
from app.core.models import ScanResult, ScanTarget, Setting


def _get_setting(session: Session, key: str, default: str = "") -> str:
    value = session.query(Setting).filter(Setting.key == key).first()
    return value.value if value else default


def get_video_extensions(session: Session) -> tuple[str, ...]:
    raw = _get_setting(session, "video_extensions", ".mp4,.mkv,.avi,.mov,.flv,.wmv")
    extensions = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    return extensions or (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv")


def get_file_list(scan_path: str, video_extensions: tuple[str, ...]) -> list[dict[str, float | str]]:
    video_files: list[dict[str, float | str]] = []
    for root, _, files in os.walk(scan_path):
        for file_name in files:
            if file_name.lower().endswith(video_extensions):
                file_path = os.path.join(root, file_name)
                last_modified = os.path.getmtime(file_path)
                video_files.append({"file_path": file_path, "last_modified": last_modified})
    return video_files


def run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return result.stderr.strip()
    except Exception as exc:
        return f"Error running command: {exc}"


def check_video_file(file_path: str) -> dict[str, str]:
    corruption_check = run_command(["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"])
    if corruption_check:
        return {"status": "Corruption Detected", "details": corruption_check}

    stream_check = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "csv=p=0",
            file_path,
        ]
    )
    if not stream_check:
        return {"status": "Stream Issues", "details": "No valid video stream detected"}

    return {"status": "OK", "details": stream_check}


def _upsert_result(
    session: Session,
    target_id: int,
    file_path: str,
    last_modified: float,
    status: str,
    details: str,
    duration: float,
) -> None:
    existing = (
        session.query(ScanResult)
        .filter(ScanResult.target_id == target_id, ScanResult.file_path == file_path)
        .first()
    )
    if existing:
        existing.last_modified = last_modified
        existing.status = status
        existing.details = details
        existing.scan_duration_seconds = duration
        existing.scanned_at = datetime.utcnow()
        return

    session.add(
        ScanResult(
            target_id=target_id,
            file_path=file_path,
            last_modified=last_modified,
            status=status,
            details=details,
            scan_duration_seconds=duration,
            scanned_at=datetime.utcnow(),
        )
    )


def scan_target(
    session: Session,
    target: ScanTarget,
    progress_callback: Callable[[str, str, int, int], None] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
    files_done_ref: list[int] | None = None,
    total_files: int = 0,
    preloaded_files: list[dict] | None = None,
) -> int:
    if not os.path.isdir(target.path):
        if log_callback is not None:
            log_callback("warn", f"Target {target.label} skipped: path not found ({target.path})")
        return 0

    if preloaded_files is not None:
        files = preloaded_files
    else:
        video_extensions = get_video_extensions(session)
        files = get_file_list(target.path, video_extensions)
    if files_done_ref is None:
        files_done_ref = [0]

    general_webhook = _get_setting(session, "general_discord_webhook", "")
    failed_webhook = _get_setting(session, "failed_discord_webhook", "")

    if log_callback is not None:
        log_callback("info", f"Target {target.label}: {len(files)} files discovered")

    scanned_count = 0
    for file_info in files:
        try:
            file_path = str(file_info["file_path"])
            last_modified = float(file_info["last_modified"])
            file_name = os.path.basename(file_path)

            # Report progress BEFORE spending time on ffmpeg
            if progress_callback is not None:
                files_done_ref[0] += 1
                progress_callback(target.label, file_path, files_done_ref[0], total_files)
            if log_callback is not None:
                log_callback(
                    "info",
                    f"Checking {file_name} ({files_done_ref[0]}/{total_files or len(files)})",
                )

            existing = (
                session.query(ScanResult)
                .filter(ScanResult.target_id == target.id, ScanResult.file_path == file_path)
                .first()
            )
            existing_modified = existing.last_modified if existing else 0.0
            if last_modified <= existing_modified:
                if log_callback is not None:
                    log_callback("info", f"Skipped unchanged: {file_name}")
                continue

            started = datetime.utcnow()
            check = check_video_file(file_path)
            duration = (datetime.utcnow() - started).total_seconds()

            _upsert_result(
                session=session,
                target_id=target.id,
                file_path=file_path,
                last_modified=last_modified,
                status=check["status"],
                details=check["details"],
                duration=duration,
            )
            session.commit()
            scanned_count += 1

            if check["status"] == "OK":
                if log_callback is not None:
                    log_callback("info", f"OK: {file_name}")
                send_discord_message(
                    (
                        f"✅ [{target.label}] Video check completed successfully:\n"
                        f"File: {file_path}\n"
                        f"Details: {check['details']}\n"
                        f"Time Taken: {duration:.2f} seconds"
                    ),
                    general_webhook,
                )
            else:
                if log_callback is not None:
                    log_callback("warn", f"Issue: {file_name} ({check['status']})")
                send_discord_message(
                    (
                        f"⚠️ [{target.label}] Video check failed:\n"
                        f"File: {file_path}\n"
                        f"Status: {check['status']}\n"
                        f"Details: {check['details']}\n"
                        f"Time Taken: {duration:.2f} seconds"
                    ),
                    failed_webhook,
                )
        except Exception as exc:
            session.rollback()
            if log_callback is not None:
                log_callback("error", f"Failed to persist result for {file_info.get('file_path')}: {exc}")

    send_discord_message(
        f"✅ [{target.label}] Video scan completed. {scanned_count} files checked.",
        general_webhook,
    )
    if log_callback is not None:
        log_callback("info", f"Target {target.label} complete: {scanned_count} files scanned")
    return scanned_count


def run_full_scan(
    session: Session,
    progress_callback: "Callable[[str, str, int, int], None] | None" = None,
    log_callback: Callable[[str, str], None] | None = None,
) -> dict[str, int]:
    targets = session.query(ScanTarget).filter(ScanTarget.enabled.is_(True)).all()
    video_extensions = get_video_extensions(session)

    # Pre-count all files across all targets (fast — no ffmpeg)
    file_lists: dict[int, list[dict]] = {}
    total_files = 0
    for target in targets:
        if os.path.isdir(target.path):
            files = get_file_list(target.path, video_extensions)
            file_lists[target.id] = files
            total_files += len(files)
        else:
            file_lists[target.id] = []

    # Announce total immediately so UI can show a progress bar
    if progress_callback is not None:
        progress_callback("", "", 0, total_files)
    if log_callback is not None:
        log_callback("info", f"Scan started: {len(targets)} targets, {total_files} total files")

    # Shared mutable counter passed into scan_target via a list (avoids nonlocal)
    files_done_ref: list[int] = [0]

    summary: dict[str, int] = {}
    for target in targets:
        summary[target.label] = scan_target(
            session,
            target,
            progress_callback=progress_callback,
            log_callback=log_callback,
            files_done_ref=files_done_ref,
            total_files=total_files,
            preloaded_files=file_lists.get(target.id),
        )
    if log_callback is not None:
        log_callback("info", "Scan completed")
    return summary
