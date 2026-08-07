# CalmRoute — Melbourne CBD Navigation

This VS Code Flask application follows the four acceptance criteria:

1. The user can drag and zoom the Melbourne CBD map.
2. The user can switch between light and dark map modes.
3. After a start and destination are selected and a route is generated, nearby safe spaces appear.
4. After the route is generated, predicted crowd alerts appear as animated area coverage rather than point markers.

## Original datasets

The five source CSV files are retained separately in `data/` with their original filenames. The application does not create replacement or merged CSV files. At runtime, the Flask API temporarily matches rows by `Location_ID` in memory so the map can display predictions; the source files themselves are never changed.

## Run in VS Code (macOS)

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
