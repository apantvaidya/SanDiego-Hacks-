"""Tests for the #43 local chat page. The router is faked (no database, no Gemini)."""
import base64

import pytest
from fastapi.testclient import TestClient

import app.messaging as messaging
from app.conversation import router
from app.local import server


@pytest.fixture
def client(monkeypatch):
    seen = []

    def fake_handle(msg):
        seen.append(msg)
        if msg.media_urls:
            return [f"PHOTO:{len(messaging.download_media(msg.media_urls[0]))}"]
        if msg.text == "boom":
            raise RuntimeError("down")
        return [f"ECHO:{msg.text}", "second"]

    monkeypatch.setattr(router, "handle", fake_handle)
    monkeypatch.setattr(messaging, "download_media", lambda url: b"provider")
    return TestClient(server.create_app()), seen


def test_page_serves_html(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "Care Navigator" in r.text and "/send" in r.text


def test_send_goes_through_router(client):
    c, seen = client
    r = c.post("/send", json={"phone": "+15551234567", "text": "  think i sprained my ankle "})
    assert r.json() == {"replies": ["ECHO:think i sprained my ankle", "second"]}
    assert seen[0].phone == "+15551234567" and seen[0].message_id.startswith("local-")


@pytest.mark.parametrize("phone", ["+16195551234", "+1555123", "555"])
def test_only_fake_555_numbers(client, phone):
    c, _ = client
    assert c.post("/send", json={"phone": phone, "text": "hi"}).status_code == 400


def test_empty_message_rejected(client):
    c, _ = client
    assert c.post("/send", json={"phone": "+15551234567", "text": "  "}).status_code == 400


def test_photo_is_read_once_then_dropped(client):
    c, _ = client
    data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"x" * 97).decode()
    r = c.post("/send", json={"phone": "+15551234567", "image_b64": data_url})
    assert r.json() == {"replies": ["PHOTO:100"]}
    assert server._media == {}


def test_non_local_urls_go_to_provider(client):
    assert messaging.download_media("https://cdn.example/x.jpg") == b"provider"


def test_bad_base64_rejected(client):
    c, _ = client
    assert c.post("/send", json={"phone": "+15551234567", "image_b64": "not base64!!"}).status_code == 400


def test_router_error_gives_safe_reply(client):
    c, _ = client
    assert "call 911" in c.post("/send", json={"phone": "+15551234567", "text": "boom"}).json()["replies"][0]
