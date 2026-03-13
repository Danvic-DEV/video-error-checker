from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.results import router as results_router
from app.api.scan import router as scan_router
from app.api.settings import router as settings_router
from app.api.targets import router as targets_router
from app.core.database import SessionLocal, init_db
from app.core.models import ScanTarget, Setting
from app.core.scheduler import add_system_log, scheduler, start_scheduler, trigger_startup_scan
from app.ui.ui_routes import router as ui_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        interval_row = session.query(Setting).filter(Setting.key == "scan_interval_seconds").first()
        interval_seconds = int(interval_row.value) if interval_row else 3600
        enabled_targets = (
            session.query(ScanTarget).filter(ScanTarget.enabled.is_(True)).count()
        )
    start_scheduler(interval_seconds)
    if enabled_targets > 0:
        trigger_startup_scan()
    else:
        add_system_log("info", "Container started with no enabled scan targets")
    yield
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
