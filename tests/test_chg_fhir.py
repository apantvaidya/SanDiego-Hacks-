"""Tests for #11 CHG FHIR import: parsing is pure; fetch is tested with fakes (no network)."""
from crawler import chg_fhir
from crawler.chg_fhir import _street_for_geocoding, address_key, fetch_chg_locations, parse_hours, stable_id
from app.models import GeoPoint


def test_parse_hours_weekdays_and_weekend():
    hours = parse_hours([
        {"daysOfWeek": ["mon", "tue", "wed", "thu", "fri"], "allDay": False,
         "openingTime": "08:00:00", "closingTime": "20:00:00"},
        {"daysOfWeek": ["sun"], "allDay": False, "openingTime": "08:00:00", "closingTime": "18:00:00"},
    ])
    assert hours["mon"] == [["08:00", "20:00"]]
    assert hours["sun"] == [["08:00", "18:00"]]
    assert "sat" not in hours


def test_parse_hours_all_day_midnight_and_overnight():
    assert parse_hours([{"daysOfWeek": ["mon"], "allDay": True}]) == {"mon": [["00:00", "24:00"]]}
    assert parse_hours([{"daysOfWeek": ["tue"], "openingTime": "08:00:00", "closingTime": "00:00:00"}]) == \
        {"tue": [["08:00", "24:00"]]}
    assert parse_hours([{"daysOfWeek": ["sun"], "openingTime": "22:00:00", "closingTime": "02:00:00"}]) == \
        {"mon": [["00:00", "02:00"]], "sun": [["22:00", "24:00"]]}


def test_parse_hours_dedupes_and_skips_incomplete():
    span = {"daysOfWeek": ["mon"], "openingTime": "09:00:00", "closingTime": "14:00:00"}
    assert parse_hours([span, span, {"daysOfWeek": ["tue"]}]) == {"mon": [["09:00", "14:00"]]}


def test_hours_from_telecom_extension():
    loc = {"telecom": [{"system": "phone", "value": "(619)-225-6200", "rank": 1, "extension": [{
        "url": "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/contactpoint-availabletime",
        "extension": [{"url": "daysOfWeek", "valueCode": "sat"}, {"url": "allDay", "valueBoolean": False},
                      {"url": "availableStartTime", "valueTime": "09:00:00"},
                      {"url": "availableEndTime", "valueTime": "14:00:00"}]}]}]}
    assert chg_fhir._hours_from(loc) == {"sat": [["09:00", "14:00"]]}


def test_address_key_matches_suite_and_suffix_variants():
    assert address_key("3434 Midway Dr Ste 1002, San Diego, CA 92110") == \
        address_key("3434 Midway Drive, San Diego, CA 92110")
    assert address_key("8260 Mira Mesa Blvd Suite A, San Diego, CA 92126") == "8260 mira 92126"
    assert address_key("1 Main St, El Cajon, CA 92020") != address_key("1 Main St, Escondido, CA 92025")


def test_street_for_geocoding_drops_suite():
    assert _street_for_geocoding("3434 Midway Dr Ste 1002, San Diego, CA 92110") == "3434 Midway Dr, San Diego, CA 92110"
    assert _street_for_geocoding("8260 Mira Mesa Blvd Suite A, San Diego") == "8260 Mira Mesa Blvd, San Diego"


def test_stable_id():
    assert stable_id("CHG5064112") == "chg_5064112"


def test_fetch_builds_rows_dedupes_and_skips_unplaced(monkeypatch):
    services = {"entry": [
        {"resource": {"location": [{"reference": "Location/CHG1"}]}},
        {"resource": {"location": [{"reference": "Location/CHG2"}, {"reference": "Location/CHG3"}]}},
    ]}
    locations = {
        "CHG1": {"name": "MARQUE URGENT CARE", "address": {"line": ["4085 Governor Dr"], "city": "San Diego",
                 "postalCode": "92122"}, "telecom": [{"system": "phone", "value": "(858) 555-0100", "rank": 1}],
                 "hoursOfOperation": [{"daysOfWeek": ["sun"], "openingTime": "08:00:00", "closingTime": "18:00:00"}]},
        "CHG2": {"name": "Same Place", "address": {"line": ["4085 Governor Drive"], "city": "San Diego",
                 "postalCode": "92122"}},
        "CHG3": {"name": "Far Away", "address": {"line": ["1 Nowhere Rd"], "city": "Los Angeles", "postalCode": "90001"}},
    }

    def fake_get(path, params=None):
        return services if path == "HealthcareService" else locations[path.split("/")[1]]

    monkeypatch.setattr(chg_fhir, "_get", fake_get)
    monkeypatch.setattr(chg_fhir, "_nominatim",
                        lambda p: GeoPoint(32.87, -117.21, "x") if "Governor" in p["q"] else None)
    rows = fetch_chg_locations()
    assert [r["id"] for r in rows] == ["chg_1"]
    r = rows[0]
    assert r["name"] == "Marque Urgent Care" and r["accepts"] == ["chg_medi_cal"] and r["kind"] == "urgent_care"
    assert r["hours"] == {"sun": [["08:00", "18:00"]]} and r["phone"] == "(858) 555-0100"
    assert r["has_xray"] is None and r["self_pay_price"] is None
    assert r["source_url"].endswith("/Location/CHG1")
