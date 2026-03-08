"""Tests for the China Grove Zoning MCP server."""

from unittest.mock import patch

from server import _normalize_district_code, get_parcel_info


class TestNormalizeDistrictCode:
    """Verify GIS codes are mapped to canonical hyphenated forms."""

    def test_unhyphenated_to_hyphenated(self):
        assert _normalize_district_code("RS") == "R-S"
        assert _normalize_district_code("CB") == "C-B"
        assert _normalize_district_code("HB") == "H-B"
        assert _normalize_district_code("RP") == "R-P"
        assert _normalize_district_code("RT") == "R-T"
        assert _normalize_district_code("RM") == "R-M"
        assert _normalize_district_code("RMH") == "R-MH"
        assert _normalize_district_code("NC") == "N-C"
        assert _normalize_district_code("OI") == "O-I"
        assert _normalize_district_code("CP") == "C-P"
        assert _normalize_district_code("LI") == "L-I"
        assert _normalize_district_code("HI") == "H-I"

    def test_already_hyphenated(self):
        assert _normalize_district_code("R-S") == "R-S"
        assert _normalize_district_code("C-B") == "C-B"
        assert _normalize_district_code("R-MH") == "R-MH"

    def test_case_insensitive(self):
        assert _normalize_district_code("rs") == "R-S"
        assert _normalize_district_code("cb") == "C-B"
        assert _normalize_district_code("r-s") == "R-S"
        assert _normalize_district_code("Rmh") == "R-MH"

    def test_pud_passthrough(self):
        assert _normalize_district_code("PUD") == "PUD"
        assert _normalize_district_code("pud") == "PUD"

    def test_whitespace_stripped(self):
        assert _normalize_district_code("  RS  ") == "R-S"
        assert _normalize_district_code(" C-B ") == "C-B"

    def test_unknown_code_returned_as_is(self):
        assert _normalize_district_code("XYZ") == "XYZ"
        assert _normalize_district_code("SPECIAL") == "SPECIAL"


# --- Helpers for mocking ArcGIS responses in get_parcel_info ---

_FAKE_PARCEL_FEATURE = {
    "attributes": {
        "PIN": "5626-01-38-0952",
        "PARCEL_ID": "5626-01-38-0952",
        "OWNNAME": "DOE JOHN",
        "OWN2": None,
        "PROP_ADDRE": "100 MAIN ST",
        "DEEDACRE": 1.0,
        "CALCACRE": 1.0,
        "TAX_DISTRI": "CG",
        "PARENT_PIN": None,
        "TOT_VAL": 150000,
        "LANDFMV": 30000,
        "IMP_FMV": 120000,
    },
    "geometry": {
        "rings": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]],
    },
}

_FAKE_ZONING_ATTRS = {"zoning": "RS", "effective_date": "2020-01-01"}


def _make_arcgis_side_effect(*, corp_attrs=None, etj_attrs=None, zoning_attrs=None):
    """Return a side effect function for _arcgis_query that returns fake data."""
    def _side_effect(url, params):
        from server import _PARCEL_URL, _ZONING_URL, _ETJ_URL, _CORP_LIMITS_URL
        if url == _PARCEL_URL:
            return {"features": [_FAKE_PARCEL_FEATURE]}
        if url == _ZONING_URL:
            attrs = zoning_attrs if zoning_attrs is not None else _FAKE_ZONING_ATTRS
            return {"features": [{"attributes": attrs}]}
        if url == _ETJ_URL:
            if etj_attrs is not None:
                return {"features": [{"attributes": etj_attrs}]}
            return {"features": []}
        if url == _CORP_LIMITS_URL:
            if corp_attrs is not None:
                return {"features": [{"attributes": corp_attrs}]}
            return {"features": []}
        return {"features": []}
    return _side_effect


class TestETJAdvisory:
    """Verify ETJ advisory note appears for ETJ parcels and not for in-Town."""

    @patch("server._arcgis_query")
    def test_etj_parcel_shows_advisory(self, mock_query):
        mock_query.side_effect = _make_arcgis_side_effect(
            etj_attrs={"OBJECTID": 1},
        )
        result = get_parcel_info(pin="5626-01-38-0952")
        assert "ETJ Advisory" in result
        assert "building inspections are conducted by Rowan County" in result
        assert "Jurisdiction:** ETJ" in result

    @patch("server._arcgis_query")
    def test_in_town_parcel_no_advisory(self, mock_query):
        mock_query.side_effect = _make_arcgis_side_effect(
            corp_attrs={"CITY_NAME": "CHINA GROVE"},
        )
        result = get_parcel_info(pin="5626-01-38-0952")
        assert "ETJ Advisory" not in result
        assert "Inside corporate limits" in result

    @patch("server._arcgis_query")
    def test_outside_jurisdiction_no_advisory(self, mock_query):
        mock_query.side_effect = _make_arcgis_side_effect()
        result = get_parcel_info(pin="5626-01-38-0952")
        assert "ETJ Advisory" not in result
        assert "Outside jurisdiction" in result


class TestConditionalUseFieldIgnored:
    """The GIS conditional_use field is district-level, not parcel-level.

    It must never appear in output — even when the GIS returns ' yes' (as
    the R-S district polygon does), it does NOT mean the parcel has a CUP.
    """

    @patch("server._arcgis_query")
    def test_district_level_conditional_use_not_surfaced(self, mock_query):
        """A zoning response with conditional_use=' yes' must not trigger a warning."""
        mock_query.side_effect = _make_arcgis_side_effect(
            corp_attrs={"CITY_NAME": "CHINA GROVE"},
            zoning_attrs={"zoning": "RS", "conditional_use": " yes", "effective_date": "031902 JMc"},
        )
        result = get_parcel_info(pin="5626-01-38-0952")
        assert "Conditional Use" not in result
        assert "CUP" not in result
        assert "ZONING:** R-S" in result

    @patch("server._arcgis_query")
    def test_no_conditional_use_field_at_all(self, mock_query):
        """A zoning response without the field should work fine."""
        mock_query.side_effect = _make_arcgis_side_effect(
            corp_attrs={"CITY_NAME": "CHINA GROVE"},
            zoning_attrs={"zoning": "CB", "effective_date": "2020-01-01"},
        )
        result = get_parcel_info(pin="5626-01-38-0952")
        assert "Conditional Use" not in result
        assert "ZONING:** C-B" in result
