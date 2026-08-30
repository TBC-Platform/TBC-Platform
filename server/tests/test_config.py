# SPDX-License-Identifier: MIT
"""Configuration loading and validation."""

from __future__ import annotations

from walle.config import Config, ServerConfig, SmartHomeConfig


def test_defaults_are_safe():
    cfg = Config()
    # Smart home off, no auth bypass: nothing surprising happens out of the box.
    assert cfg.smarthome.enabled is False
    assert cfg.server.allow_no_auth is False
    assert "lock" not in cfg.smarthome.allowed_domains
    assert "cover" not in cfg.smarthome.allowed_domains


def test_missing_auth_token_is_a_validation_error(monkeypatch):
    monkeypatch.delenv("WALLE_AUTH_TOKEN", raising=False)
    problems = Config(server=ServerConfig(auth_token="")).validate()
    assert any("WALLE_AUTH_TOKEN is not set" in p for p in problems)


def test_short_token_is_flagged():
    problems = Config(server=ServerConfig(auth_token="short")).validate()
    assert any("shorter than 16" in p for p in problems)


def test_allow_no_auth_suppresses_the_token_error():
    problems = Config(server=ServerConfig(auth_token="", allow_no_auth=True)).validate()
    assert not any("WALLE_AUTH_TOKEN is not set" in p for p in problems)


def test_smart_home_without_an_allowlist_is_flagged():
    cfg = Config(
        server=ServerConfig(auth_token="a" * 32),
        smarthome=SmartHomeConfig(enabled=True, ha_token="t" * 32, allowed_entities=[]),
    )
    assert any("WALLE_ALLOWED_ENTITIES is empty" in p for p in cfg.validate())


def test_dotenv_does_not_override_the_real_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("WALLE_PORT=9999\nWALLE_LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("WALLE_PORT", "1234")
    cfg = Config.load(dotenv=env_file)
    assert cfg.server.port == 1234        # real env wins
    assert cfg.server.log_level == "DEBUG"  # .env fills the gap


def test_bad_integer_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("WALLE_PORT", "not-a-number")
    assert Config().server.port == 8765
