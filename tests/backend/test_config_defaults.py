"""The lobby defaults, and that no number a player or host sends can be negative."""
from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import BaseModel

from tank_tactics.config import ConfigError, GameConfig

from .test_api import auth, new_game


def test_new_game_defaults():
    c = GameConfig()
    assert c.start.ap == 0
    assert c.actions.loot_on_kill is False
    assert c.jury.enabled is True
    assert c.max_duration_seconds is None


def int_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Dotted paths of every int (or optional int) setting, so new ones are covered too."""
    out = []
    for name, field in model.model_fields.items():
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out += int_paths(ann, f"{prefix}{name}.")
        elif ann is int or int in get_args(ann):
            out.append(prefix + name)
    return out


def nested(path: str, value: Any) -> dict:
    *sections, key = path.split(".")
    out: dict = {key: value}
    for s in reversed(sections):
        out = {s: out}
    return out


INT_PATHS = int_paths(GameConfig)


def test_every_number_setting_is_found():
    assert {"start.ap", "start.hp", "ap.cap", "max_duration_seconds", "board.size_multiplier"} <= set(INT_PATHS)


@pytest.mark.parametrize("path", INT_PATHS)
@pytest.mark.parametrize("value", [-1, -86_400])
def test_negative_config_numbers_are_rejected(path, value):
    with pytest.raises(ConfigError) as e:
        GameConfig().with_overrides(nested(path, value))
    assert path in str(e.value)


def test_negative_config_numbers_are_rejected_by_the_api(api):
    client, _, _ = api
    for path in INT_PATHS:
        r = client.post("/api/games", json={"name": "Ann", "config": nested(path, -1)})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_config", path
    host = client.post("/api/games", json={"name": "Ann"}).json()
    r = client.post(f"/api/games/{host['game_id']}/config", json={"config": {"start": {"ap": -1}}}, headers=auth(host))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_config"


@pytest.mark.parametrize("kind", ["give_ap", "give_hp"])
def test_negative_gift_amounts_are_rejected_by_the_api(api, kind):
    client, svc, _ = api
    ann, bob, _ = new_game(client)
    gid = ann["game_id"]
    assert client.post(f"/api/games/{gid}/start", headers=auth(ann)).status_code == 200
    before = [(pl.hp, pl.ap) for pl in svc.get(gid).players]
    r = client.post(f"/api/games/{gid}/action", headers=auth(ann),
                    json={"type": kind, "target_id": bob["player_id"], "amount": -1})
    assert r.status_code == 400, r.text
    assert [(pl.hp, pl.ap) for pl in svc.get(gid).players] == before

