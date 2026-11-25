"""User preferences (chosen browser, thresholds). First run: default values."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.matching import SCORE_MIN, SCORE_OK
from app.i18n import t as tr

DEFAULTS: dict = {
    "browser": None,          # {"name", "kind", "exe"}: asked at the first sign-in
    "score_ok": SCORE_OK,
    "score_min": SCORE_MIN,
    "language": None,         # "es" | "en"; None = not chosen yet (see Api._resolve_language)
}


def threshold_limits() -> dict:
    """Allowed ranges for the thresholds.

    "Add without doubt" has to stay above the "another version" cap (live, remix):
    otherwise those versions would pass as OK. "Add for review" has to stay above the
    "another song" cap (another artist, cover): otherwise they would be added as doubtful."""
    from app.core.matching import NO_MATCH_CAP, VERSION_CAP
    return {"ok_min": round(VERSION_CAP + 0.01, 2), "ok_max": 0.99,
            "min_min": round(NO_MATCH_CAP + 0.01, 2), "gap": 0.05}


def validate_thresholds(score_ok, score_min) -> tuple[float, float]:
    lim = threshold_limits()
    try:
        ok, mn = round(float(score_ok), 2), round(float(score_min), 2)
    except (TypeError, ValueError):
        raise ValueError(tr("err.th_not_numbers"))
    if not lim["ok_min"] <= ok <= lim["ok_max"]:
        raise ValueError(tr("err.th_ok_range", ok_min=f"{lim['ok_min']:.2f}", ok_max=f"{lim['ok_max']:.2f}"))
    if mn < lim["min_min"]:
        raise ValueError(tr("err.th_min_low", min_min=f"{lim['min_min']:.2f}"))
    if mn > round(ok - lim["gap"], 2):
        raise ValueError(tr("err.th_gap", gap=f"{lim['gap']:.2f}"))
    return ok, mn


def load(path: Path) -> dict:
    """The saved preferences over the default values. If there is no file, or it is broken,
    returns the defaults (without overwriting the broken file)."""
    prefs = dict(DEFAULTS)
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prefs
    if isinstance(saved, dict):
        prefs.update({k: v for k, v in saved.items() if k in DEFAULTS})
    try:   # thresholds edited by hand and out of range: the factory ones are used
        prefs["score_ok"], prefs["score_min"] = validate_thresholds(prefs["score_ok"], prefs["score_min"])
    except ValueError:
        prefs["score_ok"], prefs["score_min"] = DEFAULTS["score_ok"], DEFAULTS["score_min"]
    return prefs


def save(path: Path, prefs: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({k: prefs[k] for k in DEFAULTS if k in prefs}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)     # a half-written file is never left behind
