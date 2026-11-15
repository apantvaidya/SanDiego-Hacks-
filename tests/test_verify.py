from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.care import verify
from app.config import settings
from app.models import Location

SD = ZoneInfo("America/Los_Angeles")
WED_1PM = datetime(2026, 11, 18, 13, 0, tzinfo=SD)
WED_11PM = datetime(2026, 11, 18, 23, 0, tzinfo=SD)


def _loc(source_url="https://www.example-urgent.com/mira-mesa", name="AFC Urgent Care Mira Mesa",
         address="8260 Mira Mesa Blvd Suite A, San Diego, CA 92126"):
    return Location(
        id="afc_mira_mesa", name=name, kind="urgent_care", lat=32.9, lon=-117.1, address=address,
        phone=None, hours={"wed": [["08:00", "20:00"]]}, has_xray=True, walk_in=True,
        accepts=["uninsured"], self_pay_price=199.0, sliding_scale=None, source_url=source_url,
        crawled_at=datetime(2026, 11, 15, 0, 30, tzinfo=SD),
    )


def test_page_lists_by_street_or_name():
    loc = _loc()
    assert verify.page_lists(loc, "Visit us at 8260 Mira Mesa Blvd, Suite A")
    assert verify.page_lists(loc, "AFC Urgent Care Mira Mesa is open daily")
    assert not verify.page_lists(loc, "Find a location near you")


def test_decide():
    loc = _loc()
    assert verify.decide(loc, {"listed": True, "closed": False}, WED_1PM) is True
    assert verify.decide(loc, {"listed": True, "closed": False}, WED_11PM) is False
    assert verify.decide(loc, {"listed": True, "closed": True}, WED_1PM) is False
    assert verify.decide(loc, {"listed": False, "closed": False}, WED_1PM) is None


def test_closed_notice_regex():
    assert verify.CLOSED.search("This location is temporarily closed for renovation")
    assert verify.CLOSED.search("Clínica cerrada temporalmente".replace("í", "i"))
    assert not verify.CLOSED.search("Closed on Sundays")


def test_web_check_through_brightdata(monkeypatch):
    monkeypatch.setattr(settings, "brightdata_api_token", "tok")
    monkeypatch.setattr(settings, "brightdata_unlocker_zone", "zone1")
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, auth=headers["Authorization"], body=json, timeout=timeout)
        return httpx.Response(200, text="<html><script>x</script><p>8260 Mira Mesa Blvd</p></html>")

    monkeypatch.setattr(verify.httpx, "post", fake_post)
    recorded = []
    monkeypatch.setattr(verify, "_record", recorded.append)
    v = verify.verify_live(_loc(), timeout_s=4)
    assert seen["url"] == verify.BRIGHTDATA_REQUEST and seen["auth"] == "Bearer tok"
    assert seen["body"] == {"zone": "zone1", "url": "https://www.example-urgent.com/mira-mesa", "format": "raw"}
    assert seen["timeout"] == 4
    assert v.raw["how"] == "brightdata" and v.raw["listed"] is True and v.raw["closed"] is False
    assert recorded == [v]


def test_web_check_without_zone_is_not_available(monkeypatch):
    monkeypatch.setattr(settings, "brightdata_unlocker_zone", "")
    with pytest.raises(NotImplementedError):
        verify.verify_live(_loc())


def test_chg_check_uses_fhir_directly(monkeypatch):
    url = verify.CHG_FHIR + "Location/CHG5064112"

    def fake_get(u, headers, timeout):
        assert u == url
        return httpx.Response(200, json={"resourceType": "Location", "status": "active"})

    monkeypatch.setattr(verify.httpx, "get", fake_get)
    monkeypatch.setattr(verify, "_record", lambda v: None)
    v = verify.verify_live(_loc(source_url=url, name="Marque Urgent Care - UTC", address="4085 Governor Dr"))
    assert v.raw == {"how": "chg_fhir", "listed": True, "closed": False, "status": "active"}


def test_chg_inactive_or_gone_is_closed(monkeypatch):
    url = verify.CHG_FHIR + "Location/CHG1"
    monkeypatch.setattr(verify, "_record", lambda v: None)
    monkeypatch.setattr(verify.httpx, "get", lambda u, headers, timeout: httpx.Response(200, json={"resourceType": "Location", "status": "inactive"}))
    assert verify.verify_live(_loc(source_url=url)).open_now is False
    monkeypatch.setattr(verify.httpx, "get", lambda u, headers, timeout: httpx.Response(404))
    assert verify.verify_live(_loc(source_url=url)).open_now is False


@pytest.mark.skipif(not __import__("os").getenv("RUN_LIVE_CHG"), reason="set RUN_LIVE_CHG=1 to hit the CHG API")
def test_live_chg():
    v = verify.check_chg(_loc(source_url=verify.CHG_FHIR + "Location/CHG5064112"), 10)
    assert v["listed"] and not v["closed"]
