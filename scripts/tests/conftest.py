from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path("/workspace/openagronomy")
SCRIPTS = ROOT / "scripts"
CHUNKS_V2 = ROOT / "kb" / "chunks_v2.jsonl"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="session")
def chunks_by_id() -> dict:
    rows = {}
    with CHUNKS_V2.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            rows[rec["id"]] = rec
    return rows


def chunk(chunks_by_id: dict, chunk_id: str) -> dict:
    return chunks_by_id[chunk_id]
