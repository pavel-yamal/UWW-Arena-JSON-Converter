"""Turn an Arena fight object into a flat row for vMix.

The Arena OpenAPI spec has no response schemas, so field locations come from
mapping.default.json (overridable with MAPPING_FILE). Each field is a list of
dot-paths; the first non-empty one wins.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .sanitize import clean, clean_score, flag

DEFAULT_MAPPING = Path(__file__).with_name("mapping.default.json")

SIDES = {1: "red", 2: "blue"}


def load_mapping(override: str = "") -> dict[str, list[str]]:
    mapping = json.loads(DEFAULT_MAPPING.read_text(encoding="utf-8"))
    if override:
        mapping.update(json.loads(Path(override).read_text(encoding="utf-8-sig")))
    return {k: v for k, v in mapping.items() if not k.startswith("_")}


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def pick(obj: Any, paths: list[str], n: int | None = None) -> Any:
    for p in paths:
        if n is not None:
            p = p.replace("{n}", str(n))
        v = get_path(obj, p)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, str) and not clean(v):
            continue  # "null", "  ", "\u200b" … -> try the next path
        return v
    return None


def _scalar(v: Any) -> Any:
    """Objects (e.g. victoryType: {...}) -> their most readable field."""
    if isinstance(v, dict):
        for k in ("alternateName", "shortName", "code", "identifier", "name", "displayName", "fullName", "id"):
            if v.get(k) not in (None, ""):
                return v[k]
        return None
    return v


def format_timer(v: Any, unit: str = "s") -> str:
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, str):
        v = clean(v)
        if re.fullmatch(r"\d{1,3}:\d{2}", v):
            return v  # already "2:15"
    try:
        secs = float(v)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(secs):
        return ""
    if unit == "ms":
        secs /= 1000
    secs = max(0, int(secs))
    return f"{secs // 60}:{secs % 60:02d}"


def _num(v: Any) -> Any:
    v = _scalar(v)
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return v if v is not None else 0


def _winner_side(f: dict, mapping: dict, fighter_ids: dict[int, Any]) -> int | None:
    w = pick(f, mapping["winner"])
    if w is None:
        # per-fighter flags: fighter1.isWinner / fighter1.winner
        for n in (1, 2):
            if pick(f, [f"fighter{n}.isWinner", f"fighter{n}.winner", f"fighter{n}IsWinner"]) is True:
                return n
        return None
    if isinstance(w, bool):  # True == 1 in Python: must not mean "red"
        return None
    if isinstance(w, dict):
        w = w.get("id", w.get("fighterId"))
        if w is None or isinstance(w, bool):
            return None
    if isinstance(w, str):
        lw = w.lower()
        if lw in ("red", "r", "1", "fighter1"):
            return 1
        if lw in ("blue", "b", "2", "fighter2"):
            return 2
    for n, fid in fighter_ids.items():
        if fid is not None and str(fid) == str(w):
            return n
    if w in (1, 2):
        return w
    return None


TEXT_KEYS_SCORE = ("red_score", "blue_score", "red_cp", "blue_cp")


def normalize_fight(mat: dict, fight: dict | None, mapping: dict, timer_unit: str = "s") -> dict[str, str]:
    """Normalized row; every value is a clean string ("" when unknown)."""
    row = _normalize(mat, fight, mapping, timer_unit)
    out: dict[str, str] = {}
    for k, v in row.items():
        if k == "has_fight":
            out[k] = "1" if v else "0"
        elif k in TEXT_KEYS_SCORE and row["has_fight"]:
            out[k] = clean_score(v)
        else:
            out[k] = clean(v)
    for side in SIDES.values():
        out[f"{side}_flag"] = flag(out[f"{side}_country"])
    if out["winner_side"]:
        w = out["winner_side"]
        out["winner_name"], out["winner_country"] = out[f"{w}_name"], out[f"{w}_country"]
    if not out["weight_category"] and out["weight"]:
        out["weight_category"] = f"{out['weight']} kg"
    return out


def _normalize(mat: dict, fight: dict | None, mapping: dict, timer_unit: str) -> dict:
    row: dict[str, Any] = {
        "mat": mat.get("name") or mat.get("number") or mat.get("id"),
        "mat_id": mat.get("id"),
        "has_fight": bool(fight),
    }
    if not fight:
        row.update({k: "" for k in _empty_keys()})
        return row

    weight_name = _scalar(pick(fight, mapping["weight_category"]))
    weight = _scalar(pick(fight, mapping["weight"]))
    sport = _scalar(pick(fight, mapping["sport"]))
    if not weight_name and weight:
        weight_name = f"{weight} kg"

    row.update(
        {
            "fight_id": pick(fight, mapping["fight_id"]),
            "fight_number": _scalar(pick(fight, mapping["fight_number"])) or "",
            "fight_round": _scalar(pick(fight, mapping["fight_round_name"])) or "",
            "status": _scalar(pick(fight, mapping["status"])),
            "weight_category": weight_name or "",
            "weight": weight or "",
            "sport": sport or "",
            "period": _scalar(pick(fight, mapping["round"])) or "",
            "timer": format_timer(_scalar(pick(fight, mapping["timer"])), timer_unit),
        }
    )

    ids: dict[int, Any] = {}
    for n, side in SIDES.items():
        ids[n] = pick(fight, mapping["fighter_id"], n)
        last = _scalar(pick(fight, mapping["last_name"], n)) or ""
        first = _scalar(pick(fight, mapping["first_name"], n)) or ""
        full = _scalar(pick(fight, mapping["full_name"], n)) or " ".join(clean(x) for x in (last, first) if clean(x))
        row[f"{side}_name"] = full
        row[f"{side}_last_name"] = last
        row[f"{side}_first_name"] = first
        row[f"{side}_country"] = _scalar(pick(fight, mapping["country"], n)) or ""
        row[f"{side}_country_name"] = _scalar(pick(fight, mapping["country_name"], n)) or ""
        row[f"{side}_score"] = _num(pick(fight, mapping["score"], n))
        row[f"{side}_cp"] = _num(pick(fight, mapping["classification_points"], n))
        row[f"{side}_flag"] = ""

    w = _winner_side(fight, mapping, ids)
    row["winner_side"] = SIDES.get(w, "") if w else ""
    row["winner_name"] = row[f"{SIDES[w]}_name"] if w else ""
    row["winner_country"] = row[f"{SIDES[w]}_country"] if w else ""
    row["victory_type"] = _scalar(pick(fight, mapping["victory_type"])) or ""
    return row


def _empty_keys() -> list[str]:
    keys = ["fight_id", "fight_number", "fight_round", "status", "weight_category", "weight", "sport", "period", "timer"]
    for side in SIDES.values():
        keys += [f"{side}_{x}" for x in ("name", "last_name", "first_name", "country", "country_name", "score", "cp", "flag")]
    keys += ["winner_side", "winner_name", "winner_country", "victory_type"]
    return keys
