#!/usr/bin/env python3
"""Local web UI for the Pochven filament router.

Stdlib only: a threaded HTTP server that serves ./web and a small JSON API
backed by the same modules the CLI uses (cache, gate graph, BFS).

    python web.py                 # http://127.0.0.1:8770
    python web.py --port 9000 --no-browser

The page lets you pick a target system, filter by minimum exit security, sort
the origin ranking by any column, and flip between the grouped and flat views.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pochven_filaments as pf  # noqa: E402

WEB_DIR = HERE / "web"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.payload = {"error": message, **extra}


class App:
    """Everything the request handler needs, loaded once at startup."""

    def __init__(self, sde_dir: Path, cache_path: Path):
        self.cache = pf.get_cache(cache_path, sde_dir)
        self.systems = pf.load_systems(sde_dir)
        self.regions = pf.load_region_names(sde_dir)
        self.adj = pf.build_graph(sde_dir, self.systems)
        self._distances: dict[int, dict[int, int]] = {}
        self._lock = threading.Lock()
        self.system_names = sorted(
            s["name"]
            for s in self.systems.values()
            if s["region_id"] < pf.WORMHOLE_REGION_MIN
            and s["region_id"] not in {pf.POCHVEN_REGION_ID, pf.EXORDIUM_REGION_ID, *pf.JOVE_REGION_IDS}
        )

    def distances(self, system_id: int) -> dict[int, int]:
        with self._lock:
            cached = self._distances.get(system_id)
        if cached is None:
            cached = pf.bfs(self.adj, system_id)
            with self._lock:
                self._distances[system_id] = cached
        return cached

    def route(self, query: str, min_sec: float | None) -> dict:
        target, suggestions = pf.resolve_system(query, self.systems)
        if target is None:
            raise ApiError(
                404,
                f"unknown system {query!r}",
                suggestions=suggestions,
            )
        if target["region_id"] in pf.TRANSIT_EXCLUDED_REGIONS:
            raise ApiError(
                400,
                f"{target['name']} is in {pf.region_label(target['region_id'], self.regions)}, "
                "which has no usable stargate connection to K-space.",
            )
        if target["region_id"] >= pf.WORMHOLE_REGION_MIN:
            raise ApiError(400, f"{target['name']} is in wormhole space; pick a K-space destination.")

        dist = self.distances(target["id"])
        origins, unreachable = pf.compute_origins(self.cache, dist, min_sec)
        origins = pf.sort_origins(origins, "min")

        return {
            "target": {
                "name": target["name"],
                "id": target["id"],
                "region": self.regions.get(target["region_id"], ""),
                "security": round(target["security"], 6),
            },
            "min_sec": min_sec,
            "origins": [
                {
                    "name": o["name"],
                    "id": o["id"],
                    "constellation": o.get("constellation", ""),
                    "min_jumps": o["min_jumps"],
                    "avg_jumps": round(o["avg_jumps"], 3),
                    "max_jumps": o["max_jumps"],
                    "exits": o["exits"],
                }
                for o in origins
            ],
            "meta": {
                "generated_utc": self.cache.get("generated_utc", ""),
                "ly_meters": pf.LY_METERS,
                "range_ly": pf.FILAMENT_RANGE_LY,
                "reachable_systems": len(dist),
                "exit_systems": len({e["id"] for o in origins for e in o["exits"]}),
                "exit_pairs": sum(len(o["exits"]) for o in origins),
                "unreachable": unreachable,
            },
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "PochvenWeb/1.0"

    # -- plumbing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        try:
            self._dispatch()
        except BrokenPipeError:
            pass

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        app: App = self.server.app  # type: ignore[attr-defined]

        if path in ("/", "/index.html"):
            return self._file("index.html")
        if path == "/app.js":
            return self._file("app.js")
        if path == "/style.css":
            return self._file("style.css")
        if path == "/api/systems":
            return self._json({"systems": app.system_names, "count": len(app.system_names)})
        if path == "/api/route":
            return self._route(app, parse_qs(parsed.query))
        return self._json({"error": "not found"}, 404)

    # -- responses --------------------------------------------------------

    def _file(self, name: str) -> None:
        path = WEB_DIR / name
        if not path.exists():
            return self._json({"error": f"missing {name}"}, 500)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _route(self, app: App, query: dict[str, list[str]]) -> None:
        target = (query.get("target") or [""])[0].strip()
        if not target:
            return self._json({"error": "missing target parameter"}, 400)
        raw_min = (query.get("min_sec") or [""])[0].strip()
        min_sec = None
        if raw_min:
            try:
                min_sec = float(raw_min)
            except ValueError:
                return self._json({"error": f"min_sec must be a number, got {raw_min!r}"}, 400)
            if not -1.0 <= min_sec <= 1.0:
                return self._json({"error": "min_sec must be between -1 and 1"}, 400)
        try:
            return self._json(app.route(target, min_sec))
        except ApiError as exc:
            return self._json(exc.payload, exc.status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local web UI for the Pochven filament router.")
    parser.add_argument("--port", type=int, default=8770, help="port to listen on (default 8770)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--sde-dir", type=Path, default=pf.DEFAULT_SDE_DIR)
    parser.add_argument("--cache", type=Path, default=pf.DEFAULT_CACHE)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)

    app = App(args.sde_dir, args.cache)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]
    server.verbose = args.verbose  # type: ignore[attr-defined]
    server.daemon_threads = True

    url = f"http://{args.host}:{args.port}/"
    counts = app.cache["counts"]
    print("Pochven filament router")
    print(f"  {url}")
    print(
        f"  {counts['pochven_systems']} origins, {counts['exit_systems']} exit systems, "
        f"{counts['exit_pairs']} exit pairs"
    )
    print("  Ctrl+C to stop")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
