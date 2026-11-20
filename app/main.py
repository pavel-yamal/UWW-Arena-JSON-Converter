"""UWW Arena -> vMix JSON data source.

A background loop polls Arena (open fight on every mat of the current event)
and keeps a normalized snapshot in memory; vMix polls this service.

Endpoints
  GET /vmix                 ONE document for vMix: all mats, columns prefixed by mat (A_red_name, B_timer)
  GET /vmix/rows            same data, one row per mat
  GET /vmix/mat/{mat}       one mat by name or id (array with one row)
  GET /status               poller state, last error
  GET /raw/mats             raw Arena mat list         (ENABLE_RAW=1)
  GET /raw/fight/{mat_id}   raw Arena open-fight object (ENABLE_RAW=1)
  GET /raw/api/...          raw passthrough of any Arena GET route (ENABLE_RAW=1)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .arena import ArenaClient
from .config import Settings
from .normalize import load_mapping, normalize_fight
from .sanitize import ascii_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vmix")


class State:
    def __init__(self) -> None:
        self.event_id: str | None = None
        self.mats: list[dict] = []
        self.mats_loaded_at = 0.0
        self.rows: list[dict] = []
        self.updated_at = 0.0
        self.last_error: str | None = None


settings = Settings()
mapping = load_mapping(settings.mapping_file)
state = State()
client: ArenaClient | None = None


def _mat_selected(mat: dict) -> bool:
    if not settings.mats:
        return True
    keys = {str(mat.get(k)) for k in ("id", "name", "number") if mat.get(k) is not None}
    return bool(keys & set(settings.mats))


async def refresh_once() -> None:
    assert client
    now = time.time()
    if not state.event_id or not state.mats or now - state.mats_loaded_at > settings.mats_refresh:
        state.event_id = await client.current_event_id()
        if not state.event_id:
            raise RuntimeError("Arena event not found: set ARENA_EVENT_ID (current-event is ambiguous or empty)")
        state.mats = [m for m in await client.mats(state.event_id) if _mat_selected(m)]
        state.mats_loaded_at = now
        log.info("event %s, mats: %s", state.event_id, [m.get("name") for m in state.mats])

    fights = await asyncio.gather(*(client.open_fight(str(m["id"])) for m in state.mats), return_exceptions=True)
    previous = {r["mat_id"]: r for r in state.rows}
    rows = []
    for mat, fight in zip(state.mats, fights):
        if isinstance(fight, Exception):
            # one failed request must not blank the mat on air: keep its last row
            log.warning("mat %s: %s", mat.get("name"), fight)
            old = previous.get(str(mat.get("id")))
            rows.append(old or normalize_fight(mat, None, mapping, settings.timer_unit))
            continue
        try:
            rows.append(normalize_fight(mat, fight, mapping, settings.timer_unit))
        except Exception:
            log.exception("mat %s: cannot normalize fight", mat.get("name"))
            rows.append(previous.get(str(mat.get("id"))) or normalize_fight(mat, None, mapping, settings.timer_unit))
    state.rows = rows
    state.updated_at = time.time()


async def poll_loop() -> None:
    while True:
        try:
            await refresh_once()
            state.last_error = None
        except Exception as e:  # keep serving the last snapshot
            state.last_error = f"{type(e).__name__}: {e}"
            log.warning("poll failed: %s", state.last_error)
            state.mats_loaded_at = 0.0
        await asyncio.sleep(settings.poll_interval)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = ArenaClient(settings)
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    await client.close()


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="UWW Arena -> vMix", lifespan=lifespan, default_response_class=UTF8JSONResponse)


@app.get("/vmix/rows")
async def vmix_rows() -> list[dict]:
    return state.rows


def _prefix(row: dict, index: int, key: str, used: set[str]) -> str:
    """Column prefix: ASCII only, never starts with a digit, never repeats."""
    name = "" if key == "index" else ascii_key(row.get("mat", ""))
    if not name:
        name = f"mat{index}"
    elif name[0].isdigit():
        name = f"mat{name}"
    if name in used:
        name = f"{name}_{index}"
    used.add(name)
    return name


@app.get("/vmix")
async def vmix_flat(key: str = "") -> list[dict]:
    """All mats in ONE row: columns are prefixed with the mat (A_red_name, B_timer, …).

    key=name  -> prefix from mat name ("A_", "B_", "mat1_" for numeric names)
    key=index -> prefix by position ("mat1_", "mat2_", …)
    """
    key = key or settings.vmix_key
    flat: dict[str, str] = {
        "updated_at": time.strftime("%H:%M:%S", time.localtime(state.updated_at)) if state.updated_at else "",
        "arena_ok": "1" if state.updated_at and state.last_error is None else "0",
    }
    used: set[str] = set()
    for i, row in enumerate(state.rows, start=1):
        p = _prefix(row, i, key, used)
        for k, v in row.items():
            flat[f"{p}_{k}"] = v
    return [flat]


@app.get("/vmix/mat/{mat}")
async def vmix_mat(mat: str) -> list[dict]:
    for r in state.rows:
        if mat in (str(r.get("mat")), str(r.get("mat_id"))):
            return [r]
    raise HTTPException(404, f"mat {mat!r} not found; known: {[r.get('mat') for r in state.rows]}")


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "arena_url": settings.arena_url,
        "event_id": state.event_id,
        "mats": [{"id": m.get("id"), "name": m.get("name")} for m in state.mats],
        "updated_at": state.updated_at,
        "age_seconds": round(time.time() - state.updated_at, 1) if state.updated_at else None,
        "last_error": state.last_error,
    }


def _require_raw() -> None:
    if not settings.enable_raw:
        raise HTTPException(404)


@app.get("/raw/mats")
async def raw_mats() -> Any:
    _require_raw()
    assert client
    event_id = state.event_id or await client.current_event_id()
    return await client.get(f"/api/json/mat/{event_id}")


@app.get("/raw/fight/{mat_id}")
async def raw_fight(mat_id: str) -> Any:
    _require_raw()
    assert client
    return await client.open_fight(mat_id)


@app.get("/raw/api/{path:path}")
async def raw_passthrough(path: str) -> Any:
    _require_raw()
    assert client
    return await client.get(f"/api/{path}")
