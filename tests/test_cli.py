from __future__ import annotations

import json

import pytest

from tsd import cli


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    config = tmp_path / "routes.json"
    caddy = tmp_path / "routes.caddy"
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", config)
    monkeypatch.setattr(cli, "DEFAULT_CADDY", caddy)
    monkeypatch.setattr(cli, "DEFAULT_DEVICE_DOMAIN", "mx.ts.harun.dev")
    monkeypatch.setattr(cli, "DEFAULT_UPSTREAM_HOST", "127.0.0.1")
    monkeypatch.setattr(cli, "CADDY_CONTAINER", "does-not-exist")
    return config, caddy


def test_help_mentions_add_and_rm(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["-h"])
    out = capsys.readouterr().out
    assert "tsd init" in out
    assert "tsd add" in out
    assert "tsd rm" in out


def test_add_list_apply_roundtrip(temp_env, monkeypatch, capsys):
    config, caddy = temp_env
    monkeypatch.setattr(cli, "restart_caddy", lambda: None)
    answers = iter(["is", "9010", "/Users/rayhan/Code/instagram-slides", "/instagram-slides"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.cmd_add(type("A", (), {"name": None, "port": None, "workdir": "", "path": None})()) == 0
    assert config.exists()
    data = json.loads(config.read_text())
    assert data["device_domain"] == "mx.ts.harun.dev"
    assert data["routes"]["is"]["port"] == 9010
    assert data["routes"]["is"]["path"] == "/instagram-slides"
    assert "reverse_proxy 127.0.0.1:9010" in caddy.read_text()
    capsys.readouterr()
    assert cli.cmd_list(type("A", (), {})()) == 0
    out = capsys.readouterr().out
    assert "domain=is.mx.ts.harun.dev" in out
    assert "path=/instagram-slides" in out


def test_duplicate_add_prompts_and_can_abort(temp_env, monkeypatch, capsys):
    config, _ = temp_env
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"device_domain": "mx.ts.harun.dev", "routes": {"is": {"port": 9010, "workdir": "/x", "path": ""}}}))
    monkeypatch.setattr(cli, "restart_caddy", lambda: None)
    answers = iter(["is", "9011", "/Users/rayhan/Code/instagram-slides", "", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.cmd_add(type("A", (), {"name": None, "port": None, "workdir": "", "path": None})()) == 0
    out = capsys.readouterr().out
    assert "existing route:" in out
    assert "aborted" in out
    data = json.loads(config.read_text())
    assert data["routes"]["is"]["port"] == 9010


def test_remove_shows_routes_and_removes_first_by_default(temp_env, monkeypatch, capsys):
    config, _ = temp_env
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "device_domain": "mx.ts.harun.dev",
        "routes": {
            "alpha": {"port": 1111, "workdir": "/a", "path": ""},
            "is": {"port": 9010, "workdir": "/x", "path": ""},
        },
    }))
    monkeypatch.setattr(cli, "restart_caddy", lambda: None)
    answers = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.cmd_remove(type("A", (), {"name": None})()) == 0
    out = capsys.readouterr().out
    assert "current routes:" in out
    assert "selected: alpha.mx.ts.harun.dev -> 1111" in out
    assert "removed alpha.mx.ts.harun.dev" in out
    data = json.loads(config.read_text())
    assert "alpha" not in data["routes"]
    assert "is" in data["routes"]


def test_init_creates_config(temp_env, monkeypatch):
    config, _ = temp_env
    monkeypatch.setattr("builtins.input", lambda prompt="": "mx.ts.harun.dev")
    assert cli.cmd_init(type("A", (), {})()) == 0
    data = json.loads(config.read_text())
    assert data["device_domain"] == "mx.ts.harun.dev"
