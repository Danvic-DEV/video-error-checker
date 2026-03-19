# Video Error Checker

Dockerized video integrity scanner with a FastAPI backend, React UI, embedded PostgreSQL, and ffmpeg/ffprobe-based checks.

## What this project does

- Scans video libraries mounted under `/media`
- Stores scan targets, results, and settings in PostgreSQL persisted under `/config`
- Supports scheduled scans and manual scans
- Automatically runs a startup scan when enabled targets exist
- Tracks progress and live scan activity
- Lets you rescan individual failed files from the Results page

## Architecture

Single container:

- **Backend:** FastAPI (`app/`)
- **UI:** React + Vite (`ui-react/`, built into `app/ui/static/app/`)
- **Database:** PostgreSQL running inside the same container
- **Scanner:** ffmpeg + ffprobe CLI checks

Persistent volume:

- `/config/postgres` → PostgreSQL data

Media mount (read-only):

- `/media` → your video library

## Features

- Scan target management (add/update/delete/enable)
- Folder browser for `/media`
- Full scan trigger (`Run Scan Now`)
- Progress status (`files_done`, `files_total`, current file/target)
- Live scan activity log
- GPU offload settings (global) with backend/device selection
- In-app GPU diagnostics panel and runtime probe results
- Dashboard summary and DB diagnostics
- Results table with "Errors only" filter
- Per-result **Rescan** action for failed rows

## Video checks currently implemented

For each file:

1. `ffmpeg -v error -i <file> -f null -` (hard decode/container errors)
2. `ffprobe` validation of a primary video stream
3. Conservative playback-artifact heuristics:
   - strong timestamp anomalies from warning scan
   - large A/V duration drift

Possible statuses include:

- `OK`
- `Corruption Detected`
- `Stream Issues`
- `Playback Artifacts Suspected`
- `File Missing` (for manual rescans where file no longer exists)

## Requirements

- Docker + Docker Compose
- Host path containing your videos

Optional for local non-container work:

- Python 3.11+
- Node.js 18+

## Quick start (Docker)

From repository root:

```bash
docker compose up -d --build
```

GPU-enabled startup (NVIDIA):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Open UI:

- `http://localhost:8080`

### Important mount note (macOS)

If `/media` is not a valid shared path on your Mac, set `MEDIA_PATH` to an allowed host path:

```bash
MEDIA_PATH=/Users/yourname docker compose up -d --build
```

The app still sees this as `/media` inside the container.

## docker-compose configuration

Current compose file uses:

- Port: `8080:8080`
- Env:
  - `WEB_PORT=8080`
  - `TZ=Europe/London`
  - `PUID=1000`
  - `PGID=1000`
- Volumes:
  - `./config:/config`
  - `${MEDIA_PATH:-/media}:/media:ro`

Optional GPU override file:

- `docker-compose.gpu.yml`
- Enables NVIDIA runtime settings (`gpus: all`, `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES`)
- Includes commented `/dev/dri` mapping guidance for Intel/AMD VAAPI/QSV hosts

## Unraid deployment notes

Recommended container variables:

- `PUID=99`
- `PGID=100`
- `WEB_PORT=8080`
- `TZ=Europe/London`

Mappings:

- AppData → `/config` (RW)
- Media share → `/media` (RO)
- Port `8080` host ↔ container `8080`

The entrypoint self-heals PostgreSQL auth/bootstrap on startup and reconciles DB access each run.

## First-time setup in UI

1. Open **Scan Targets**
2. Add a target under `/media` (or use **Browse**)
3. Go to **Dashboard** and click **Run Scan Now**
4. Monitor progress/log tiles
5. Review **Results** and use **Rescan** on failures

## Data persistence behavior

Persistent data is stored in PostgreSQL under `/config/postgres`.

- Targets persist across restarts
- Results persist across restarts
- Settings persist across restarts

Live in-memory runtime fields (e.g., current in-flight scan state) reset on restart, but persisted summary and results remain.

## API reference (implemented endpoints)

### Health/UI

- `GET /health`
- `GET /`
- `GET /assets/{asset_path}`

### Settings

- `GET /api/settings`
- `PUT /api/settings`

### GPU

- `GET /api/gpu/discovery`
- `GET /api/gpu/diagnostics`

### Targets

- `GET /api/targets`
- `POST /api/targets`
- `PUT /api/targets/{target_id}`
- `DELETE /api/targets/{target_id}`
- `GET /api/targets/browse?path=...`

### Scan control/status

- `POST /api/scan/trigger`
- `GET /api/scan/status`

### Results

- `GET /api/results`
- `GET /api/results/summary`
- `GET /api/results/diagnostics`
- `POST /api/results/{result_id}/rescan`

## Development

### Backend dependencies

Installed from `requirements.txt`:

- fastapi
- uvicorn
- sqlalchemy
- psycopg2-binary
- apscheduler
- requests
- pydantic

### Frontend

```bash
cd ui-react
npm install
npm run build
```

The Docker build automatically compiles UI and copies Vite output into `app/ui/static/app/`.

## CI/CD

GitHub Actions workflow:

- `.github/workflows/docker-publish.yml`
- Builds and pushes image to GHCR:
  - `ghcr.io/renegadeuk/video-error-checker`
- Includes retry logic for transient build/push failures

## Troubleshooting

### UI shows no updates or blank module errors

- Ensure UI assets are being served from `/assets/...`
- Rebuild container to refresh static UI bundle

### `Persisted rows` looks wrong

- Check `GET /api/results/diagnostics`
- Verify container is using expected `/config` volume
- Confirm you’re running latest image/tag

### Docker on macOS: mount denied for `/media`

- Use `MEDIA_PATH=/Users/...` and re-run compose
- Ensure path is shared in Docker Desktop settings

### Startup scan did not run

- Ensure at least one target is enabled
- Check live scan log panel for startup entries

### Results unexpectedly all failing

- New playback heuristics were made conservative, but you can still inspect details text in each result row
- Use per-row **Rescan** after adjusting settings/thresholds in future versions

### GPU offload fails to initialize

- Open **Settings** and review the GPU diagnostics panel
- Click **Refresh GPU Diagnostics** after changing container/runtime config
- If CUDA is selected and diagnostics show missing `libnvcuvid`, ensure NVIDIA video capability mapping is enabled for the container
- For VAAPI/QSV, ensure `/dev/dri` is mapped and the selected render node exists in the container
- Keep GPU offload enabled with CPU fallback behavior if intermittent initialization failures occur

## Known limitations

- No strictness toggle yet for playback artifact sensitivity (single conservative mode)
- No distributed workers (single-process scan execution)
- No auth/multi-user model (single-user local tool)

## Project structure

```text
video-error-checker/
  app/
    api/
    core/
    ui/
    main.py
  ui-react/
  config/
  Dockerfile
  docker-compose.yml
  entrypoint.sh
  requirements.txt
```

## License

No license file is currently included in this repository.
