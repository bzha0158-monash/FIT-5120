# SilentWaze— Melbourne CBD Navigation

This VS Code Flask application follows the four acceptance criteria:

1. The user can drag and zoom the Melbourne CBD map.
2. The user can switch between light and dark map modes.
3. After a start and destination are selected and a route is generated, nearby safe spaces appear.
4. After the route is generated, predicted crowd alerts appear as animated area coverage rather than point markers.

## Original datasets

The application currently reads five source CSV files from `data/`. The CSV
files are not stored in Git because one of them exceeds GitHub's normal file
size limit. Obtain the original datasets from the team and place them in
`data/` using the filenames referenced in `app.py` before running locally.

At runtime, the Flask API temporarily matches rows by `Location_ID` in memory
so the map can display predictions; the source files themselves are never
changed. In the planned AWS architecture, these local CSV files will be
replaced by PostgreSQL/PostGIS on Amazon RDS and an automated data pipeline.

## Run in VS Code

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The first request to `/api/crowd` may take several seconds because the historical source CSV is large and is aggregated directly from the original file. The result is cached in memory while the server remains running.

## Internet requirement

OpenStreetMap/CARTO map tiles, location search and the walking route service are online services, so an internet connection is required.
