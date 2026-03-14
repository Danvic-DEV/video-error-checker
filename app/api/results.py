from datetime import datetime
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.models import ScanResult, ScanTarget
from app.core.scanner import check_video_file


router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("")
def list_results(
    db: Session = Depends(get_db),
    label: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[dict]:
    query = db.query(ScanResult, ScanTarget.label).join(ScanTarget, ScanTarget.id == ScanResult.target_id)
    if label:
        query = query.filter(ScanTarget.label == label)
    if status:
        query = query.filter(ScanResult.status == status)

    rows = query.order_by(ScanResult.scanned_at.desc()).limit(limit).all()
    return [
        {
            "id": result.id,
            "label": target_label,
            "file_path": result.file_path,
            "last_modified": result.last_modified,
            "status": result.status,
            "details": result.details,
            "scan_duration_seconds": result.scan_duration_seconds,
            "scanned_at": result.scanned_at.isoformat(),
        }
        for result, target_label in rows
    ]


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)) -> dict:
    grouped = (
        db.query(ScanTarget.label, ScanResult.status, func.count(ScanResult.id))
        .join(ScanResult, ScanTarget.id == ScanResult.target_id)
        .group_by(ScanTarget.label, ScanResult.status)
        .all()
    )

    summary: dict[str, dict[str, int]] = {}
    for label, status, count in grouped:
        summary.setdefault(label, {})
        summary[label][status] = int(count)

    latest_scan = db.query(func.max(ScanResult.scanned_at)).scalar()
    total_results = db.query(func.count(ScanResult.id)).scalar() or 0
    total_errors = db.query(func.count(ScanResult.id)).filter(ScanResult.status != "OK").scalar() or 0
    return {
        "by_target": summary,
        "last_scan": latest_scan.isoformat() if isinstance(latest_scan, datetime) else None,
        "total_results": int(total_results),
        "total_errors": int(total_errors),
    }


@router.get("/diagnostics")
def get_diagnostics(db: Session = Depends(get_db)) -> dict:
    total_results = int(db.query(func.count(ScanResult.id)).scalar() or 0)
    total_targets = int(db.query(func.count(ScanTarget.id)).scalar() or 0)
    enabled_targets = int(
        db.query(func.count(ScanTarget.id)).filter(ScanTarget.enabled.is_(True)).scalar() or 0
    )
    latest_result = db.query(ScanResult).order_by(ScanResult.scanned_at.desc()).first()

    return {
        "total_targets": total_targets,
        "enabled_targets": enabled_targets,
        "total_results": total_results,
        "latest_result": {
            "id": latest_result.id,
            "target_id": latest_result.target_id,
            "file_path": latest_result.file_path,
            "status": latest_result.status,
            "scanned_at": latest_result.scanned_at.isoformat(),
        }
        if latest_result
        else None,
    }


@router.post("/{result_id}/rescan")
def rescan_result(result_id: int, db: Session = Depends(get_db)) -> dict:
    result = db.query(ScanResult).filter(ScanResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    file_path = result.file_path
    if not os.path.exists(file_path):
        result.status = "File Missing"
        result.details = "File no longer exists at recorded path"
        result.scan_duration_seconds = 0.0
        result.scanned_at = datetime.utcnow()
        db.commit()
        return {
            "id": result.id,
            "status": result.status,
            "details": result.details,
            "scan_duration_seconds": result.scan_duration_seconds,
            "scanned_at": result.scanned_at.isoformat(),
        }

    started = time.perf_counter()
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

    db.commit()

    return {
        "id": result.id,
        "status": result.status,
        "details": result.details,
        "scan_duration_seconds": result.scan_duration_seconds,
        "scanned_at": result.scanned_at.isoformat(),
    }
