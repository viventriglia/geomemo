"""Local HTTP and SQLite backend for the visited administrative areas map."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "visited.sqlite3"

AREA_LEVELS = {
    "country": {"label": "Country", "zoom": 3},
    "region": {"label": "Region/State", "zoom": 5},
    "province": {"label": "Province/County", "zoom": 8},
    "city": {"label": "City", "zoom": 10},
}

NOMINATIM_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "geomemo-local-app/1.0 (local development)",
}


def init_db() -> None:
    """Create the SQLite schema and apply additive migrations."""
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                display_name TEXT,
                country TEXT,
                country_code TEXT,
                region TEXT,
                state TEXT,
                province TEXT,
                county TEXT,
                city TEXT,
                locality TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_places_country_state
            ON places(country_code, state, province, county)
            """
        )
        ensure_column(conn, "places", "level", "TEXT NOT NULL DEFAULT 'point'")
        ensure_column(conn, "places", "area_name", "TEXT")
        ensure_column(conn, "places", "geometry_geojson", "TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_places_level_country_state
            ON places(level, country_code, state, province, county, city)
            """
        )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column only when an existing local database does not have it yet."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = {key: row[key] for key in row.keys()}
    geometry = data.get("geometry_geojson")
    if isinstance(geometry, str) and geometry:
        try:
            data["geometry_geojson"] = json.loads(geometry)
        except json.JSONDecodeError:
            data["geometry_geojson"] = None
    return data


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("The payload must be a JSON object")
    return payload


def normalize_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid '{field_name}' field") from exc
    if field_name == "lat" and not -90 <= result <= 90:
        raise ValueError("Latitude is out of range")
    if field_name == "lng" and not -180 <= result <= 180:
        raise ValueError("Longitude is out of range")
    return result


def clean_text(value: Any, max_length: int = 2000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_length]


def first_text(*values: Any) -> str:
    for value in values:
        text = clean_text(value, 255)
        if text:
            return text
    return ""


def validate_level(value: Any) -> str:
    level = clean_text(value, 32) or "region"
    if level not in AREA_LEVELS and level != "point":
        raise ValueError("Invalid area level")
    return level


def validate_geojson(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid GeoJSON") from exc
    if not is_area_geometry(value):
        raise ValueError("Geometry must be a GeoJSON Polygon or MultiPolygon")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_area_geometry(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") in {"Polygon", "MultiPolygon"}


def request_nominatim(endpoint: str, params: dict[str, Any], timeout: int = 10) -> Any:
    """Call Nominatim using the project User-Agent and return decoded JSON."""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/{endpoint}?{query}",
        headers=NOMINATIM_HEADERS,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def reverse_geocode(lat: float, lng: float, *, zoom: int | None = None, polygon: bool = False) -> dict[str, Any]:
    """Resolve a coordinate into a Nominatim address, optionally with a boundary."""
    params: dict[str, Any] = {
        "format": "jsonv2",
        "lat": f"{lat:.7f}",
        "lon": f"{lng:.7f}",
        "addressdetails": "1",
        "accept-language": "en",
        "layer": "address",
    }
    if zoom is not None:
        params["zoom"] = str(zoom)
    if polygon:
        params["polygon_geojson"] = "1"
        params["polygon_threshold"] = "0.001"
    return request_nominatim("reverse", params)


def search_nominatim(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Search Nominatim for boundary polygons using structured address fields."""
    filtered = {key: value for key, value in params.items() if value}
    if not any(key in filtered for key in ("q", "street", "city", "county", "state", "country", "postalcode")):
        return []

    query_params = {
        "format": "jsonv2",
        "addressdetails": "1",
        "polygon_geojson": "1",
        "polygon_threshold": "0.001",
        "limit": "5",
        "dedupe": "1",
        "accept-language": "en",
        **filtered,
    }
    results = request_nominatim("search", query_params)
    return results if isinstance(results, list) else []


def area_name_for(level: str, result: dict[str, Any]) -> str:
    address = result.get("address") or {}
    display_name = clean_text(result.get("display_name"))
    display_first = display_name.split(",", 1)[0] if display_name else ""

    if level == "country":
        return first_text(address.get("country"), result.get("name"), display_first)
    if level == "region":
        return first_text(address.get("state"), address.get("region"), address.get("state_district"), result.get("name"), display_first)
    if level == "province":
        return first_text(
            address.get("province"),
            address.get("county"),
            address.get("state_district"),
            address.get("district"),
            result.get("name"),
            display_first,
        )
    if level == "city":
        return first_text(
            address.get("city"),
            address.get("town"),
            address.get("village"),
            address.get("municipality"),
            result.get("name"),
            display_first,
        )
    return first_text(result.get("name"), display_first)


def normalize_area(lat: float, lng: float, level: str, result: dict[str, Any]) -> dict[str, Any]:
    address = result.get("address") or {}
    return {
        "lat": lat,
        "lng": lng,
        "level": level,
        "area_name": area_name_for(level, result),
        "display_name": clean_text(result.get("display_name")),
        "country": clean_text(address.get("country"), 255),
        "country_code": clean_text(address.get("country_code"), 16).upper(),
        "region": first_text(address.get("region"), address.get("state_district")),
        "state": clean_text(address.get("state"), 255),
        "province": first_text(address.get("province"), address.get("city_district")),
        "county": first_text(address.get("county"), address.get("district")),
        "city": first_text(address.get("city"), address.get("town"), address.get("village"), address.get("municipality")),
        "locality": first_text(address.get("suburb"), address.get("neighbourhood"), address.get("hamlet")),
        "geometry_geojson": result.get("geojson"),
    }


def search_candidates(level: str, address: dict[str, Any], fallback_name: str) -> list[dict[str, Any]]:
    country = address.get("country")
    state = first_text(address.get("state"), address.get("region"), address.get("state_district"))
    county = first_text(address.get("province"), address.get("county"), address.get("state_district"), address.get("district"))
    city = first_text(address.get("city"), address.get("town"), address.get("village"), address.get("municipality"))

    if level == "country":
        return [{"country": country}, {"q": country or fallback_name}]
    if level == "region":
        return [{"state": state, "country": country}, {"q": ", ".join(part for part in [state, country] if part)}]
    if level == "province":
        return [
            {"county": county, "state": state, "country": country},
            {"q": ", ".join(part for part in [county, state, country] if part)},
        ]
    if level == "city":
        return [
            {"city": city, "county": county, "state": state, "country": country},
            {"q": ", ".join(part for part in [city, county, state, country] if part)},
        ]
    return [{"q": fallback_name}]


def lookup_area(lat: float, lng: float, level: str) -> dict[str, Any]:
    if level not in AREA_LEVELS:
        raise ValueError("Invalid area level")

    # Reverse lookup usually returns the right administrative boundary. Search is
    # kept as a fallback for cities and provinces that resolve to place nodes.
    reverse_result = reverse_geocode(lat, lng, zoom=AREA_LEVELS[level]["zoom"], polygon=True)
    if is_area_geometry(reverse_result.get("geojson")):
        return normalize_area(lat, lng, level, reverse_result)

    address = reverse_result.get("address") or {}
    fallback_name = clean_text(reverse_result.get("display_name"))
    for candidate in search_candidates(level, address, fallback_name):
        for result in search_nominatim(candidate):
            if is_area_geometry(result.get("geojson")):
                return normalize_area(lat, lng, level, result)

    label = AREA_LEVELS[level]["label"]
    raise ValueError(f"No boundary was found for this level ({label}). Try a broader level.")


class AppHandler(BaseHTTPRequestHandler):
    server_version = "GeoMemo/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def send_json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/places":
            self.handle_list_places()
            return
        if parsed.path == "/api/area":
            self.handle_area_lookup(parsed.query)
            return
        if parsed.path == "/api/reverse":
            self.handle_reverse(parsed.query)
            return
        self.handle_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/places":
            self.handle_create_place()
            return
        self.send_error_json(404, "Endpoint not found")

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/places/"):
            self.handle_delete_place(parsed.path)
            return
        self.send_error_json(404, "Endpoint not found")

    def handle_list_places(self) -> None:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM places
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        self.send_json(200, [row_to_dict(row) for row in rows])

    def handle_reverse(self, raw_query: str) -> None:
        params = urllib.parse.parse_qs(raw_query)
        try:
            lat = normalize_float(params.get("lat", [None])[0], "lat")
            lng = normalize_float(params.get("lng", [None])[0], "lng")
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return

        try:
            self.send_json(200, reverse_geocode(lat, lng))
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.send_error_json(502, f"Geocoding is unavailable: {exc}")

    def handle_area_lookup(self, raw_query: str) -> None:
        params = urllib.parse.parse_qs(raw_query)
        try:
            lat = normalize_float(params.get("lat", [None])[0], "lat")
            lng = normalize_float(params.get("lng", [None])[0], "lng")
            level = validate_level(params.get("level", ["region"])[0])
            if level == "point":
                raise ValueError("Select a geographic level")
            area = lookup_area(lat, lng, level)
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.send_error_json(502, f"Geocoding is unavailable: {exc}")
            return

        self.send_json(200, area)

    def handle_create_place(self) -> None:
        try:
            payload = read_json(self)
            lat = normalize_float(payload.get("lat"), "lat")
            lng = normalize_float(payload.get("lng"), "lng")
            level = validate_level(payload.get("level", "point"))
            geometry_geojson = validate_geojson(payload.get("geometry_geojson"))
            if level != "point" and not geometry_geojson:
                raise ValueError("Area geometry is missing")
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return

        fields = {
            "level": level,
            "area_name": clean_text(payload.get("area_name"), 255),
            "display_name": clean_text(payload.get("display_name")),
            "country": clean_text(payload.get("country"), 255),
            "country_code": clean_text(payload.get("country_code"), 16).upper(),
            "region": clean_text(payload.get("region"), 255),
            "state": clean_text(payload.get("state"), 255),
            "province": clean_text(payload.get("province"), 255),
            "county": clean_text(payload.get("county"), 255),
            "city": clean_text(payload.get("city"), 255),
            "locality": clean_text(payload.get("locality"), 255),
            "notes": clean_text(payload.get("notes")),
            "geometry_geojson": geometry_geojson,
        }

        with db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO places (
                    lat, lng, level, area_name, display_name, country, country_code,
                    region, state, province, county, city, locality, notes,
                    geometry_geojson
                )
                VALUES (
                    :lat, :lng, :level, :area_name, :display_name, :country,
                    :country_code, :region, :state, :province, :county, :city,
                    :locality, :notes, :geometry_geojson
                )
                """,
                {"lat": lat, "lng": lng, **fields},
            )
            row = conn.execute("SELECT * FROM places WHERE id = ?", (cursor.lastrowid,)).fetchone()
        self.send_json(201, row_to_dict(row))

    def handle_delete_place(self, path: str) -> None:
        try:
            place_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_error_json(400, "Invalid ID")
            return

        with db() as conn:
            cursor = conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
        if cursor.rowcount == 0:
            self.send_error_json(404, "Place not found")
            return
        self.send_json(200, {"ok": True})

    def handle_static(self, url_path: str) -> None:
        if url_path == "/":
            url_path = "/index.html"
        relative_path = urllib.parse.unquote(url_path).lstrip("/")
        file_path = (PUBLIC_DIR / relative_path).resolve()

        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            self.send_error_json(403, "Path is not allowed")
            return

        if not file_path.is_file():
            self.send_error_json(404, "File not found")
            return

        content_type, _ = mimetypes.guess_type(file_path.name)
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="GeoMemo local visited places map")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    init_db()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"App running at http://{args.host}:{args.port}")
    print(f"SQLite database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
