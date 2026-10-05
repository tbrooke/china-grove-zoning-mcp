"""Parcel lookups survive the County's doubled parcel layer.

Since its mid-September 2026 reload, Rowan County's cg_parcels layer returns
every parcel twice (identical but for OBJECTID_1), so an exact-PIN lookup
answered "2 parcels found, requery" and get_infill_context "Multiple parcels
match". The offline test pins the deduplication; the live tests repeat the
reported cases against the real service and skip when it can't be reached.
"""

import json
import urllib.error

import pytest

import server

MAIN_ST = "5636-09-06-2113"  # 101 S Main St, Brooke & Sons LLC


def _feature(pin: str, oid: int) -> dict:
    return {"attributes": {"OBJECTID_1": oid, "PIN": pin, "PARCEL_ID": pin, "OWNNAME": "X"},
            "geometry": {"rings": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}


def test_parcel_query_keeps_one_feature_per_pin(monkeypatch):
    doubled = {"features": [_feature("A", 1), _feature("B", 2), _feature("A", 3), _feature("B", 4)]}
    monkeypatch.setattr(server, "_arcgis_query", lambda url, params: json.loads(json.dumps(doubled)))
    pins = [f["attributes"]["PIN"] for f in server._parcel_query({"where": "1=1"})["features"]]
    assert pins == ["A", "B"]


def _live(fn, *args, **kwargs) -> str:
    try:
        return fn(*args, **kwargs)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"Rowan County GIS unreachable: {e}")


def test_exact_pin_returns_the_parcel():
    out = _live(server.get_parcel_info, pin=MAIN_ST)
    assert "parcels found" not in out, out[:200]
    assert MAIN_ST in out and "101 S MAIN ST" in out


def test_owner_search_lists_each_parcel_once():
    out = _live(server.get_parcel_info, owner="BULLARD")
    pins = [l.split("**")[1] for l in out.split("\n") if l.startswith("- **")]
    assert pins and len(pins) == len(set(pins)), out[:400]


def test_infill_context_finds_each_neighbor_once():
    out = _live(server.get_infill_context, MAIN_ST)
    assert not out.startswith("Multiple parcels"), out[:200]
    import re
    pins = re.findall(r"\*\*(\d{4}-\d{2}-\d{2}-\d{4})\*\*", out)
    assert pins and len(pins) == len(set(pins))


def test_personnel_parent_match_does_not_repeat_its_children():
    out = server.get_personnel_policy("grievance")
    shown = [l for l in out.split("\n") if l.startswith("*Personnel Policy · ")]
    assert any("III-8.0*" in l for l in shown)
    assert not any("III-8.01" in l or "III-8.02" in l for l in shown), shown
    assert "8.01" in out and "8.02" in out  # still there, inside III-8.0
