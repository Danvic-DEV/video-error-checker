import os
import subprocess
from collections.abc import Callable
from datetime import datetime
import json
from functools import lru_cache

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


def _as_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_gpu_settings(session: Session) -> dict[str, str | bool]:
    backend = _get_setting(session, "gpu_backend", "auto").strip().lower() or "auto"
    if backend not in {"auto", "cuda", "vaapi", "qsv", "opencl", "vulkan"}:
        backend = "auto"
    return {
        "enabled": _as_bool(_get_setting(session, "gpu_enabled", "false")),
        "backend": backend,
        "device_id": _get_setting(session, "gpu_device_id", "0").strip() or "0",
    }


@lru_cache(maxsize=1)
def _get_ffmpeg_hwaccels() -> tuple[str, ...]:
    returncode, output = run_command_with_code(["ffmpeg", "-hide_banner", "-hwaccels"])
    if returncode != 0:
        return tuple()

    methods: list[str] = []
    for line in output.splitlines():
        clean = line.strip().lower()
        if not clean or clean.startswith("hardware acceleration methods"):
            continue
        methods.append(clean)
    return tuple(dict.fromkeys(methods))


def _resolve_gpu_backend(backend: str) -> str:
    if backend != "auto":
        return backend

    available = set(_get_ffmpeg_hwaccels())
    for candidate in ("cuda", "qsv", "vaapi", "vulkan", "opencl"):
        if candidate in available:
            return candidate
    return ""


def _build_hwaccel_args(gpu_settings: dict[str, str | bool] | None) -> tuple[list[str], str]:
    if not gpu_settings or not bool(gpu_settings.get("enabled", False)):
        return [], ""

    backend = _resolve_gpu_backend(str(gpu_settings.get("backend", "auto")))
    if not backend:
        return [], ""

    args: list[str] = ["-hwaccel", backend]
    if backend == "cuda":
        device_id = str(gpu_settings.get("device_id", "0"))
        args.extend(["-hwaccel_device", device_id])
    return args, backend


def _is_hwaccel_failure(output: str) -> bool:
    lowered = output.lower()
    markers = (
        "cannot load libnvcuvid",
        "failed loading nvcuvid",
        "hwaccel initialisation returned error",
        "failed setup for format cuda",
        "device setup failed",
        "no device available",
        "could not dynamically load cuda",
        "invalid device",
    )
    return any(marker in lowered for marker in markers)


def _run_media_command(
    command: list[str],
    gpu_settings: dict[str, str | bool] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
) -> str:
    hwaccel_args, resolved_backend = _build_hwaccel_args(gpu_settings)
    if not hwaccel_args:
        return run_command(command)

    gpu_command = [command[0], *hwaccel_args, *command[1:]]
    returncode, output = run_command_with_code(gpu_command)
    if returncode == 0:
        return output

    if _is_hwaccel_failure(output):
        if log_callback is not None:
            log_callback(
                "warn",
                (
                    f"GPU backend {resolved_backend or 'auto'} failed for command; "
                    "retrying on CPU"
                ),
            )
        return run_command(command)

    return output


def run_command_with_code(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.returncode, result.stdout.strip()
        return result.returncode, result.stderr.strip()
    except Exception as exc:
        return 1, f"Error running command: {exc}"


def run_command(command: list[str]) -> str:
    _, output = run_command_with_code(command)
    return output


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _run_json_command(command: list[str]) -> dict:
    output = run_command(command)
    if not output or output.startswith("Error running command"):
        return {}
    try:
        parsed = json.loads(output)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def detect_playback_artifacts(
    file_path: str,
    gpu_settings: dict[str, str | bool] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
) -> list[str]:
    issues: list[str] = []

    # 1) Stream metadata and A/V drift heuristics
    stream_probe = _run_json_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            file_path,
        ]
    )

    streams = stream_probe.get("streams", []) if isinstance(stream_probe, dict) else []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    if video_stream and audio_stream:
        video_duration = _safe_float(str(video_stream.get("duration", "")))
        audio_duration = _safe_float(str(audio_stream.get("duration", "")))
        if video_duration is not None and audio_duration is not None:
            drift_seconds = abs(video_duration - audio_duration)
            allowed_drift = max(5.0, video_duration * 0.10)
            if drift_seconds > allowed_drift:
                issues.append(
                    f"Potential A/V sync drift: audio-video duration delta {drift_seconds:.2f}s"
                )

    # 2) Warning-level ffmpeg scan for strong timestamp/playback warnings
    warning_output = _run_media_command(
        ["ffmpeg", "-v", "warning", "-i", file_path, "-f", "null", "-"],
        gpu_settings=gpu_settings,
        log_callback=log_callback,
    )
    warning_markers = (
        "non monotonically increasing dts",
        "invalid dts",
        "invalid pts",
        "application provided invalid, non monotonically increasing dts",
    )
    lower_warning_output = warning_output.lower()
    if warning_output and any(marker in lower_warning_output for marker in warning_markers):
        issues.append("ffmpeg reported strong timestamp anomalies")

    # Deduplicate while preserving order
    deduped_issues = list(dict.fromkeys(issues))
    return deduped_issues


def check_video_file(
    file_path: str,
    gpu_settings: dict[str, str | bool] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
) -> dict[str, str]:
    corruption_check = _run_media_command(
        ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"],
        gpu_settings=gpu_settings,
        log_callback=log_callback,
    )
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

    playback_issues = detect_playback_artifacts(
        file_path,
        gpu_settings=gpu_settings,
        log_callback=log_callback,
    )
    if playback_issues:
        return {
            "status": "Playback Artifacts Suspected",
            "details": " | ".join(playback_issues),
        }

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
    gpu_settings = get_gpu_settings(session)

    if log_callback is not None:
        if bool(gpu_settings.get("enabled", False)):
            backend = str(gpu_settings.get("backend", "auto"))
            resolved_backend = _resolve_gpu_backend(backend) if backend == "auto" else backend
            device_id = str(gpu_settings.get("device_id", "0"))
            if resolved_backend:
                log_callback(
                    "info",
                    (
                        "GPU offload enabled "
                        f"(backend={backend}, resolved={resolved_backend}, device={device_id})"
                    ),
                )
            else:
                log_callback(
                    "warn",
                    "GPU offload enabled but no supported backend was resolved; falling back to CPU",
                )
        else:
            log_callback("info", "GPU offload disabled; using CPU decode")

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
            check = check_video_file(file_path, gpu_settings=gpu_settings, log_callback=log_callback)
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
    if log_callback is not None:
        log_callback("info", f"Indexing files across {len(targets)} enabled targets")

    # Pre-count all files across all targets (fast — no ffmpeg)
    file_lists: dict[int, list[dict]] = {}
    total_files = 0
    for target in targets:
        if os.path.isdir(target.path):
            if log_callback is not None:
                log_callback("info", f"Indexing target {target.label}: {target.path}")
            files = get_file_list(target.path, video_extensions)
            file_lists[target.id] = files
            total_files += len(files)
            if progress_callback is not None:
                progress_callback(target.label, "", total_files, 0)
            if log_callback is not None:
                log_callback(
                    "info",
                    f"Indexed {len(files)} files in {target.label} ({total_files} total discovered)",
                )
        else:
            file_lists[target.id] = []
            if log_callback is not None:
                log_callback("warn", f"Indexing skipped for {target.label}: path not found ({target.path})")

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
