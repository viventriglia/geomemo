# GeoMemo

GeoMemo is a small web app for marking visited areas on a map.

The app lets you choose an administrative level, click the map, preview the selected boundary, add optional notes, and save everything to a local SQLite database.

## Requirements

- Python 3.10+
- Internet access for map tiles, place names, and boundary GeoJSON from OpenStreetMap/Nominatim

## Dependency Management

Python runtime dependencies are intentionally empty: the backend uses only the Python standard library (`http.server`, `sqlite3`, and `urllib`).

Frontend/runtime services are loaded remotely:

- Leaflet, from CDN
- CARTO/OpenStreetMap raster tiles
- Nominatim reverse geocoding and administrative GeoJSON lookup

## Setup

```powershell
poetry lock
poetry install --sync
```

## Run

```powershell
poetry run python app.py
```

Open:

```text
http://127.0.0.1:8000
```

The SQLite database is created at:

```text
data/visited.sqlite3
```

## Usage

1. Choose a level: `Country`, `Region`, `Province`, or `City`.
2. Click the map.
3. Review the highlighted boundary.
4. Add notes, if needed.
5. Save the area.

Use the top-left sun/moon switch to change the map and panel theme. The choice is stored in the browser.

Some smaller places may not have a valid administrative polygon in Nominatim. In that case, choose a broader level such as `Province` or `Region`.
