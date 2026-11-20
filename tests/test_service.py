"""End-to-end test against a mocked Arena (httpx.MockTransport).

The fight payload shape here is an assumption (the spec has no schemas);
it exercises the mapping fallbacks, OAuth and winner detection.
"""
import asyncio

import httpx
import pytest

from app import main
from app.arena import ArenaClient
from app.config import Settings
from app.normalize import format_timer, load_mapping, normalize_fight

EVENT = "11111111-2222-3333-4444-555555555555"

FIGHT_A = {
    "id": "f-1",
    "fightNumber": 12,
    "status": 3,
    "currentRound": 2,
    "timer": 95,
    "sportEventWeightCategory": {"name": "FS 65 kg", "maxWeight": 65},
    "fighter1": {"id": "x1", "athlete": {"personFullName": "IVANOV Ivan", "team": {"alternateName": "AIN", "name": "Individual Neutral Athletes"}}},
    "fighter2": {"id": "x2", "displayName": "SMITH John", "teamAlternateName": "USA"},
    "fighter1Score": 7,
    "fighter2Score": "3",
    "winnerFighter": {"id": "x1"},
    "victoryType": {"alternateName": "VPO1", "name": "Points"},
}


def arena_handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/oauth/v2/token":
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    assert request.headers.get("authorization") == "Bearer tok"
    if p == "/api/json/arena/current-event":
        return httpx.Response(200, json={"sportEvent": {"id": EVENT}})
    if p == f"/api/json/mat/{EVENT}":
        return httpx.Response(200, json={"sportEventMats": [{"id": "m1", "name": "A"}, {"id": "m2", "name": "B"}]})
    if p == "/api/json/scoreboard/fight/open/m1":
        return httpx.Response(200, json={"fight": FIGHT_A})
    if p == "/api/json/scoreboard/fight/open/m2":
        return httpx.Response(404)
    if p == "/api/json/fight/open/m2":
        return httpx.Response(200, content=b"")
    return httpx.Response(500, text=p)


@pytest.fixture
def mocked(monkeypatch):
    s = Settings(arena_url="http://arena", arena_client_id="id", arena_client_secret="sec")
    c = ArenaClient(s)
    c._http = httpx.AsyncClient(base_url="http://arena", transport=httpx.MockTransport(arena_handler))
    monkeypatch.setattr(main, "settings", s)
    monkeypatch.setattr(main, "client", c)
    monkeypatch.setattr(main, "state", main.State())
    return c


def test_refresh_builds_rows(mocked):
    asyncio.run(main.refresh_once())
    rows = main.state.rows
    assert [r["mat"] for r in rows] == ["A", "B"]
    a, b = rows
    assert a["timer"] == "1:35"
    assert a["weight_category"] == "FS 65 kg"
    assert a["red_name"] == "IVANOV Ivan" and a["red_country"] == "AIN"
    assert a["blue_name"] == "SMITH John" and a["blue_country"] == "USA"
    assert (a["red_score"], a["blue_score"]) == (7, 3)
    assert a["winner_side"] == "red" and a["winner_name"] == "IVANOV Ivan"
    assert a["victory_type"] == "VPO1"
    assert b["has_fight"] is False and b["red_name"] == ""
    assert set(a) == set(b)  # stable columns for vMix


def test_winner_variants():
    m = load_mapping()
    mat = {"id": "m", "name": "A"}
    base = {"fighter1": {"id": 5}, "fighter2": {"id": 6}}
    assert normalize_fight(mat, {**base, "winnerFighter": 6}, m)["winner_side"] == "blue"
    assert normalize_fight(mat, {**base, "winner": "red"}, m)["winner_side"] == "red"
    assert normalize_fight(mat, base, m)["winner_side"] == ""


def test_timer_format():
    assert format_timer(0) == "0:00"
    assert format_timer(180) == "3:00"
    assert format_timer(65500, "ms") == "1:05"
    assert format_timer("2:59") == "2:59"
    assert format_timer(None) == ""


def test_flat_all_mats_one_row(mocked):
    asyncio.run(main.refresh_once())
    (row,) = asyncio.run(main.vmix_flat())
    assert row["A_red_name"] == "IVANOV Ivan" and row["A_timer"] == "1:35"
    assert row["B_has_fight"] is False
    (row,) = asyncio.run(main.vmix_flat(key="index"))
    assert row["mat1_blue_country"] == "USA" and "mat2_red_score" in row
