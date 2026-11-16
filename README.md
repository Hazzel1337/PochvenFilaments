# Pochven filament extraction router

A small, dependency-free Python tool that answers one question:

> **I want to end up at system X. Which Pochven system should I activate a
> Proximity 'Extraction' filament in, and where is the filament likely to spit
> me out?**

It computes the 2.5 LY filament exit set for every Pochven system from EVE's
Static Data Export and ranks the origins by stargate jumps to your target.
Comes with a CLI and a local web UI with sortable tables.

---

## Quick start

```powershell
python pochven_filaments.py fetch        # download the SDE CSVs (~3.4 MB)
python pochven_filaments.py build        # cache the 2.5 LY exits
python pochven_filaments.py route Jita   # ranked table
```

`route` builds the cache automatically on first use, so the explicit `build`
step is optional. Everything is offline after the download; there are no ESI
calls and no third-party packages.

```
Target: Jita  (The Forge, security 0.9)
Graph:  5,175 gate-connected systems; filament radius 2.5 LY
Origins: 27 Pochven systems / 389 exit systems / 556 exit pairs

  #  Pochven origin    Type      Krai         Exits   Min    Avg   Max
  -  ----------------  --------  -----------  -----  ----  -----  ----
  1  Sakenta           Border    Krai Perun      24     0    2.7     7
  2  Nani              Internal  Krai Svarog     35     1    3.5     6
  3  Senda             Border    Krai Veles      20     1    4.1    13
...
```

`--top 2` expands the ranking into the full exit list:

```
#1  Sakenta  -  24 exits, 0-7 jumps (avg 2.7)
  Jumps      LY    Sec   Exit system       Region
      0   1.360    0.9   Jita              The Forge  <- target
      1   0.334    0.9   Ikuchi            The Forge
      1   0.746    0.9   Niyabainen        The Forge
      1   0.751    0.9   New Caldari       The Forge
      1   1.010    0.9   Perimeter         The Forge
      1   1.696    0.9   Maurasi           The Forge
      2   0.801    0.9   Hirtamon          The Forge
...
```

Sakenta can drop you **directly in Jita**, 1.36 LY away — the well-known
"filament home" trick, and a good end-to-end sanity check of the coordinates.

---

## How it works

### Phase 1 — the 2.5 LY exit cache (`build`)

1. Parse `mapSolarSystems.csv` (id, name, region, security, x/y/z in metres).
2. Take the 27 systems with `regionID == 10000070` (Pochven). Their coordinates
   are their original pre-invasion positions, scattered around the map — which
   is exactly why a 2.5 LY filament from Sakenta lands next to Jita.
3. For every Pochven × valid-K-space pair compute

   ```
   d_ly = hypot(xP - xK, yP - yK, zP - zK) / 9_460_730_472_580_800
   ```

   using the exact 1 LY = 9,460,730,472,580,800 m constant.
4. Keep pairs with `d_ly <= 2.5` and store name, region, security, exact
   distance and the origin's Pochven krai (constellation, from
   `mapConstellations.csv`: Krai Perun, Krai Svarog, Krai Veles) in
   `pochven_exits.json`.

The current Fuzzwork dump yields **27 origins, 389 unique exit systems and 556
origin→exit pairs**.

### Phase 2 — gate routing (`route`)

1. Build an undirected stargate graph from `mapSolarSystemJumps.csv`.
2. BFS outward from your target system (unweighted BFS is exact here; no
   `networkx` needed, and no live API).
3. Attach each exit's jump count, then rank origins by minimum jumps
   (`--sort min`), mean jumps (`--sort avg`) or worst case (`--sort max`).

`avg` is arguably the better default for real use: the filament picks a random
system in range, so the mean is your expected travel — a single lucky 0-jump
exit does not make an origin good.

---

## Filters (and one correction)

| Region | ID | In exit pool? | In gate graph? | Why |
|---|---|---|---|---|
| Pochven | 10000070 | no | no | you start here; its gates are internal only |
| Jove (UUA-F4, J7HZ-F, A821-A) | 10000004 / 10000017 / 10000019 | no | no | inaccessible space, disconnected from gates |
| Zarzakh | 10001000 | **no** | **yes** | see below |
| Exordium | 10001004 | no | no | special/event region |
| Wormhole space | ≥ 11000000 | no | no | not K-space |

**Zarzakh is not an isolated pocket.** The SDE contains eight jump rows for
system 30100000, four each way, connecting it to H-PA29, Turnur, G-0Q86 and
Alsavoinon. Those are ordinary stargates, so a gate route may legitimately pass
through Zarzakh and the BFS keeps it. It is still excluded from *filament
destinations* (special-access system), which is why the candidate pool and the
gate graph use different exclusion sets. The tool shows the difference: keeping
Zarzakh in the graph changes ~1,000 systems' Jita distance by one jump.

Only exits with security > -1 are ever stored, and every stored exit is verified
to be gate-reachable from Jita.

---

## Commands

```
python pochven_filaments.py fetch [--sde-dir DIR] [--force]
python pochven_filaments.py build [--sde-dir DIR] [--out FILE]
python pochven_filaments.py route TARGET [TARGET ...] [--sort min|avg|max]
                                   [--min-sec SEC] [--top N] [--summary] [--json]
python pochven_filaments.py verify [--sde-dir DIR] [--cache FILE]
```

- `TARGET` is a system name (case-insensitive) or numeric id. Unknown names get
  close-match suggestions. Pochven/Jove/wormhole targets are rejected with an
  explanation.
- `--min-sec 0.45` keeps only high-sec exits (useful when the exit itself must
  be safe). `--min-sec 0.0` drops null-sec exits.
- `--top N` truncates both the ranking and the detail sections.
- `--summary` prints only the ranking table.
- `--json` emits the full structure for scripting (exits always unfiltered by
  `--top`, but `--min-sec` still applies).

`route Jita` and `route Jita Amarr` both work; `python pochven_filaments.py Jita`
is shorthand for `route Jita`.

---

## Web UI

```powershell
python web.py            # opens http://127.0.0.1:8770/
```

or double-click `web-ui.cmd`. The server is stdlib-only; `--port`, `--host`,
`--no-browser` and `--verbose` are available.

- **Target box** with autocomplete over every K-space system.
- **"2.5 LY exits" (default view)** — one row per exit system, sorted by gate
  jumps to your target, with columns: jumps, exit system, security, K-space
  region, LY to the nearest origin, Pochven origin, Triglavian type
  (Border/Home/Internal) and Pochven krai. A `+N` chip means the system is in
  range of N additional origins; click the row to list every linked origin with
  its type, krai and exact LY distance.
- **"Origins ranked"** — the CLI-style ranking, with type and krai columns.
- **Sortable tables** — click any header in either view; click again to reverse.
- **Filter** box matches system, origin and krai names.
- **Min exit sec** re-ranks server-side, using the same filter as `--min-sec`.
- Target, min-sec, active tab and a pinned `open=` system are written to the
  URL, so the view is shareable/bookmarkable.
- Distances and jump counts are computed live from the same cache and gate
  graph as the CLI; the server caches each target's BFS result in memory.

---

## Validation

`verify` re-derives everything and checks it against the SDE:

```
PASS  Pochven system count is 27
PASS  system names and exit lists are unique
PASS  cached counts match recomputed counts
PASS  exit distances <= 2.5 LY and recompute exactly
PASS  no wormhole / Jove / Zarzakh / Exordium exits
PASS  every Pochven system has an exit within 1 LY
PASS  known pair Sakenta->Ikuchi is ~0.334 LY
PASS  every exit is gate-reachable (BFS from Jita)
PASS  Pochven systems cover the three krais
PASS  Border/Home/Internal split is 6/3/18, two-one-six per krai
10/10 checks passed; 27 Pochven systems, 389 exit systems, 556 exit pairs
```

Unit tests: `python -m unittest -v test_pochven_filaments.py`.

The 2.5 LY radius is not folklore: the SDE dogma attribute
`lightYearDistanceMax` (3097) is **2.5** on both Proximity-5 (type 56068) and
Proximity-15 (type 56069). The "5" and "15" in the names are the maximum number
of ships the filament can carry (`Maximum Ship Jump cap`), not a range.

---

## Caveats

- **Jump count is not a safety score.** BFS treats every gate equally and
  ignores security, so the shortest route from an exit to your target can still
  cross low-sec or null pipes. `--min-sec` filters the exit system, not the
  route. A security-weighted path search is the natural next step if you want
  gatecamp avoidance.
- The cache is a snapshot. Re-run `build` after an SDE update; new systems or a
  changed region will shift the counts (the tests pin the current ones).
- Distances are computed from star coordinates, not from the in-game filament
  implementation. CCP could change the mechanic; the `lightYearDistanceMax`
  attribute is the thing to re-check.
- Filament destination is random within range, so use `--sort avg` for expected
  travel time and `--sort max` for worst case.
- Security is shown rounded to one decimal (EVE style); the JSON keeps 6.
- **Border/Home/Internal is curated, not SDE-derived.** The dump has no such
  field (`border` and `securityClass` are legacy map data). The table follows
  the per-system in-game type as published by
  [EDI](https://edencom.space/pochven_systems.php), checked 2026-11-16. It is
  self-consistent: 6 Border (2 per krai, the known C729 systems), 1 Home per
  krai, 6 Internal per krai. If CCP reclassifies a system, edit
  `POCHVEN_SYSTEM_CLASSES` in `pochven_filaments.py`; `verify` will flag a
  broken 6/3/18 split.
- Data: [Fuzzwork SDE dump](https://www.fuzzwork.co.uk/dump/latest/csv/)
  (mapSolarSystems, mapSolarSystemJumps, mapRegions, mapConstellations).

## Files

| File | Purpose |
|---|---|
| `pochven_filaments.py` | the CLI (standard library only) |
| `web.py` | local web UI server + JSON API (standard library only) |
| `web/` | `index.html`, `app.js`, `style.css` for the UI |
| `web-ui.cmd` | double-click launcher for the UI |
| `pochven_exits.json` | generated 2.5 LY cache, safe to delete |
| `test_pochven_filaments.py` | unit tests over the math, BFS, SDE and web API |
| `sde/` | downloaded CSVs (git-ignored, re-fetchable) |
