#!/usr/bin/env python3
"""Chargenberechnung (#47 Block F): release_gates.batch_hours/batch_plan und Parität zum Client
(index.html batchHours/batchCfg/batchPlanFor, per node ausgewertet). Aufruf: python tests/test_batch_calc.py"""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_gates import batch_hours, batch_plan, batch_config_error, normalize_batch  # noqa: E402


class BatchCalc(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(batch_hours(1000, 250, 2.5), 10.0)

    def test_remainder_rounds_up(self):
        self.assertEqual(batch_hours(1001, 250, 2.5), 12.5)
        self.assertEqual(batch_hours(1, 250, 2.0), 2.0)

    def test_zero_qty(self):
        self.assertEqual(batch_hours(0, 250, 2.5), 0.0)

    def test_qty_below_batch(self):
        self.assertEqual(batch_hours(40, 250, 1.5), 1.5)

    def test_floats(self):
        self.assertEqual(batch_hours(1000.0, 250.0, 0.5), 2.0)
        self.assertEqual(batch_hours(100.5, 50.0, 1.0), 3.0)
        self.assertEqual(batch_hours(10, 2.5, 1.0), 4.0)

    def test_zero_process(self):
        self.assertEqual(batch_hours(500, 100, 0), 0.0)

    def test_invalid(self):
        for args in [(-1, 10, 1), (10, 0, 1), (10, -5, 1), (10, 10, -0.1),
                     (float("nan"), 10, 1), (10, float("inf"), 1), ("10", 10, 1),
                     (None, 10, 1), (True, 10, 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                batch_hours(*args)

    def test_error_message_german(self):
        with self.assertRaisesRegex(ValueError, "Chargengröße"):
            batch_hours(10, 0, 1)

    def test_large_numbers(self):
        self.assertEqual(batch_hours(10**12, 1000, 1.0), 1e9)
        self.assertEqual(batch_hours(10**12 + 1, 1000, 1.0), 1e9 + 1)


class BatchCalcExtended(unittest.TestCase):
    def test_parallel(self):
        # 7 Chargen, 3 gleichzeitig -> 3 Durchgänge
        self.assertEqual(batch_hours(700, 100, 2.0, parallel=3), 6.0)
        self.assertEqual(batch_hours(600, 100, 2.0, parallel=3), 4.0)
        self.assertEqual(batch_hours(600, 100, 2.0, parallel=99), 2.0)

    def test_cleaning_between_rounds_not_after_last(self):
        # 3 Chargen × 2:00 + Reinigung 2 × 0:15 = 6,5 h
        self.assertEqual(batch_hours(300, 100, 2.0, clean_hours=0.25), 6.5)
        self.assertEqual(batch_hours(100, 100, 2.0, clean_hours=0.25), 2.0)
        self.assertEqual(batch_hours(600, 100, 2.0, parallel=2, clean_hours=0.5), 7.0)

    def test_setup_per_batch(self):
        # Rüsten je Charge: weitere Durchgänge × Rüstzeit (das erste Rüsten plant der Scheduler)
        self.assertEqual(batch_hours(300, 100, 1.0, setup_hours=0.5), 4.0)
        self.assertEqual(batch_hours(300, 100, 1.0, setup_hours=0.5, clean_hours=0.25), 4.5)

    def test_prorata(self):
        # 250 Stk, 100 je Charge, 2:00 -> 2 volle + 0,5 × 2:00 = 5 h
        self.assertEqual(batch_hours(250, 100, 2.0, partial="prorata"), 5.0)
        self.assertEqual(batch_hours(250, 100, 2.0, partial="full"), 6.0)
        self.assertEqual(batch_hours(300, 100, 2.0, partial="prorata"), 6.0)
        # parallel 2: letzte Runde 50 von 200 -> 0:30
        self.assertEqual(batch_hours(250, 100, 2.0, parallel=2, partial="prorata"), 2.5)
        # anteilig auf ganze Minuten aufgerundet: 1/3 von 1:00 = 20 min, 1/7 von 1:00 -> 9 min
        self.assertAlmostEqual(batch_hours(1, 3, 1.0, partial="prorata"), 20 / 60, places=12)
        self.assertAlmostEqual(batch_hours(1, 7, 1.0, partial="prorata"), 9 / 60, places=12)

    def test_defaults_unchanged(self):
        for q, b, p in [(1000, 250, 2.5), (1001, 250, 2.5), (10, 2.5, 1.0), (100.5, 50.0, 1.0)]:
            self.assertEqual(batch_hours(q, b, p), batch_hours(q, b, p, parallel=1, clean_hours=0, setup_hours=0, partial="full"))

    def test_invalid_new_args(self):
        for kw in [{"parallel": 0}, {"parallel": 1.5}, {"parallel": True}, {"clean_hours": -1}, {"setup_hours": float("nan")},
                   {"partial": "half"}]:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                batch_hours(10, 5, 1, **kw)

    def test_config_validation(self):
        ok = {"size": 250, "minutes": 120, "parallel": 2, "cleanMinutes": 15, "setup": "batch", "partial": "prorata"}
        self.assertIsNone(batch_config_error(ok))
        self.assertIsNone(batch_config_error({"size": 1, "minutes": 1}))
        self.assertIsNone(batch_config_error(None))
        for bad in [[], "x", {"minutes": 60}, {"size": 0, "minutes": 60}, {"size": 10, "minutes": 0}, {"size": 10, "minutes": 1.5},
                    {"size": 10, "minutes": 14401}, {"size": 10, "minutes": 60, "parallel": 0}, {"size": 10, "minutes": 60, "parallel": 100},
                    {"size": 10, "minutes": 60, "cleanMinutes": -1}, {"size": 10, "minutes": 60, "cleanMinutes": 1441},
                    {"size": 10, "minutes": 60, "setup": "x"}, {"size": 10, "minutes": 60, "partial": "x"},
                    {"size": True, "minutes": 60}, {"size": "10", "minutes": 60}, {"size": 10, "minutes": 60, "foo": 1}]:
            with self.subTest(bad=bad):
                self.assertIsNotNone(batch_config_error(bad))
        self.assertEqual(normalize_batch({"size": 5, "minutes": 30}),
                         {"size": 5.0, "minutes": 30, "parallel": 1, "cleanMinutes": 0, "setup": "order", "partial": "full"})

    def test_plan(self):
        cfg = {"size": 100, "minutes": 120, "cleanMinutes": 15}
        p = batch_plan(cfg, 300, 30)
        self.assertEqual((p["hours"], p["batches"], p["rounds"]), (6.5, 3, 3))
        self.assertEqual(batch_plan({**cfg, "setup": "batch"}, 300, 30)["hours"], 7.5)
        self.assertIsNone(batch_plan(cfg, 0))
        self.assertIsNone(batch_plan(None, 300))
        self.assertIsNone(batch_plan({"size": 0, "minutes": 60}, 300))


# Paritätstabelle Client/Server: (Chargen-Einstellung, Menge, Umrüsten min)
CFGS = [
    {"size": 100, "minutes": 120},
    {"size": 100, "minutes": 120, "cleanMinutes": 15},
    {"size": 250, "minutes": 95, "parallel": 3, "cleanMinutes": 7, "setup": "batch", "partial": "prorata"},
    {"size": 2.5, "minutes": 1, "parallel": 2, "partial": "prorata"},
    {"size": 7, "minutes": 61, "parallel": 1, "cleanMinutes": 1440, "setup": "batch", "partial": "full"},
    {"size": 1000, "minutes": 14400, "parallel": 99, "cleanMinutes": 59, "setup": "order", "partial": "prorata"},
    {"size": 33.3, "minutes": 47, "parallel": 4, "cleanMinutes": 13, "setup": "batch", "partial": "prorata"},
    {"size": 0, "minutes": 60},                      # ungültig -> beide None
    {"size": 10, "minutes": 60, "parallel": 0},      # ungültig -> beide None
]
QTYS = [0, 1, 2, 3, 99, 100, 101, 249, 250, 251, 299, 300, 301, 749, 750, 751, 1001, 12345, 999999]
SETUPS = [0, 30, 45]


def client_functions() -> str:
    lines = (ROOT / "index.html").read_text(encoding="utf-8").splitlines()
    out = []
    for name in ("batchHours", "batchCfg", "batchPlanFor"):
        hit = [ln for ln in lines if ln.startswith(f"function {name}(")]
        if len(hit) != 1:
            raise AssertionError(f"Client-Funktion {name} nicht eindeutig gefunden ({len(hit)})")
        out.append(hit[0])
    return "\n".join(out)


@unittest.skipUnless(shutil.which("node"), "node fehlt – Paritätstest übersprungen")
class ClientServerParity(unittest.TestCase):
    def test_parity_table(self):
        cases = list(itertools.product(range(len(CFGS)), QTYS, SETUPS))
        js = client_functions() + """
const CFGS=%s, CASES=%s;
console.log(JSON.stringify(CASES.map(([c,q,s])=>{const p=batchPlanFor(CFGS[c],q,s);return p?[p.hours,p.batches,p.rounds]:null})));
""" % (json.dumps(CFGS), json.dumps(cases))
        res = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        client = json.loads(res.stdout)
        self.assertEqual(len(client), len(cases))
        checked = 0
        for (c, q, s), got in zip(cases, client):
            p = batch_plan(CFGS[c], q, s)
            want = None if p is None else [p["hours"], p["batches"], p["rounds"]]
            with self.subTest(cfg=CFGS[c], qty=q, setup=s):
                self.assertEqual(got, want)  # exakt gleiche Gleitkommazahl
            checked += want is not None
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main(verbosity=1)
