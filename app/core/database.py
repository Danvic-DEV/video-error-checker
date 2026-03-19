from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.models import Base, Setting


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


DEFAULT_SETTINGS = {
    "general_discord_webhook": "",
    "failed_discord_webhook": "",
    "scan_interval_seconds": "3600",
    "video_extensions": ".mp4,.mkv,.avi,.mov,.flv,.wmv",
    "gpu_enabled": "false",
    "gpu_backend": "auto",
    "gpu_device_id": "0",
}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        for key, value in DEFAULT_SETTINGS.items():
            existing = session.query(Setting).filter(Setting.key == key).first()
            if not existing:
                session.add(Setting(key=key, value=value))
        session.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
