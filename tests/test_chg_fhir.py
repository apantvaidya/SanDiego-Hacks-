from crawler.chg_fhir import (
    address_text,
    hours_from_available_time,
    hours_from_location,
    in_san_diego_county,
    is_medi_cal,
    phone_from,
    row_id,
    stable_id,
)

EXT = "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/contactpoint-availabletime"


def avail(days, start=None, end=None, all_day=False):
    parts = [{"url": "daysOfWeek", "valueCode": d} for d in days]
    parts.append({"url": "allDay", "valueBoolean": all_day})
    if start:
        parts.append({"url": "availableStartTime", "valueTime": start})
    if end:
        parts.append({"url": "availableEndTime", "valueTime": end})
    return {"url": EXT, "extension": parts}


def test_hours_from_available_time_weekday_and_weekend():
    telecom = [
        {"system": "url", "value": "www.example.com"},
        {
            "system": "phone",
            "value": "(619)-591-9999",
            "extension": [
                avail(["sun"], "10:00:00", "18:00:00"),
                avail(["mon", "tue", "wed", "thu", "fri"], "09:00:00", "20:00:00"),
                avail(["sat"], "10:00:00", "18:00:00"),
            ],
        },
    ]
    hours = hours_from_available_time(telecom)
    assert hours["mon"] == [["09:00", "20:00"]]
    assert hours["sun"] == [["10:00", "18:00"]]
    assert list(hours) == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def test_hours_all_day_overnight_midnight_and_dedupe():
    telecom = [
        {"system": "phone", "extension": [avail(["mon"], all_day=True)]},
        {"system": "fax", "extension": [avail(["mon"], all_day=True)]},  # duplicate on another telecom
        {"system": "phone", "extension": [avail(["fri"], "22:00:00", "02:00:00")]},
        {"system": "phone", "extension": [avail(["sat"], "08:00:00", "00:00:00")]},
    ]
    hours = hours_from_available_time(telecom)
    assert hours["mon"] == [["00:00", "24:00"]]
    assert hours["fri"] == [["22:00", "24:00"]]
    assert hours["sat"] == [["00:00", "02:00"], ["08:00", "24:00"]]


def test_hours_from_location():
    loc = {"hoursOfOperation": [{"daysOfWeek": ["mon", "tue"], "openingTime": "08:00:00", "closingTime": "17:00:00"}]}
    assert hours_from_location(loc) == {"mon": [["08:00", "17:00"]], "tue": [["08:00", "17:00"]]}
    assert hours_from_location({}) == {}


def test_network_and_county_filters():
    assert is_medi_cal("CHGM4343121")
    assert not is_medi_cal("CHGC5818385") and not is_medi_cal("CHGD5818385")
    assert in_san_diego_county("92020") and in_san_diego_county("91910") and in_san_diego_county("92103-1234")
    assert not in_san_diego_county("92201") and not in_san_diego_county("90012") and not in_san_diego_county(None)


def test_row_id_address_phone():
    assert row_id("urgent_care", "Location/CHG5064068") == "chg_5064068"
    assert stable_id("CHG5064112") == "chg_5064112"
    loc = {"address": {"line": ["133 W Main St", "Ste 100"], "city": "El Cajon", "state": "CA", "postalCode": "92020"}}
    assert address_text(loc) == "133 W Main St, Ste 100, El Cajon, CA 92020"
    assert address_text({"address": {"text": "1 A St, San Diego, CA, 92101, US"}}) == "1 A St, San Diego, CA, 92101"
    assert phone_from([{"system": "email", "value": "x@y.com"}, {"system": "phone", "value": "619"}]) == "619"
    assert phone_from([{"system": "email", "value": "x@y.com"}]) is None


def test_clean_all_day():
    from crawler.chg_fhir import clean_all_day

    hours = {"mon": [["00:00", "24:00"], ["08:00", "17:00"]], "sat": [["00:00", "24:00"]]}
    assert clean_all_day(hours) == {"mon": [["08:00", "17:00"]], "sat": [["00:00", "24:00"]]}
    assert clean_all_day(hours, allow_all_day=False) == {"mon": [["08:00", "17:00"]]}


def test_km():
    from crawler.chg_fhir import _km

    assert 40 < _km((32.75, -117.16), (33.12, -117.09)) < 45  # Hillcrest to Escondido


def test_ordinal_street():
    from crawler.chg_fhir import ordinal_street

    assert ordinal_street("4094 Fourth Ave") == "4094 4th Avenue"
    assert ordinal_street("1550 Broadway") == "1550 Broadway"


def test_building_key_ignores_floor_and_suite():
    from crawler.chg_fhir import building_key

    a = building_key("fqhc", "4094 Fourth Ave, San Diego, CA 92103")
    b = building_key("fqhc", "4094 Fourth Ave, First Floor, San Diego, CA 92103")
    assert a == b == ("fqhc", "4094 fourth ave 92103")
    assert building_key("fqhc", "3705 Mission Blvd, San Diego, CA 92109") != building_key("fqhc", "3690 Mission Blvd, San Diego, CA 92109")
