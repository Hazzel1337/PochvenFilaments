#!/usr/bin/env python3
"""Pochven filament extraction router for EVE Online.

Given a target K-space system, rank the 27 Pochven systems by how few stargate
jumps away their 2.5 LY filament exits are.

Data
----
EVE Static Data Export (SDE), Fuzzwork CSV dump:
  mapSolarSystems.csv     - id, name, region, security, x/y/z (metres)
  mapSolarSystemJumps.csv - stargate graph (one row per directed edge)
  mapRegions.csv          - region names

Math
----
Pochven systems keep their original pre-invasion coordinates, so the Euclidean
distance between a Pochven system P and a K-space system K is

    d_ly = hypot(xP-xK, yP-yK, zP-zK) / 9_460_730_472_580_800

which is the exact 1 light-year constant.  Proximity-5 and Proximity-15
'Extraction' filaments deliver within 2.5 LY, so every pair with d_ly <= 2.5 is
a legal exit.

Filters
-------
Candidate exits must be gate-connected K-space: regionID < 11_000_000 and not
Pochven (10000070), the three Jove regions (10000004, 10000017, 10000019),
Zarzakh (10001000) or Exordium (10001004).  Wormhole regions are all >= 11e6.

Zarzakh deserves a note: it is NOT isolated - it has four ordinary stargates
(H-PA29, Turnur, G-0Q86, Alsavoinon) - so it stays in the *gate graph* and can
legitimately be flown through.  It is still excluded from *filament destinations*
because it is a special-access system, so the two region sets below differ.

Usage
-----
    python pochven_filaments.py fetch      # download the three CSVs
    python pochven_filaments.py build      # cache 2.5 LY exits -> pochven_exits.json
    python pochven_filaments.py route Jita # ranked extraction table
    python pochven_filaments.py verify     # sanity-check cache + SDE

No third-party packages: only the Python standard library.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import difflib
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

LY_METERS = 9_460_730_472_580_800.0
FILAMENT_RANGE_LY = 2.5
FILAMENT_RANGE_METERS = FILAMENT_RANGE_LY * LY_METERS
FILAMENT_RANGE_SQ = FILAMENT_RANGE_METERS * FILAMENT_RANGE_METERS

POCHVEN_REGION_ID = 10000070
JOVE_REGION_IDS = frozenset({10000004, 10000017, 10000019})
ZARZAKH_REGION_ID = 10001000
EXORDIUM_REGION_ID = 10001004
WORMHOLE_REGION_MIN = 11_000_000

# Regions that cannot appear in the filament exit pool.
CANDIDATE_EXCLUDED_REGIONS = frozenset(
    {POCHVEN_REGION_ID, ZARZAKH_REGION_ID, EXORDIUM_REGION_ID, *JOVE_REGION_IDS}
)

# Regions excluded from the stargate graph.  Zarzakh is intentionally absent:
# its four stargates are usable, so gate routes may pass through it.
TRANSIT_EXCLUDED_REGIONS = frozenset(
    {POCHVEN_REGION_ID, EXORDIUM_REGION_ID, *JOVE_REGION_IDS}
)

SDE_FILES = ("mapSolarSystems.csv", "mapSolarSystemJumps.csv", "mapRegions.csv")
CACHE_SCHEMA = 1
USER_AGENT = "pochven-filaments/1.0 (+local EVE tool)"

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SDE_DIR = BASE_DIR / "sde"
DEFAULT_CACHE = BASE_DIR / "pochven_exits.json"

URL_SOURCES = (
    "https://www.fuzzwork.co.uk/dump/latest/csv/{name}",
    "https://www.fuzzwork.co.uk/dump/latest/{name}.bz2",
    "https://www.fuzzwork.co.uk/dump/latest/{name}",
)

COMMANDS = ("fetch", "build", "route", "verify")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a Fuzzwork CSV.  Their files carry a UTF-8 BOM, hence utf-8-sig."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        raise SystemExit(
            f"error: {path} not found.  Run 'python {Path(__file__).name} fetch' first."
        ) from None


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fmt_sec(value: float) -> str:
    """EVE-style one-decimal security, without the '-0.0' artefact."""
    if abs(value) < 0.05:
        return "0.0"
    return f"{value:.1f}"


def fmt_int(value: int) -> str:
    return f"{value:,}"


# --------------------------------------------------------------------------
# SDE loading
# --------------------------------------------------------------------------

def load_systems(sde_dir: Path) -> dict[int, dict]:
    """solarSystemID -> {id, name, region_id, security, x, y, z}."""
    rows = read_csv_rows(sde_dir / "mapSolarSystems.csv")
    systems: dict[int, dict] = {}
    for row in rows:
        try:
            sid = int(row["solarSystemID"])
            systems[sid] = {
                "id": sid,
                "name": row["solarSystemName"],
                "region_id": int(row["regionID"]),
                "security": float(row["security"]),
                "x": float(row["x"]),
                "y": float(row["y"]),
                "z": float(row["z"]),
            }
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"error: malformed row in mapSolarSystems.csv: {exc}") from None
    if not systems:
        raise SystemExit("error: mapSolarSystems.csv contained no systems.")
    return systems


def load_region_names(sde_dir: Path) -> dict[int, str]:
    path = sde_dir / "mapRegions.csv"
    if not path.exists():
        return {}
    names: dict[int, str] = {}
    for row in read_csv_rows(path):
        try:
            names[int(row["regionID"])] = row["regionName"]
        except (KeyError, ValueError):
            continue
    return names


def region_label(region_id: int, region_names: dict[int, str]) -> str:
    return region_names.get(region_id) or f"region {region_id}"


def distance_ly(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"], a["z"] - b["z"]) / LY_METERS


# --------------------------------------------------------------------------
# Phase 1: build the 2.5 LY extraction cache
# --------------------------------------------------------------------------

def build_cache(sde_dir: Path) -> dict:
    systems = load_systems(sde_dir)
    region_names = load_region_names(sde_dir)

    pochven = sorted(
        (s for s in systems.values() if s["region_id"] == POCHVEN_REGION_ID),
        key=lambda s: s["name"].lower(),
    )
    candidates = [
        s
        for s in systems.values()
        if s["region_id"] < WORMHOLE_REGION_MIN
        and s["region_id"] not in CANDIDATE_EXCLUDED_REGIONS
    ]

    # Pochven coordinates are their original pre-invasion positions, which is
    # exactly what makes this distance meaningful.
    cache_pochven = []
    for p in pochven:
        exits = []
        px, py, pz = p["x"], p["y"], p["z"]
        for k in candidates:
            dx = px - k["x"]
            dy = py - k["y"]
            dz = pz - k["z"]
            d2 = dx * dx + dy * dy + dz * dz
            if d2 > FILAMENT_RANGE_SQ:
                continue
            exits.append(
                {
                    "id": k["id"],
                    "name": k["name"],
                    "region_id": k["region_id"],
                    "region": region_label(k["region_id"], region_names),
                    "security": round(k["security"], 6),
                    "ly": round(math.sqrt(d2) / LY_METERS, 6),
                }
            )
        exits.sort(key=lambda e: (e["ly"], e["name"].lower()))
        cache_pochven.append(
            {
                "id": p["id"],
                "name": p["name"],
                "x": p["x"],
                "y": p["y"],
                "z": p["z"],
                "exits": exits,
            }
        )

    unique_exits = {e["id"] for p in cache_pochven for e in p["exits"]}
    pairs = sum(len(p["exits"]) for p in cache_pochven)
    if not pochven:
        raise SystemExit(
            "error: no systems with regionID 10000070 (Pochven) found - "
            "is this really the EVE SDE?"
        )
    if not unique_exits:
        raise SystemExit(
            "error: no K-space system within 2.5 LY of Pochven - coordinates are wrong."
        )

    return {
        "schema": CACHE_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "constants": {
            "ly_meters": LY_METERS,
            "filament_range_ly": FILAMENT_RANGE_LY,
        },
        "filters": {
            "pochven_region_id": POCHVEN_REGION_ID,
            "candidate_excluded_regions": {
                str(rid): region_label(rid, region_names)
                for rid in sorted(CANDIDATE_EXCLUDED_REGIONS)
            },
            "transit_excluded_regions": {
                str(rid): region_label(rid, region_names)
                for rid in sorted(TRANSIT_EXCLUDED_REGIONS)
            },
        },
        "sde": {
            "systems": len(systems),
            "regions": len(region_names),
            "files": {
                name: (sde_dir / name).stat().st_size
                for name in SDE_FILES
                if (sde_dir / name).exists()
            },
        },
        "counts": {
            "pochven_systems": len(cache_pochven),
            "exit_systems": len(unique_exits),
            "exit_pairs": pairs,
        },
        "pochven_systems": cache_pochven,
    }


def get_cache(path: Path, sde_dir: Path) -> dict:
    """Load the cache, building it from the SDE on first use."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except FileNotFoundError:
        print(f"exit cache missing; building it from {sde_dir} ...", file=sys.stderr)
        cache = build_cache(sde_dir)
        write_json_atomic(path, cache)
        _print_build_summary(cache, path)
        return cache
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"error: {path} is not valid JSON ({exc}).  Rebuild with 'build --force'."
        ) from None
    if cache.get("schema") != CACHE_SCHEMA:
        raise SystemExit(
            f"error: {path} uses schema {cache.get('schema')}, expected {CACHE_SCHEMA}.  "
            "Rebuild with 'build --force'."
        )
    return cache


def _print_build_summary(cache: dict, path: Path) -> None:
    counts = cache["counts"]
    print(
        f"wrote {path}: {counts['pochven_systems']} Pochven systems, "
        f"{counts['exit_systems']} unique exit systems, {counts['exit_pairs']} exit pairs",
        file=sys.stderr,
    )


# --------------------------------------------------------------------------
# Phase 2: stargate graph and BFS
# --------------------------------------------------------------------------

def build_graph(sde_dir: Path, systems: dict[int, dict]) -> dict[int, set[int]]:
    rows = read_csv_rows(sde_dir / "mapSolarSystemJumps.csv")
    adj: dict[int, set[int]] = {}
    for row in rows:
        try:
            a = int(row["fromSolarSystemID"])
            b = int(row["toSolarSystemID"])
        except (KeyError, ValueError):
            continue
        if a == b or a not in systems or b not in systems:
            continue
        if (
            systems[a]["region_id"] in TRANSIT_EXCLUDED_REGIONS
            or systems[b]["region_id"] in TRANSIT_EXCLUDED_REGIONS
        ):
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    if not adj:
        raise SystemExit("error: mapSolarSystemJumps.csv produced an empty gate graph.")
    return adj


def bfs(adj: dict[int, set[int]], start: int) -> dict[int, int]:
    """Unweighted shortest gate jumps from start (BFS; networkx not needed)."""
    dist = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        nxt = dist[node] + 1
        for neighbour in adj.get(node, ()):
            if neighbour not in dist:
                dist[neighbour] = nxt
                queue.append(neighbour)
    return dist


def resolve_system(query: str, systems: dict[int, dict]) -> tuple[dict | None, list[str]]:
    """Name (case-insensitive) or numeric id -> system dict, plus suggestions."""
    q = query.strip()
    if q.isdigit():
        hit = systems.get(int(q))
        if hit is not None:
            return hit, []
    low = q.lower()
    exact = [s for s in systems.values() if s["name"].lower() == low]
    if len(exact) == 1:
        return exact[0], []
    names = [s["name"] for s in systems.values()]
    suggestions = difflib.get_close_matches(q, names, n=4, cutoff=0.55)
    return None, suggestions


def compute_origins(
    cache: dict,
    dist: dict[int, int],
    min_sec: float | None = None,
) -> tuple[list[dict], int]:
    """Attach jump counts to every exit and rank Pochven origins.

    Returns (origins, unreachable_count).
    """
    origins: list[dict] = []
    unreachable = 0
    for poch in cache["pochven_systems"]:
        exits = []
        for exit_system in poch["exits"]:
            jumps = dist.get(exit_system["id"])
            if jumps is None:
                unreachable += 1
                continue
            if min_sec is not None and exit_system["security"] < min_sec:
                continue
            exits.append({**exit_system, "jumps": jumps})
        if not exits:
            continue
        exits.sort(key=lambda e: (e["jumps"], e["ly"], e["name"].lower()))
        jump_counts = [e["jumps"] for e in exits]
        origins.append(
            {
                "id": poch["id"],
                "name": poch["name"],
                "exits": exits,
                "min_jumps": min(jump_counts),
                "avg_jumps": sum(jump_counts) / len(jump_counts),
                "max_jumps": max(jump_counts),
            }
        )
    return origins, unreachable


def sort_origins(origins: list[dict], mode: str) -> list[dict]:
    if mode == "avg":
        key = lambda o: (o["avg_jumps"], o["min_jumps"], o["max_jumps"], o["name"].lower())
    elif mode == "max":
        key = lambda o: (o["max_jumps"], o["avg_jumps"], o["min_jumps"], o["name"].lower())
    else:  # "min"
        key = lambda o: (o["min_jumps"], o["avg_jumps"], o["max_jumps"], o["name"].lower())
    return sorted(origins, key=key)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_summary(origins: list[dict]) -> list[str]:
    rank_w = len(str(len(origins)))
    name_w = max([16] + [len(o["name"]) for o in origins])
    lines = [
        f"  {'#':>{rank_w}}  {'Pochven origin':<{name_w}}  {'Exits':>5}  {'Min':>4}  {'Avg':>5}  {'Max':>4}",
        f"  {'-' * rank_w}  {'-' * name_w}  {'-' * 5}  {'-' * 4}  {'-' * 5}  {'-' * 4}",
    ]
    for i, o in enumerate(origins, 1):
        lines.append(
            f"  {i:>{rank_w}}  {o['name']:<{name_w}}  {len(o['exits']):>5}  "
            f"{o['min_jumps']:>4}  {o['avg_jumps']:>5.1f}  {o['max_jumps']:>4}"
        )
    return lines


def render_details(origins: list[dict]) -> list[str]:
    name_w = max([16] + [len(e["name"]) for o in origins for e in o["exits"]])
    region_w = max([12] + [len(e["region"]) for o in origins for e in o["exits"]])
    lines: list[str] = []
    for rank, o in enumerate(origins, 1):
        header = (
            f"#{rank}  {o['name']}  -  {len(o['exits'])} exits, "
            f"{o['min_jumps']}-{o['max_jumps']} jumps (avg {o['avg_jumps']:.1f})"
        )
        lines.append(header)
        lines.append(
            f"  {'Jumps':>5}  {'LY':>6}  {'Sec':>5}   {'Exit system':<{name_w}}  Region"
        )
        for e in o["exits"]:
            marker = "  <- target" if e["jumps"] == 0 else ""
            lines.append(
                f"  {e['jumps']:>5}  {e['ly']:>6.3f}  {fmt_sec(e['security']):>5}   "
                f"{e['name']:<{name_w}}  {e['region']}{marker}"
            )
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def render_report(result: dict, top: int, details: bool) -> str:
    origins = result["origins"]
    shown = origins[:top] if top else origins
    target = result["target"]

    lines = [
        f"Target: {target['name']}  ({target['region']}, security {fmt_sec(target['security'])})",
        f"Graph:  {fmt_int(result['reachable_systems'])} gate-connected systems; "
        f"filament radius {FILAMENT_RANGE_LY} LY",
        f"Origins: {len(origins)} Pochven systems / {result['exit_systems']} exit systems / "
        f"{result['exit_pairs']} exit pairs",
    ]
    if result["min_sec"] is not None:
        lines.append(f"Filter: exits with security >= {result['min_sec']:.2f}")
    lines.append("")
    if not origins:
        lines.append("No filament exits matched this target/filter.")
        return "\n".join(lines)

    lines.extend(render_summary(shown))
    if details:
        lines.append("")
        lines.extend(render_details(shown))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _download(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def cmd_fetch(args: argparse.Namespace) -> int:
    sde_dir: Path = args.sde_dir
    sde_dir.mkdir(parents=True, exist_ok=True)
    for name in SDE_FILES:
        target = sde_dir / name
        if target.exists() and not args.force:
            print(f"{name}: already present ({target.stat().st_size:,} bytes), skipping")
            continue
        data = None
        last_error: Exception | None = None
        sources = [pattern.format(name=name) for pattern in URL_SOURCES]
        for url in sources:
            for attempt in range(3):
                try:
                    data = _download(url)
                    break
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    if exc.code == 404:
                        break  # try the next URL shape
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_error = exc
                if data is None:
                    time.sleep(1.5 * (attempt + 1))
            if data is not None:
                break
        if data is None:
            raise SystemExit(f"error: could not download {name}: {last_error}")
        if data[:3] == b"BZh":
            data = bz2.decompress(data)
        if not data:
            raise SystemExit(f"error: {name} downloaded empty")
        header = data.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace").lower()
        if "solarsystemid" not in header and "regionid" not in header:
            raise SystemExit(f"error: {name} does not look like an SDE csv (header: {header[:120]})")
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        print(f"{name}: {len(data):,} bytes -> {target}")
    print("SDE ready.  Next: python pochven_filaments.py build")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    sde_dir: Path = args.sde_dir
    cache = build_cache(sde_dir)
    out: Path = args.out
    write_json_atomic(out, cache)
    _print_build_summary(cache, out)
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    if args.top < 0:
        raise SystemExit("error: --top must be 0 (all) or a positive number.")
    cache = get_cache(args.cache, args.sde_dir)
    systems = load_systems(args.sde_dir)
    region_names = load_region_names(args.sde_dir)
    adj = build_graph(args.sde_dir, systems)

    results: list[dict] = []
    failures = 0
    for index, query in enumerate(args.targets):
        target, suggestions = resolve_system(query, systems)
        if target is None:
            failures += 1
            hint = f"  Did you mean: {', '.join(suggestions)}" if suggestions else ""
            print(f"error: unknown system {query!r}.{hint}", file=sys.stderr)
            continue
        if target["region_id"] in TRANSIT_EXCLUDED_REGIONS:
            failures += 1
            print(
                f"error: {target['name']} is in {region_label(target['region_id'], region_names)}, "
                "which has no usable stargate connection to K-space.",
                file=sys.stderr,
            )
            continue
        if target["region_id"] >= WORMHOLE_REGION_MIN:
            failures += 1
            print(
                f"error: {target['name']} is in wormhole space, which has no stargates. "
                "Pick a K-space destination.",
                file=sys.stderr,
            )
            continue
        dist = bfs(adj, target["id"])
        origins, unreachable = compute_origins(cache, dist, args.min_sec)
        origins = sort_origins(origins, args.sort)
        if unreachable:
            print(
                f"warning: {unreachable} exit(s) are not gate-reachable from {target['name']} "
                "and were skipped",
                file=sys.stderr,
            )
        exit_systems = len({e["id"] for o in origins for e in o["exits"]})
        result = {
            "target": {
                "name": target["name"],
                "id": target["id"],
                "region": region_names.get(target["region_id"], ""),
                "security": round(target["security"], 6),
            },
            "filament_ly": FILAMENT_RANGE_LY,
            "min_sec": args.min_sec,
            "sort": args.sort,
            "reachable_systems": len(dist),
            "exit_systems": exit_systems,
            "exit_pairs": sum(len(o["exits"]) for o in origins),
            "origins": [
                {
                    "name": o["name"],
                    "id": o["id"],
                    "min_jumps": o["min_jumps"],
                    "avg_jumps": round(o["avg_jumps"], 3),
                    "max_jumps": o["max_jumps"],
                    "exits": o["exits"],
                }
                for o in origins
            ],
        }
        results.append(result)
        if not args.json:
            print(render_report(result, args.top, not args.summary))
            if index != len(args.targets) - 1:
                print()

    if args.json:
        payload = results[0] if len(results) == 1 else results
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if failures else 0


def cmd_verify(args: argparse.Namespace) -> int:
    checks: list[tuple[bool, str]] = []

    def check(label: str, fn) -> None:
        try:
            fn()
        except AssertionError as exc:
            checks.append((False, f"{label}: {exc}"))
        except SystemExit as exc:
            checks.append((False, f"{label}: {exc}"))
        else:
            checks.append((True, label))

    cache = get_cache(args.cache, args.sde_dir)
    systems = load_systems(args.sde_dir)
    region_names = load_region_names(args.sde_dir)
    pochven = cache["pochven_systems"]

    def check_pochven_count() -> None:
        assert len(pochven) == 27, f"found {len(pochven)} systems in region 10000070, expected 27"

    def check_unique() -> None:
        names = [p["name"] for p in pochven]
        assert len(names) == len(set(names)), "duplicate Pochven system names"
        for p in pochven:
            ids = [e["id"] for e in p["exits"]]
            assert len(ids) == len(set(ids)), f"{p['name']} lists an exit twice"

    def check_counts() -> None:
        pairs = sum(len(p["exits"]) for p in pochven)
        unique = {e["id"] for p in pochven for e in p["exits"]}
        assert pairs == cache["counts"]["exit_pairs"], (
            f"cache says {cache['counts']['exit_pairs']} pairs, recomputed {pairs}"
        )
        assert len(unique) == cache["counts"]["exit_systems"], (
            f"cache says {cache['counts']['exit_systems']} exits, recomputed {len(unique)}"
        )

    def check_distances() -> None:
        for p in pochven:
            assert p["exits"], f"{p['name']} has no exits at all"
            for e in p["exits"]:
                assert e["ly"] <= FILAMENT_RANGE_LY + 1e-9, f"{p['name']}->{e['name']} is {e['ly']} LY"
                actual = distance_ly(p, systems[e["id"]])
                assert abs(actual - e["ly"]) < 1e-6, (
                    f"{p['name']}->{e['name']}: cached {e['ly']}, recomputed {actual:.6f}"
                )

    def check_regions() -> None:
        for p in pochven:
            for e in p["exits"]:
                rid = systems[e["id"]]["region_id"]
                assert rid < WORMHOLE_REGION_MIN, f"{e['name']} is wormhole space"
                assert rid not in CANDIDATE_EXCLUDED_REGIONS, (
                    f"{e['name']} is in {region_label(rid, region_names)}, an excluded region"
                )

    def check_nearest() -> None:
        for p in pochven:
            nearest = min(e["ly"] for e in p["exits"])
            assert nearest < 1.0, f"{p['name']}'s nearest exit is {nearest:.3f} LY"

    def check_known_distance() -> None:
        sakenta = next(p for p in pochven if p["name"] == "Sakenta")
        ikuchi = next(e for e in sakenta["exits"] if e["name"] == "Ikuchi")
        assert abs(ikuchi["ly"] - 0.334) < 0.002, (
            f"Sakenta->Ikuchi is {ikuchi['ly']} LY, expected ~0.334 - wrong coordinates?"
        )

    def check_graph() -> None:
        jumps = args.sde_dir / "mapSolarSystemJumps.csv"
        if not jumps.exists():
            raise AssertionError("mapSolarSystemJumps.csv missing, cannot verify reachability")
        adj = build_graph(args.sde_dir, systems)
        jita = next(s for s in systems.values() if s["name"] == "Jita")
        dist = bfs(adj, jita["id"])
        for p in pochven:
            for e in p["exits"]:
                assert e["id"] in dist, f"{e['name']} is not gate-reachable from Jita"
        assert dist[next(s for s in systems.values() if s["name"] == "Ikuchi")["id"]] == 1, (
            "Jita->Ikuchi should be 1 gate"
        )

    check(f"Pochven system count is 27", check_pochven_count)
    check("system names and exit lists are unique", check_unique)
    check("cached counts match recomputed counts", check_counts)
    check("exit distances <= 2.5 LY and recompute exactly", check_distances)
    check("no wormhole / Jove / Zarzakh / Exordium exits", check_regions)
    check("every Pochven system has an exit within 1 LY", check_nearest)
    check("known pair Sakenta->Ikuchi is ~0.334 LY", check_known_distance)
    check("every exit is gate-reachable (BFS from Jita)", check_graph)

    for ok, label in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    failed = sum(1 for ok, _ in checks if not ok)
    print()
    print(
        f"{len(checks) - failed}/{len(checks)} checks passed; "
        f"{len(pochven)} Pochven systems, "
        f"{cache['counts']['exit_systems']} exit systems, "
        f"{cache['counts']['exit_pairs']} exit pairs"
    )
    return 1 if failed else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pochven_filaments.py",
        description="Rank Pochven filament exits by stargate distance to a target system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python pochven_filaments.py fetch\n"
            "  python pochven_filaments.py build\n"
            "  python pochven_filaments.py route Jita\n"
            "  python pochven_filaments.py route Jita Amarr --sort avg --top 5\n"
            "  python pochven_filaments.py route Jita --min-sec 0.45 --json\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="download the SDE CSVs from Fuzzwork")
    p_fetch.add_argument("--sde-dir", type=Path, default=DEFAULT_SDE_DIR)
    p_fetch.add_argument("--force", action="store_true", help="re-download existing files")
    p_fetch.set_defaults(func=cmd_fetch)

    p_build = sub.add_parser("build", help="compute the 2.5 LY exit cache")
    p_build.add_argument("--sde-dir", type=Path, default=DEFAULT_SDE_DIR)
    p_build.add_argument("--out", type=Path, default=DEFAULT_CACHE)
    p_build.set_defaults(func=cmd_build)

    p_route = sub.add_parser("route", help="rank Pochven origins for a target system")
    p_route.add_argument("targets", nargs="+", metavar="TARGET", help="K-space system name or id")
    p_route.add_argument("--sde-dir", type=Path, default=DEFAULT_SDE_DIR)
    p_route.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p_route.add_argument(
        "--sort",
        choices=("min", "avg", "max"),
        default="min",
        help="rank origins by fewest jumps (min), expected jumps (avg), or worst case (max)",
    )
    p_route.add_argument(
        "--min-sec",
        type=float,
        default=None,
        metavar="SEC",
        help="only count exits with security >= SEC (e.g. 0.45 for high-sec only)",
    )
    p_route.add_argument("--top", type=int, default=0, metavar="N", help="show only the top N origins (0 = all)")
    p_route.add_argument("--summary", action="store_true", help="summary table only, no exit detail")
    p_route.add_argument("--json", action="store_true", help="machine-readable output")
    p_route.set_defaults(func=cmd_route)

    p_verify = sub.add_parser("verify", help="sanity-check the cache against the SDE")
    p_verify.add_argument("--sde-dir", type=Path, default=DEFAULT_SDE_DIR)
    p_verify.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Convenience: "route Jita" may be written as just "Jita".
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "route")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
