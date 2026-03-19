from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.models import Setting
from app.core.scheduler import reschedule_scan_job


router = APIRouter(prefix="/api/settings", tags=["settings"])


SUPPORTED_GPU_BACKENDS = {"auto", "cuda", "vaapi", "qsv", "opencl", "vulkan"}


class SettingsUpdate(BaseModel):
    general_discord_webhook: str = ""
    failed_discord_webhook: str = ""
    scan_interval_seconds: int = 3600
    video_extensions: str = ".mp4,.mkv,.avi,.mov,.flv,.wmv"
    gpu_enabled: bool = False
    gpu_backend: str = "auto"
    gpu_device_id: str = "0"


def _serialize_setting_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _normalize_gpu_backend(value: str) -> str:
    backend = value.strip().lower() if value else "auto"
    return backend if backend in SUPPORTED_GPU_BACKENDS else "auto"


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict[str, str]:
    settings = db.query(Setting).all()
    return {item.key: item.value for item in settings}


@router.put("")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> dict[str, str]:
    updates = payload.model_dump()
    updates["gpu_backend"] = _normalize_gpu_backend(str(updates.get("gpu_backend", "auto")))
    for key, value in updates.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        value_str = _serialize_setting_value(value)
        if existing:
            existing.value = value_str
        else:
            db.add(Setting(key=key, value=value_str))
    db.commit()

    reschedule_scan_job(payload.scan_interval_seconds)
    return {"status": "updated"}
