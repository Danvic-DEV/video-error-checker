from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse


router = APIRouter()
static_root = Path(__file__).resolve().parent / "static" / "app"


@router.get("/assets/{asset_path:path}")
def assets(asset_path: str):
    asset_file = static_root / "assets" / asset_path
    if asset_file.exists() and asset_file.is_file():
        return FileResponse(str(asset_file))
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@router.get("/favicon.svg")
def favicon_svg():
    icon_path = static_root / "favicon.svg"
    if icon_path.exists() and icon_path.is_file():
        return FileResponse(str(icon_path))
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@router.get("/favicon.ico")
def favicon_ico():
    icon_path = static_root / "favicon.ico"
    if icon_path.exists() and icon_path.is_file():
        return FileResponse(str(icon_path))
    svg_path = static_root / "favicon.svg"
    if svg_path.exists() and svg_path.is_file():
        return FileResponse(str(svg_path))
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@router.get("/")
def index():
    index_path = static_root / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"message": "UI build not found. Build ui-react and copy dist to app/ui/static/app."})


@router.get("/{full_path:path}")
def spa_catch_all(full_path: str):
    if (
        full_path.startswith("api/")
        or full_path.startswith("assets/")
        or full_path == "favicon.svg"
        or full_path == "favicon.ico"
    ):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    index_path = static_root / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"message": "UI build not found."}, status_code=404)
