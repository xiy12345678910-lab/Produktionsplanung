#!/usr/bin/env python3
"""Chargenberechnung (#47 Block F): release_gates.batch_hours. Aufruf: python tests/test_batch_calc.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_gates import batch_hours  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=1)
