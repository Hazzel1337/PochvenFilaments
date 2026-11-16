"""Unit tests for the Pochven filament router.

Run with:  python -m unittest -v test_pochven_filaments.py

The real-data tests read the SDE under ./sde and are skipped if it has not been
downloaded.  Counts are pinned to the SDE snapshot the cache was built from; if
CCP adds systems, update them deliberately after reviewing `verify` output.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pochven_filaments as pf  # noqa: E402

SDE_DIR = HERE / "sde"


class TestDistanceMath(unittest.TestCase):
    def test_one_light_year(self):
        a = {"x": 0.0, "y": 0.0, "z": 0.0}
        b = {"x": pf.LY_METERS, "y": 0.0, "z": 0.0}
        self.assertAlmostEqual(pf.distance_ly(a, b), 1.0, places=12)

    def test_diagonal(self):
        # 3-4-5 triangle scaled by one light year / 5.
        step = pf.LY_METERS / 5.0
        a = {"x": 0.0, "y": 0.0, "z": 0.0}
        b = {"x": 3 * step, "y": 4 * step, "z": 0.0}
        self.assertAlmostEqual(pf.distance_ly(a, b), 1.0, places=12)

    def test_filament_radius_is_the_dogma_value(self):
        # lightYearDistanceMax = 2.5 on type 56068 (Proximity-5) and 56069.
        self.assertEqual(pf.FILAMENT_RANGE_LY, 2.5)
        self.assertAlmostEqual(pf.FILAMENT_RANGE_METERS / pf.LY_METERS, 2.5)
        self.assertEqual(pf.FILAMENT_RANGE_SQ, pf.FILAMENT_RANGE_METERS ** 2)


class TestBfs(unittest.TestCase):
    def test_chain(self):
        adj = {1: {2}, 2: {1, 3}, 3: {2, 4}, 4: {3}}
        self.assertEqual(pf.bfs(adj, 1), {1: 0, 2: 1, 3: 2, 4: 3})

    def test_shortcut_wins(self):
        # 1-2-3 and 1-4-3: both length 2; add 1-5-3 to stay length 2.
        adj = {1: {2, 4}, 2: {1, 3}, 3: {2, 4}, 4: {1, 3}}
        self.assertEqual(pf.bfs(adj, 1)[3], 2)

    def test_disconnected(self):
        adj = {1: {2}, 2: {1}}
        self.assertEqual(pf.bfs(adj, 1), {1: 0, 2: 1})
        self.assertNotIn(3, pf.bfs(adj, 1))


class TestFormatting(unittest.TestCase):
    def test_security_rounding(self):
        self.assertEqual(pf.fmt_sec(0.945913), "0.9")
        self.assertEqual(pf.fmt_sec(0.45), "0.5")
        self.assertEqual(pf.fmt_sec(-1.0), "-1.0")
        self.assertEqual(pf.fmt_sec(-0.017), "0.0")  # no "-0.0"

    def test_distance_constant(self):
        self.assertEqual(pf.LY_METERS, 9_460_730_472_580_800.0)


@unittest.skipUnless((SDE_DIR / "mapSolarSystems.csv").exists(), "SDE not downloaded")
class TestRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = pf.build_cache(SDE_DIR)
        cls.by_name = {p["name"]: p for p in cls.cache["pochven_systems"]}

    def test_pochven_has_27_systems(self):
        self.assertEqual(len(self.cache["pochven_systems"]), 27)

    def test_exit_counts(self):
        self.assertEqual(self.cache["counts"]["exit_pairs"], 556)
        self.assertEqual(self.cache["counts"]["exit_systems"], 389)

    def test_every_exit_is_inside_the_radius(self):
        for p in self.cache["pochven_systems"]:
            for e in p["exits"]:
                self.assertLessEqual(e["ly"], 2.5 + 1e-9, f"{p['name']}->{e['name']}")
                self.assertGreaterEqual(e["ly"], 0.0)

    def test_no_special_regions_in_exit_pool(self):
        for p in self.cache["pochven_systems"]:
            for e in p["exits"]:
                self.assertLess(e["region_id"], 11_000_000)
                self.assertNotIn(e["region_id"], pf.CANDIDATE_EXCLUDED_REGIONS)

    def test_sakenta_known_distances(self):
        sakenta = self.by_name["Sakenta"]
        exits = {e["name"]: e for e in sakenta["exits"]}
        self.assertAlmostEqual(exits["Ikuchi"]["ly"], 0.334, delta=0.002)
        self.assertAlmostEqual(exits["Jita"]["ly"], 1.360, delta=0.002)
        self.assertEqual(exits["Jita"]["security"], 0.945913)

    def test_zarzakh_is_not_a_filament_exit(self):
        for p in self.cache["pochven_systems"]:
            for e in p["exits"]:
                self.assertNotEqual(e["name"], "Zarzakh")

    def test_krais(self):
        krais = {p["constellation"] for p in self.cache["pochven_systems"]}
        self.assertEqual(krais, {"Krai Perun", "Krai Svarog", "Krai Veles"})
        self.assertEqual(self.by_name["Sakenta"]["constellation"], "Krai Perun")
        self.assertEqual(self.by_name["Niarja"]["constellation"], "Krai Svarog")
        self.assertEqual(self.by_name["Senda"]["constellation"], "Krai Veles")

    def test_system_classes(self):
        classes = {p["name"]: p["system_class"] for p in self.cache["pochven_systems"]}
        self.assertEqual(classes["Sakenta"], "Border")
        self.assertEqual(classes["Urhinichi"], "Border")
        self.assertEqual(classes["Niarja"], "Home")
        self.assertEqual(classes["Kino"], "Home")
        self.assertEqual(classes["Archee"], "Home")
        self.assertEqual(classes["Komo"], "Internal")
        self.assertEqual(sum(1 for v in classes.values() if v == "Border"), 6)
        self.assertEqual(sum(1 for v in classes.values() if v == "Home"), 3)
        self.assertEqual(sum(1 for v in classes.values() if v == "Internal"), 18)
        for krai in ("Krai Perun", "Krai Svarog", "Krai Veles"):
            in_krai = [p for p in self.cache["pochven_systems"] if p["constellation"] == krai]
            self.assertEqual(len(in_krai), 9)
            self.assertEqual(sum(1 for p in in_krai if p["system_class"] == "Border"), 2)
            self.assertEqual(sum(1 for p in in_krai if p["system_class"] == "Home"), 1)
            self.assertEqual(sum(1 for p in in_krai if p["system_class"] == "Internal"), 6)

    def test_jita_ranking_prefers_sakenta(self):
        systems = pf.load_systems(SDE_DIR)
        adj = pf.build_graph(SDE_DIR, systems)
        jita = next(s for s in systems.values() if s["name"] == "Jita")
        dist = pf.bfs(adj, jita["id"])
        origins, unreachable = pf.compute_origins(self.cache, dist)
        self.assertEqual(unreachable, 0)
        origins = pf.sort_origins(origins, "min")
        self.assertEqual(origins[0]["name"], "Sakenta")
        self.assertEqual(origins[0]["min_jumps"], 0)
        # ranking must be monotonic in the chosen sort key
        mins = [o["min_jumps"] for o in origins]
        self.assertEqual(mins, sorted(mins))

    def test_min_sec_filter(self):
        systems = pf.load_systems(SDE_DIR)
        adj = pf.build_graph(SDE_DIR, systems)
        jita = next(s for s in systems.values() if s["name"] == "Jita")
        dist = pf.bfs(adj, jita["id"])
        origins, _ = pf.compute_origins(self.cache, dist, min_sec=0.45)
        for o in origins:
            for e in o["exits"]:
                self.assertGreaterEqual(e["security"], 0.45)


@unittest.skipUnless((SDE_DIR / "mapSolarSystems.csv").exists(), "SDE not downloaded")
class TestWebApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web

        cls.web = web
        cls.app = web.App(SDE_DIR, HERE / "pochven_exits.json")

    def test_route_payload(self):
        result = self.app.route("Jita", None)
        self.assertEqual(result["target"]["name"], "Jita")
        self.assertEqual(len(result["origins"]), 27)
        self.assertEqual(result["meta"]["exit_pairs"], 556)
        first = result["origins"][0]
        self.assertEqual(first["name"], "Sakenta")
        self.assertEqual(first["constellation"], "Krai Perun")
        self.assertEqual(first["system_class"], "Border")
        self.assertEqual(first["min_jumps"], 0)
        self.assertEqual(first["exits"][0]["name"], "Jita")

    def test_unknown_target_is_404(self):
        with self.assertRaises(self.web.ApiError) as ctx:
            self.app.route("Jitaa", None)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("Jita", ctx.exception.payload["suggestions"])

    def test_pochven_and_wormhole_targets_rejected(self):
        for name in ("Niarja", "Thera"):
            with self.assertRaises(self.web.ApiError) as ctx:
                self.app.route(name, None)
            self.assertEqual(ctx.exception.status, 400, name)

    def test_min_sec_filter(self):
        result = self.app.route("Jita", 0.45)
        self.assertLess(result["meta"]["exit_pairs"], 556)
        for origin in result["origins"]:
            for exit_system in origin["exits"]:
                self.assertGreaterEqual(exit_system["security"], 0.45)

    def test_datalist_excludes_pochven(self):
        self.assertIn("Jita", self.app.system_names)
        self.assertNotIn("Niarja", self.app.system_names)
        self.assertIn("Zarzakh", self.app.system_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
