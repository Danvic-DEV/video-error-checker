import os
import subprocess
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.models import Setting


router = APIRouter(prefix="/api/gpu", tags=["gpu"])

SUPPORTED_GPU_BACKENDS = ["auto", "cuda", "vaapi", "qsv", "opencl", "vulkan"]


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _run_command(command: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    except FileNotFoundError as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))
    except Exception as exc:  # pragma: no cover - defensive fallback
        return CommandResult(returncode=1, stdout="", stderr=str(exc))


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def _as_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ffmpeg_hwaccels() -> tuple[list[str], str | None]:
    result = _run_command(["ffmpeg", "-hide_banner", "-hwaccels"])
    if result.returncode != 0:
        return [], result.stderr or "Unable to list ffmpeg hardware accelerators"

    accels: list[str] = []
    for line in result.stdout.splitlines():
        clean = line.strip().lower()
        if not clean or clean.startswith("hardware acceleration methods"):
            continue
        accels.append(clean)
    return sorted(set(accels)), None


def _resolve_backend(selected_backend: str, hwaccels: list[str]) -> str:
    backend = (selected_backend or "auto").strip().lower()
    if backend != "auto":
        return backend

    for candidate in ["cuda", "qsv", "vaapi", "vulkan", "opencl"]:
        if candidate in hwaccels:
            return candidate
    return ""


def _nvidia_devices() -> tuple[list[dict[str, str]], str | None]:
    result = _run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,name",
            "--format=csv,noheader",
        ]
    )
    if result.returncode != 0:
        return [], result.stderr or "nvidia-smi query failed"

    devices: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if "," in clean:
            index, name = clean.split(",", 1)
            device_id = index.strip()
            devices.append(
                {
                    "id": device_id,
                    "label": f"GPU {device_id}: {name.strip()}",
                    "backend": "cuda",
                    "usable": True,
                    "reason": "",
                }
            )

    return devices, None


def _dri_devices() -> tuple[list[dict[str, str]], str | None]:
    dri_root = "/dev/dri"
    if not os.path.isdir(dri_root):
        return [], f"{dri_root} not present in container"

    try:
        entries = sorted(os.listdir(dri_root))
    except OSError as exc:
        return [], str(exc)

    render_nodes = [item for item in entries if item.startswith("renderD")]
    if not render_nodes:
        return [], "No render nodes found under /dev/dri"

    devices: list[dict[str, str]] = []
    for node in render_nodes:
        node_path = f"{dri_root}/{node}"
        devices.append(
            {
                "id": node_path,
                "label": f"DRI {node}",
                "backend": "vaapi",
                "usable": True,
                "reason": "",
            }
        )
        devices.append(
            {
                "id": node_path,
                "label": f"DRI {node} (QSV)",
                "backend": "qsv",
                "usable": True,
                "reason": "",
            }
        )

    return devices, None


def _ldconfig_contains(pattern: str) -> bool:
    result = _run_command(["sh", "-lc", f"ldconfig -p | grep -i '{pattern}'"])
    return result.returncode == 0 and bool(result.stdout)


def _has_video_capability() -> bool:
    caps = os.environ.get("NVIDIA_DRIVER_CAPABILITIES", "")
    if not caps:
        return False
    cap_set = {item.strip().lower() for item in caps.split(",") if item.strip()}
    return "all" in cap_set or "video" in cap_set


def _probe_cuda(device_id: str) -> tuple[bool, str, str]:
    # This validates CUDA initialization (driver/runtime visibility), not media decode quality.
    result = _run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-init_hw_device",
            f"cuda=gpu:{device_id}",
            "-hwaccel",
            "cuda",
            "-hwaccel_device",
            device_id,
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=1",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ]
    )

    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        return True, "CUDA probe passed", ""

    message = output or "CUDA probe failed"
    lowered = message.lower()
    if "libnvcuvid" in lowered:
        return False, message, "Ensure NVIDIA video driver capability is mapped into the container"
    if "cannot load libcuda" in lowered or "could not dynamically load cuda" in lowered:
        return False, message, "Ensure NVIDIA runtime maps CUDA driver libraries into this container"
    if "no device" in lowered or "device" in lowered:
        return False, message, "Verify selected GPU device id is valid and visible in this container"
    return False, message, "Verify NVIDIA runtime, driver libraries, and CUDA capability mapping"


def _probe_vaapi(device_id: str) -> tuple[bool, str, str]:
    target = device_id if device_id.startswith("/dev/dri/") else "/dev/dri/renderD128"
    result = _run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-init_hw_device",
            f"vaapi=va:{target}",
            "-hwaccel",
            "vaapi",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=1",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        return True, "VAAPI probe passed", ""
    message = output or "VAAPI probe failed"
    if "no such file" in message.lower() or "not found" in message.lower():
        return False, message, "Pass /dev/dri into the container and select a valid render node"
    return False, message, "Verify /dev/dri mapping and VAAPI userspace libraries"


def _probe_qsv(device_id: str) -> tuple[bool, str, str]:
    target = device_id if device_id.startswith("/dev/dri/") else "/dev/dri/renderD128"
    result = _run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-init_hw_device",
            f"vaapi=va:{target}",
            "-init_hw_device",
            "qsv=qs@va",
            "-hwaccel",
            "qsv",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=1",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        return True, "QSV probe passed", ""
    message = output or "QSV probe failed"
    return False, message, "Verify Intel iGPU exposure via /dev/dri and ffmpeg QSV support"


def _probe_opencl(device_id: str) -> tuple[bool, str, str]:
    index = device_id if device_id.isdigit() else "0"
    result = _run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-init_hw_device",
            f"opencl=ocl:{index}.0",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=1",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        return True, "OpenCL probe passed", ""
    message = output or "OpenCL probe failed"
    return False, message, "Verify OpenCL runtime visibility in this container"


def _probe_vulkan(device_id: str) -> tuple[bool, str, str]:
    index = device_id if device_id.isdigit() else "0"
    result = _run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-init_hw_device",
            f"vulkan=vk:{index}",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=1",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        return True, "Vulkan probe passed", ""
    message = output or "Vulkan probe failed"
    return False, message, "Verify Vulkan runtime and device visibility in this container"


def _probe_backend(backend: str, device_id: str) -> tuple[bool, str, str]:
    if backend == "cuda":
        return _probe_cuda(device_id)
    if backend == "vaapi":
        return _probe_vaapi(device_id)
    if backend == "qsv":
        return _probe_qsv(device_id)
    if backend == "opencl":
        return _probe_opencl(device_id)
    if backend == "vulkan":
        return _probe_vulkan(device_id)
    return False, f"Unsupported backend for probing: {backend}", "Use auto or a supported backend"


@router.get("/discovery")
def get_gpu_discovery() -> dict:
    hwaccels, hwaccel_error = _ffmpeg_hwaccels()
    nvidia_devices, nvidia_error = _nvidia_devices()
    dri_devices, dri_error = _dri_devices()

    backends: list[dict[str, object]] = []
    for backend in SUPPORTED_GPU_BACKENDS:
        if backend == "auto":
            backends.append(
                {
                    "id": "auto",
                    "label": "Auto",
                    "available": bool(hwaccels),
                    "reason": "" if hwaccels else (hwaccel_error or "No hardware acceleration methods reported"),
                }
            )
            continue

        available = backend in hwaccels
        backends.append(
            {
                "id": backend,
                "label": backend.upper() if backend != "vaapi" else "VAAPI",
                "available": available,
                "reason": "" if available else "Not reported by ffmpeg -hwaccels",
            }
        )

    warnings: list[str] = []
    if hwaccel_error:
        warnings.append(hwaccel_error)
    if nvidia_error and "cuda" in hwaccels:
        warnings.append(nvidia_error)
    if dri_error and ("vaapi" in hwaccels or "qsv" in hwaccels):
        warnings.append(dri_error)

    return {
        "hwaccels": hwaccels,
        "backends": backends,
        "devices": [*nvidia_devices, *dri_devices],
        "warnings": warnings,
    }


@router.get("/diagnostics")
def get_gpu_diagnostics(db: Session = Depends(get_db)) -> dict:
    selected_backend = _get_setting(db, "gpu_backend", "auto").strip().lower() or "auto"
    selected_device = _get_setting(db, "gpu_device_id", "0")
    gpu_enabled = _as_bool(_get_setting(db, "gpu_enabled", "false"))

    checks: list[dict[str, object]] = []

    ffmpeg_version = _run_command(["ffmpeg", "-hide_banner", "-version"])
    checks.append(
        {
            "id": "ffmpeg",
            "severity": "info" if ffmpeg_version.returncode == 0 else "error",
            "ok": ffmpeg_version.returncode == 0,
            "message": (
                ffmpeg_version.stdout.splitlines()[0]
                if ffmpeg_version.returncode == 0 and ffmpeg_version.stdout
                else (ffmpeg_version.stderr or "ffmpeg not available")
            ),
            "hint": "Install ffmpeg in the container" if ffmpeg_version.returncode != 0 else "",
        }
    )

    hwaccels, hwaccel_error = _ffmpeg_hwaccels()
    checks.append(
        {
            "id": "hwaccels",
            "severity": "info" if hwaccels else "warn",
            "ok": bool(hwaccels),
            "message": ", ".join(hwaccels) if hwaccels else (hwaccel_error or "No hwaccels found"),
            "hint": "Verify ffmpeg build has hardware acceleration support",
        }
    )

    nvidia_smi = _run_command(["nvidia-smi", "-L"])
    checks.append(
        {
            "id": "nvidia_smi",
            "severity": "info" if nvidia_smi.returncode == 0 else "warn",
            "ok": nvidia_smi.returncode == 0,
            "message": (
                "NVIDIA runtime visible"
                if nvidia_smi.returncode == 0
                else (nvidia_smi.stderr or "nvidia-smi unavailable")
            ),
            "hint": "Enable GPU device passthrough/runtime for this container",
        }
    )

    has_libcuda = _ldconfig_contains("libcuda")
    has_libnvcuvid = _ldconfig_contains("libnvcuvid")
    has_video_capability = _has_video_capability()
    checks.append(
        {
            "id": "libcuda",
            "severity": "info" if has_libcuda else "warn",
            "ok": has_libcuda,
            "message": "libcuda found" if has_libcuda else "libcuda not found in ldconfig",
            "hint": "Ensure NVIDIA runtime maps driver libraries into container",
        }
    )
    checks.append(
        {
            "id": "libnvcuvid",
            "severity": "info" if has_libnvcuvid else "warn",
            "ok": has_libnvcuvid,
            "message": "libnvcuvid found" if has_libnvcuvid else "libnvcuvid not found in ldconfig",
            "hint": "Set NVIDIA_DRIVER_CAPABILITIES to include video",
        }
    )
    checks.append(
        {
            "id": "nvidia_driver_capabilities",
            "severity": "info" if has_video_capability else "warn",
            "ok": has_video_capability,
            "message": (
                "NVIDIA_DRIVER_CAPABILITIES includes video"
                if has_video_capability
                else "NVIDIA_DRIVER_CAPABILITIES missing video"
            ),
            "hint": "Set NVIDIA_DRIVER_CAPABILITIES=video,compute,utility",
        }
    )

    probes: list[dict[str, object]] = []
    resolved_backend = _resolve_backend(selected_backend, hwaccels)
    if gpu_enabled and resolved_backend in {"cuda", "vaapi", "qsv", "opencl", "vulkan"}:
        default_device = selected_device or ("/dev/dri/renderD128" if resolved_backend in {"vaapi", "qsv"} else "0")
        probe_ok, probe_message, probe_hint = _probe_backend(resolved_backend, default_device)
        probes.append(
            {
                "backend": resolved_backend,
                "device_id": default_device,
                "ok": probe_ok,
                "message": probe_message,
                "hint": probe_hint,
            }
        )
    elif gpu_enabled and selected_backend != "auto":
        probes.append(
            {
                "backend": selected_backend,
                "device_id": selected_device or "0",
                "ok": selected_backend in hwaccels,
                "message": (
                    f"{selected_backend} reported by ffmpeg"
                    if selected_backend in hwaccels
                    else f"{selected_backend} not reported by ffmpeg -hwaccels"
                ),
                "hint": "Use auto backend or choose a backend reported by ffmpeg",
            }
        )

    successful_cuda_probe = any(
        probe.get("backend") == "cuda" and bool(probe.get("ok", False)) for probe in probes
    )
    if successful_cuda_probe:
        for check in checks:
            if check["id"] in {"libcuda", "libnvcuvid"} and not bool(check["ok"]):
                check["ok"] = True
                check["severity"] = "info"
                check["message"] = f"{check['message']} (runtime probe succeeded)"
                check["hint"] = "Runtime CUDA probe passed; ldconfig visibility can be misleading in containers"

    has_errors = any(not bool(check["ok"]) and check["severity"] == "error" for check in checks)
    failed_probe = any(not probe.get("ok", False) for probe in probes)

    return {
        "summary": {
            "gpu_enabled": gpu_enabled,
            "selected_backend": selected_backend,
            "resolved_backend": resolved_backend or "cpu",
            "selected_device": selected_device,
            "healthy": not has_errors and not failed_probe,
        },
        "environment": {
            "NVIDIA_VISIBLE_DEVICES": os.environ.get("NVIDIA_VISIBLE_DEVICES", ""),
            "NVIDIA_DRIVER_CAPABILITIES": os.environ.get("NVIDIA_DRIVER_CAPABILITIES", ""),
        },
        "checks": checks,
        "probes": probes,
    }
