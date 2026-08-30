# SPDX-License-Identifier: MIT
"""HTTP/WebSocket surface, including authentication."""

from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from walle import protocol as proto
from walle.config import Config, LlmConfig, ServerConfig, SttConfig, TtsConfig, VisionConfig

TOKEN = "test-token-that-is-long-enough"


@pytest.fixture
def app_config(tmp_path):
    return Config(
        server=ServerConfig(auth_token=TOKEN, data_dir=tmp_path / "data", log_level="WARNING"),
        # Point every engine at something that will not be found, so no
        # subprocess or network call is ever attempted in a test.
        stt=SttConfig(backend="whispercpp", binary="/nonexistent", model_path="/nonexistent"),
        tts=TtsConfig(backend="piper", binary="/nonexistent", model_path="/nonexistent"),
        llm=LlmConfig(backend="ollama", ollama_url="http://127.0.0.1:1"),
        vision=VisionConfig(enabled=False),
    )


@pytest.fixture
def client(app_config):
    from walle.app import create_app

    with TestClient(create_app(app_config)) as test_client:
        yield test_client


def test_health_reports_the_engines(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["protocol"] == proto.PROTO_VERSION
    assert body["engines"]["stt"] == "whisper.cpp"
    assert body["engines"]["smarthome"] is None


def test_websocket_requires_a_token(client):
    # The server closes with 1008 (policy violation) before accepting, so the
    # first read raises rather than the connect call itself.
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
        ws.receive_text()


def test_websocket_rejects_a_wrong_token(client):
    headers = {"X-Walle-Token": "wrong"}
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws", headers=headers) as ws:
        ws.receive_text()


def test_websocket_accepts_the_right_token_and_acks_hello(client):
    headers = {"X-Walle-Token": TOKEN, "X-Walle-Device": "walle-test"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        ws.send_text(proto.encode_json(proto.MSG_HELLO, fw="1.0.0",
                                       proto=proto.PROTO_VERSION, caps=["mic"]))
        ack = proto.decode_json(ws.receive_text())
        assert ack["t"] == proto.MSG_HELLO_ACK
        assert ack["proto"] == proto.PROTO_VERSION


def test_token_can_be_passed_as_a_query_parameter(client):
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_text(proto.encode_json(proto.MSG_HELLO, proto=proto.PROTO_VERSION))
        assert proto.decode_json(ws.receive_text())["t"] == proto.MSG_HELLO_ACK


def test_connected_device_appears_in_health(client):
    headers = {"X-Walle-Token": TOKEN, "X-Walle-Device": "walle-42"}
    with client.websocket_connect("/ws", headers=headers):
        assert "walle-42" in client.get("/health").json()["devices_online"]


def test_events_endpoint_is_empty_initially(client):
    assert client.get("/events").json() == {"events": []}


def test_events_limit_is_validated(client):
    assert client.get("/events?limit=0").status_code == 422
    assert client.get("/events?limit=9999").status_code == 422


def test_history_endpoint(client):
    body = client.get("/history?device=walle-test").json()
    assert body == {"device": "walle-test", "turns": []}


def test_malformed_control_frame_does_not_kill_the_socket(client):
    headers = {"X-Walle-Token": TOKEN, "X-Walle-Device": "walle-test"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        ws.send_text("{ this is not json")
        # The socket must survive and still answer a valid message.
        ws.send_text(proto.encode_json(proto.MSG_HELLO, proto=proto.PROTO_VERSION))
        assert proto.decode_json(ws.receive_text())["t"] == proto.MSG_HELLO_ACK
