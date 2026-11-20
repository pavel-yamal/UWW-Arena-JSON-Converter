"""HTTP client for the UWW Arena REST API (OpenAPI "Arena API" 1.7.x).

Auth (per UWW's official client github.com/unitedworldwrestling/oauth2-arena):
  POST /oauth/v2/token  grant_type=https://arena.uww.io/grants/api_key
                        client_id, client_secret, api_key, scope=read
  -> {"access_token": ..., "expires_in": ...}, then "Authorization: Bearer <token>".
Without ARENA_API_KEY the standard client_credentials grant is used.
With neither client id nor api key, requests go without auth (or with a static
ARENA_API_TOKEN, if given).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("arena")


class ArenaClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = httpx.AsyncClient(
            base_url=settings.arena_url.rstrip("/"),
            timeout=settings.http_timeout,
            verify=settings.arena_verify_tls,
        )
        self._token: str | None = settings.arena_api_token or None
        self._token_expires = float("inf") if self._token else 0.0

    async def close(self) -> None:
        await self._http.aclose()

    # ---------------------------------------------------------------- auth
    def _uses_oauth(self) -> bool:
        return bool(self.s.arena_client_id or self.s.arena_api_key)

    def _token_request(self) -> dict[str, str]:
        data = {"grant_type": self.s.arena_grant_type, "scope": self.s.arena_scope}
        if self.s.arena_client_id:
            data["client_id"] = self.s.arena_client_id
            data["client_secret"] = self.s.arena_client_secret
        if self.s.arena_api_key:
            data["api_key"] = self.s.arena_api_key
        return {k: v for k, v in data.items() if v}

    async def _ensure_token(self) -> None:
        if not self._uses_oauth():
            return
        if self._token and time.time() < self._token_expires - 30:
            return
        data = self._token_request()
        r = await self._http.post(self.s.arena_token_path, data=data)
        if r.status_code >= 400:
            # some OAuth servers only accept GET with query params
            r = await self._http.get(self.s.arena_token_path, params=data)
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + float(body.get("expires_in", 3600))
        log.info("Arena OAuth token obtained, expires in %ss", body.get("expires_in"))

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ---------------------------------------------------------------- calls
    async def get(self, path: str) -> Any:
        """GET an API path. `path` may contain `{_format}`; it is replaced by `json`."""
        path = path.replace("{_format}", "json")
        await self._ensure_token()
        r = await self._http.get(path, headers=self._headers())
        if r.status_code == 401 and self._uses_oauth():
            self._token, self._token_expires = None, 0.0
            await self._ensure_token()
            r = await self._http.get(path, headers=self._headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        if not r.content:
            return None
        return r.json()

    async def current_event_id(self) -> str | None:
        if self.s.arena_event_id:
            return self.s.arena_event_id
        data = await self.get("/api/json/arena/current-event")
        return find_id(data, ("sportEvent", "event", "currentEvent"))

    async def mats(self, event_id: str) -> list[dict]:
        data = await self.get(f"/api/json/mat/{event_id}")
        if data is None:
            data = await self.get(f"/api/json/scoreboard/mat/{event_id}")
        return first_list(data)

    async def open_fight(self, mat_id: str) -> dict | None:
        """Fight currently open on a mat (scoreboard route first, then the generic one)."""
        for path in (f"/api/json/scoreboard/fight/open/{mat_id}", f"/api/json/fight/open/{mat_id}"):
            try:
                data = await self.get(path)
            except httpx.HTTPStatusError as e:
                log.debug("%s -> %s", path, e.response.status_code)
                continue
            fight = unwrap_object(data, ("fight", "openFight", "data"))
            if fight:
                return fight
        return None


# ------------------------------------------------------------------ helpers
def first_list(data: Any) -> list[dict]:
    """Arena responses are either a bare list or an object wrapping one list."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return [x for x in v if isinstance(x, dict)]
    return []


def unwrap_object(data: Any, keys: tuple[str, ...]) -> dict | None:
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data:
        return None
    for k in keys:
        if isinstance(data.get(k), dict):
            return data[k]
    if len(data) == 1:
        (v,) = data.values()
        if isinstance(v, dict):
            return v
    return data


def find_id(data: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, dict):
        return None
    if data.get("id"):
        return str(data["id"])
    for k in keys:
        if isinstance(data.get(k), dict) and data[k].get("id"):
            return str(data[k]["id"])
    return None
