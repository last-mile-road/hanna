import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.treatment import derive_treatment_grid


def test_one_dose_and_two_empty_rows():
    parsed = {
        "treatment_grid": {
            "rows": [
                {"year": 2025, "doses": [{"month": 5, "day": 31}], "missed_months": []},
                None,
                None,
            ]
        }
    }
    out = derive_treatment_grid(parsed)
    assert out["treatment_start_date"] == "2025-05-31"
    assert out["total_treatment_times"] == "1"
    assert out["treatment_months_total"] == "1"
    assert out["treatment_missed"] == "0"


def test_missed_month_and_second_year():
    parsed = {
        "rows": [
            {"year": 2025, "doses": [{"month": 11, "day": 4}], "missed_months": []},
            {"year": 2026, "doses": [{"month": 2, "day": 3}], "missed_months": [3]},
            None,
        ]
    }
    out = derive_treatment_grid(parsed)
    assert out["treatment_start_date"] == "2025-11-04"
    assert out["total_treatment_times"] == "2"
    assert out["treatment_months_total"] == "3"
    assert out["treatment_missed"] == "1"


def test_month_name_and_string_year():
    parsed = {"rows": [{"year": "2025", "doses": [{"month": "Maio", "day": "31"}]}]}
    out = derive_treatment_grid(parsed)
    assert out["treatment_start_date"] == "2025-05-31"


def test_empty_grid():
    out = derive_treatment_grid({"treatment_grid": {"rows": []}})
    assert out["treatment_start_date"] == ""
    assert out["total_treatment_times"] == "0"


def test_months_total_includes_missed_months():
    parsed = {
        "treatment_grid": {
            "rows": [
                {
                    "year": 2022,
                    "doses": [
                        {"month": 2, "day": 18},
                        {"month": 3, "day": 21},
                        {"month": 4, "day": 17},
                        {"month": 5, "day": 15},
                    ],
                    "missed_months": [6, 7, 8],
                }
            ]
        }
    }
    out = derive_treatment_grid(parsed)
    assert out["total_treatment_times"] == "4"
    assert out["treatment_missed"] == "3"
    assert out["treatment_months_total"] == "7"


def test_cells_shape_is_supported():
    parsed = {
        "treatment_grid": {
            "rows": [
                {
                    "year": 2022,
                    "cells": [
                        {"month": 2, "status": "dose", "day": 18},
                        {"month": 3, "status": "dose", "day": 21},
                        {"month": 6, "status": "missed"},
                        {"month": 7, "status": "missed"},
                    ],
                }
            ]
        }
    }
    out = derive_treatment_grid(parsed)
    assert out["total_treatment_times"] == "2"
    assert out["treatment_missed"] == "2"
    assert out["treatment_months_total"] == "4"
    assert out["treatment_start_date"] == "2022-02-18"
