"""Local JSONL traces. Langfuse is not used (no Docker, no Langfuse keys)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from rag.paths import TRACES_PATH

ZONE = ZoneInfo("America/New_York")


def append_trace(record: dict[str, Any], path: Path = TRACES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = dict(record)
    rec.setdefault("ts", datetime.now(ZONE).isoformat(timespec="seconds"))
    rec.setdefault("tz", "America/New_York")
    # Never persist secrets if a caller accidentally passed one.
    for k in list(rec):
        lk = k.lower()
        if "api_key" in lk or lk.endswith("secret") or lk.endswith("token"):
            rec.pop(k, None)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return path
