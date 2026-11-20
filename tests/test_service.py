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
    assert (a["red_score"], a["blue_score"]) == ("7", "3")
    assert a["winner_side"] == "red" and a["winner_name"] == "IVANOV Ivan"
    assert a["victory_type"] == "VPO1"
    assert b["has_fight"] == "0" and b["red_name"] == "" and a["has_fight"] == "1"
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
    assert row["arena_ok"] == "1"
    assert row["A_red_name"] == "IVANOV Ivan" and row["A_timer"] == "1:35"
    assert row["B_has_fight"] == "0"
    (row,) = asyncio.run(main.vmix_flat(key="index"))
    assert row["mat1_blue_country"] == "USA" and "mat2_red_score" in row


def _all_clean_strings(d):
    for k, v in d.items():
        assert isinstance(v, str), (k, v)
        assert v == v.strip() and "  " not in v, (k, v)
        assert not any(ord(c) < 32 or c in "\u200b\ufeff\ufffd\x7f" for c in v), (k, v)
        assert v.lower() not in ("null", "none", "true", "false", "undefined", "nan"), (k, v)
        assert "&amp;" not in v and "Ð" not in v and "<b>" not in v, (k, v)


NASTY = {
    "id": None, "fightNumber": None, "status": None, "currentRound": {"name": None}, "timer": "NaN",
    "sportEventWeightCategory": {"name": "FS\n65 kg\t", "maxWeight": 65.0},
    "fighter1": {"id": 1, "displayName": "  IVANOV\u200b  Ivan\x00 ", "teamAlternateName": "null",
                 "athlete": {"team": {"alternateName": "kaz"}}},
    "fighter2": {"id": 2, "athlete": {"personFullName": "Ð˜Ð²Ð°Ð½Ð¾Ð² Ð\x9dÐ¸ÐºÐ¾Ð»Ð°Ð¹",
                                       "team": {"alternateName": "RUS", "name": "Tom &amp;amp; Jerry"}}},
    "fighter1Score": None, "fighter2Score": True, "fighter1RankingPoint": 3.0, "fighter2RankingPoint": "-1",
    "winnerFighter": True, "victoryType": {"id": None}, "roundName": ["1/8", None],
}


def test_nasty_payload_is_air_safe():
    r = normalize_fight({"id": None, "name": None}, NASTY, load_mapping())
    _all_clean_strings(r)
    assert r["red_name"] == "IVANOV Ivan"
    assert r["red_country"] == "kaz" and r["red_flag"] == "KAZ.png"   # skipped "null", took next path
    assert r["blue_name"] == "Иванов Николай"                         # mojibake repaired
    assert r["blue_country_name"] == "Tom & Jerry"
    assert (r["red_score"], r["blue_score"], r["red_cp"], r["blue_cp"]) == ("0", "0", "3", "0")
    assert r["weight_category"] == "FS 65 kg" and r["weight"] == "65"
    assert r["fight_round"] == "1/8" and r["timer"] == "" and r["winner_side"] == ""
    assert r["fight_number"] == "" and r["status"] == "" and r["period"] == ""


def test_legit_names_untouched():
    m = load_mapping()
    for name in ("João DA SILVA", "MÜLLER Jürgen", "ÖZTÜRK Çağrı", "ДЖАЛАЛОВ Ғани", "ÑANDÚ José", "O'NEIL Mary-Ann"):
        r = normalize_fight({"id": "m", "name": "A"}, {"fighter1": {"displayName": name}}, m)
        assert r["red_name"] == name


def test_flat_document_is_air_safe(mocked, monkeypatch):
    global FIGHT_A
    monkeypatch.setitem(FIGHT_A, "fighter1", NASTY["fighter1"])
    asyncio.run(main.refresh_once())
    (doc,) = asyncio.run(main.vmix_flat())
    _all_clean_strings(doc)


def test_mat_prefixes_safe_and_unique():
    rows = [{"mat": "Ковёр 1"}, {"mat": "1"}, {"mat": "A"}, {"mat": "a "}, {"mat": None}, {"mat": "A"}]
    used: set = set()
    got = [main._prefix(r, i, "name", used) for i, r in enumerate(rows, 1)]
    assert got == ["mat1", "mat1_2", "A", "a", "mat5", "A_6"]


def test_failed_mat_keeps_last_row(mocked, monkeypatch):
    asyncio.run(main.refresh_once())
    before = main.state.rows[0]

    async def boom(mat_id):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(mocked, "open_fight", boom)
    asyncio.run(main.refresh_once())
    assert main.state.rows[0] == before and before["red_name"] == "IVANOV Ivan"


def test_fuzz_never_crashes_never_leaks_junk():
    import random

    rnd = random.Random(42)
    junk = [None, True, False, 0, -3, 2.5, float("nan"), float("inf"), "", " ", "null", "None", "\x00\x07",
            "​", "﻿", "�", "&lt;b&gt;", "a\r\nb", "\t", "ÐŸÑ€Ð¸Ð²ÐµÑ‚", [], {}, [None, "x"],
            {"name": None}, {"id": True}, {"alternateName": " RUS "}, "12:34", "999999", "-5", "1e309"]
    m = load_mapping()
    paths = sorted({p.replace("{n}", str(n)) for ps in m.values() for p in ps for n in (1, 2)})

    def put(d, path, v):
        parts = path.split(".")
        for part in parts[:-1]:
            if part.isdigit():
                return
            nxt = d.get(part)
            if not isinstance(nxt, dict):
                nxt = d[part] = {}
            d = nxt
        if not parts[-1].isdigit():
            d[parts[-1]] = v

    for _ in range(3000):
        fight = {}
        for p in rnd.sample(paths, rnd.randint(1, len(paths))):
            put(fight, p, rnd.choice(junk))
        mat = {"id": rnd.choice(junk), "name": rnd.choice(junk)}
        _all_clean_strings(normalize_fight(mat, fight, m, rnd.choice(["s", "ms"])))
