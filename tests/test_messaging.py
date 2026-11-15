from fastapi.testclient import TestClient

from app.messaging import handler, sendblue

INBOUND = {
    "accountEmail": "x@example.com",
    "content": "think i sprained my ankle",
    "is_outbound": False,
    "status": "RECEIVED",
    "message_handle": "AAA-111",
    "date_sent": "2026-11-15T07:41:20.932Z",
    "from_number": "+15550001111",
    "number": "+15550001111",
    "to_number": "+15550002222",
    "media_url": "",
    "message_type": "message",
    "group_id": "",
    "service": "iMessage",
}


def test_parse_inbound_text():
    msg = sendblue.parse_inbound(INBOUND)
    assert msg.phone == "+15550001111"
    assert msg.text == "think i sprained my ankle"
    assert msg.media_urls == []
    assert msg.message_id == "AAA-111"
    assert msg.received_at.year == 2026


def test_parse_inbound_media():
    msg = sendblue.parse_inbound({**INBOUND, "content": "", "media_url": "https://cdn.example.com/card.jpg"})
    assert msg.media_urls == ["https://cdn.example.com/card.jpg"]


def test_parse_ignores_outbound_and_groups():
    assert sendblue.parse_inbound({**INBOUND, "is_outbound": True}) is None
    assert sendblue.parse_inbound({**INBOUND, "group_id": "g1"}) is None


def test_dedupe():
    assert handler.already_seen("dup-1") is False
    assert handler.already_seen("dup-1") is True
    assert handler.already_seen(None) is False


def test_handle_inbound_echoes_until_router_exists(monkeypatch):
    sent = []
    monkeypatch.setattr(handler, "send_text", lambda phone, text: sent.append((phone, text)))
    monkeypatch.setattr(handler, "download_media", lambda url: b"x" * 4096)
    msg = sendblue.parse_inbound({**INBOUND, "message_handle": "echo-1", "media_url": "https://cdn/x.jpg"})
    handler.handle_inbound(msg)
    handler.handle_inbound(msg)  # retry is ignored
    assert len(sent) == 1
    assert sent[0][0] == "+15550001111"
    assert "sprained" in sent[0][1] and "4 KB" in sent[0][1]


def test_webhook_returns_fast_and_ignores_outbound(monkeypatch):
    from app import main

    calls = []
    monkeypatch.setattr(main, "handle_inbound", lambda msg: calls.append(msg))
    client = TestClient(main.app)
    assert client.post("/webhooks/sendblue", json={**INBOUND, "message_handle": "wh-1"}).json() == {"ok": True}
    assert client.post("/webhooks/sendblue", json={**INBOUND, "is_outbound": True}).json()["ignored"] is True
    assert len(calls) == 1


def test_webhook_secret(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "sendblue_webhook_secret", "s3cret")
    monkeypatch.setattr(main, "handle_inbound", lambda msg: None)
    client = TestClient(main.app)
    assert client.post("/webhooks/sendblue", json=INBOUND).status_code == 401
    assert client.post("/webhooks/sendblue", json=INBOUND, headers={"sb-signing-secret": "s3cret"}).status_code == 200
