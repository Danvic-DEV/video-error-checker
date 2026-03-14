from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.results import router as results_router
from app.api.scan import router as scan_router
from app.api.settings import router as settings_router
from app.api.targets import router as targets_router
from app.core.database import SessionLocal, init_db
from app.core.models import ScanResult, ScanTarget, Setting
from app.core.scheduler import (
    add_system_log,
    enqueue_rescan,
    scheduler,
    start_rescan_worker,
    start_scheduler,
    stop_rescan_worker,
    trigger_startup_scan,
)
from app.ui.ui_routes import router as ui_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    pending_rescan_ids: list[int] = []
    with SessionLocal() as session:
        pending_rescans = (
            session.query(ScanResult)
            .filter(ScanResult.status.in_(["Rescanning", "Rescan Queued"]))
            .order_by(ScanResult.scanned_at.asc(), ScanResult.id.asc())
            .all()
        )
        for row in pending_rescans:
            row.status = "Rescan Queued"
            row.details = "Queued for manual rescan (restored after restart)"
            pending_rescan_ids.append(row.id)
        if pending_rescans:
            session.commit()

        interval_row = session.query(Setting).filter(Setting.key == "scan_interval_seconds").first()
        interval_seconds = int(interval_row.value) if interval_row else 3600
        enabled_targets = (
            session.query(ScanTarget).filter(ScanTarget.enabled.is_(True)).count()
        )
    start_scheduler(interval_seconds)
    start_rescan_worker()

    restored = 0
    for result_id in pending_rescan_ids:
        enqueue_state = enqueue_rescan(result_id)
        if enqueue_state in {"started", "queued"}:
            restored += 1
    if restored > 0:
        add_system_log("info", f"Restored {restored} pending rescans after restart")

    if enabled_targets > 0:
        trigger_startup_scan()
    else:
        add_system_log("info", "Container started with no enabled scan targets")
    yield
    stop_rescan_worker()
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Video Error Checker", lifespan=lifespan)

app.include_router(settings_router)
app.include_router(targets_router)
app.include_router(results_router)
app.include_router(scan_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(ui_router)
