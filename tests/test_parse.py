import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import parse


def test_plain_json():
    assert parse.parse_json_response('{"a": "1"}') == {"a": "1"}


def test_fenced_json_and_key_normalisation():
    raw = '```json\n{"Patient Name": "Sam", "n-2": 2}\n```'
    assert parse.parse_json_response(raw) == {"patient_name": "Sam", "n_2": 2}


def test_trailing_comma_is_repaired():
    assert parse.parse_json_response('{"a": "1",}') == {"a": "1"}


def test_regex_fallback_for_one_field():
    raw = 'The value is "patient_name": "Sam" in the image.'
    assert parse.parse_json_response(raw, ["patient_name"]) == {"patient_name": "Sam"}


def test_non_json_returns_empty():
    assert parse.parse_json_response("I cannot read this image.") == {}
