"""Load chunks_v2 and the dense/BM25 index. Enforces the startup manifest guard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from rag.manifest_guard import ManifestMismatch, check_manifest, load_embed_meta, load_manifest
from rag.paths import BM25_PATH, CHUNKS_V2, EMBEDDINGS_PATH, IDS_PATH, KB_DIR



FUNDING_PARENT_FIELDS = (
    "eligibility_text",
    "who_can_apply",
    "amount",
    "cost_share",
    "readiness",
)


def attach_parent_fields(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy a few structured funding fields from kb.jsonl onto v2 chunks (they were omitted at rechunk)."""
    kb_path = KB_DIR / "kb.jsonl"
    if not kb_path.exists():
        return chunks
    by_id: dict[str, dict[str, Any]] = {}
    with kb_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("id"):
                by_id[rec["id"]] = rec
    for c in chunks:
        parent = by_id.get(c.get("parent_id") or "") or by_id.get(c.get("id") or "")
        if not parent:
            continue
        for field in FUNDING_PARENT_FIELDS:
            if not c.get(field) and parent.get(field):
                c[field] = parent[field]
    return chunks


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_chunks(path: Path = CHUNKS_V2) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing chunks file: {path}")
    return read_jsonl(path)


class ChunkStore:
    """In-memory chunk catalog plus memory-mapped embeddings."""

    def __init__(self, chunks: list[dict[str, Any]], embeddings: np.ndarray | None = None):
        self.chunks = attach_parent_fields(chunks)
        self.ids = [c["id"] for c in chunks]
        self.by_id = {c["id"]: c for c in chunks}
        self.by_parent: dict[str, list[dict[str, Any]]] = {}
        for c in chunks:
            self.by_parent.setdefault(c.get("parent_id") or c["id"], []).append(c)
        self.id_to_index = {cid: i for i, cid in enumerate(self.ids)}
        self.embeddings = embeddings
        self.manifest = load_manifest()
        self.embed_meta: dict[str, Any] | None = None

    @classmethod
    def load(cls, *, require_embeddings: bool = True) -> "ChunkStore":
        chunks = load_chunks()
        embeddings = None
        store = cls(chunks)
        if require_embeddings:
            if not EMBEDDINGS_PATH.exists():
                raise ManifestMismatch(
                    f"Missing embeddings at {EMBEDDINGS_PATH}. Run: .venv/bin/python scripts/embed_chunks.py"
                )
            embeddings = np.load(EMBEDDINGS_PATH, mmap_mode="r")
            if embeddings.dtype != np.float32:
                embeddings = np.asarray(embeddings, dtype=np.float32)
            meta = load_embed_meta()
            # id order must match the matrix rows
            if IDS_PATH.exists():
                saved_ids = json.loads(IDS_PATH.read_text(encoding="utf-8"))
                if saved_ids != store.ids:
                    raise ManifestMismatch(
                        "embeddings row order does not match current chunks_v2.jsonl ids; re-run embed_chunks.py"
                    )
            check_manifest(meta, store.manifest, embeddings_shape=tuple(embeddings.shape))
            if embeddings.shape[0] != len(chunks):
                raise ManifestMismatch(
                    f"Embedding rows {embeddings.shape[0]} != chunks {len(chunks)}; re-run embed_chunks.py"
                )
            store.embeddings = embeddings
            store.embed_meta = meta
        else:
            # still confirm the manifest file itself is readable
            _ = store.manifest
        return store

    def parent_id(self, chunk_or_id: str | dict) -> str:
        if isinstance(chunk_or_id, dict):
            return chunk_or_id.get("parent_id") or chunk_or_id["id"]
        c = self.by_id.get(chunk_or_id)
        if c:
            return c.get("parent_id") or c["id"]
        if "::" in chunk_or_id:
            return chunk_or_id.rsplit("::", 1)[0]
        return chunk_or_id

    def expired_funding_ids(self) -> set[str]:
        out = set()
        for c in self.chunks:
            if c.get("source") == "she_funding" and c.get("expired") is True:
                out.add(c["id"])
        return out

    def eligible_indices(self, *, exclude_expired_funding: bool = True) -> np.ndarray:
        if not exclude_expired_funding:
            return np.arange(len(self.chunks))
        expired = self.expired_funding_ids()
        return np.array([i for i, cid in enumerate(self.ids) if cid not in expired], dtype=np.int64)

    def get(self, chunk_id: str) -> dict[str, Any] | None:
        return self.by_id.get(chunk_id)

    def as_retrieved(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return copies whose `text` includes structured fields the verifier may quote."""
        from rag.funding_fields import visible_text

        out = []
        for cid in ids:
            c = self.by_id.get(cid)
            if c:
                rec = dict(c)
                rec["text"] = visible_text(c)
                out.append(rec)
        return out


def tokenize(text: str) -> list[str]:
    import re

    if not text:
        return []
    return re.findall(r"[a-z0-9]+", text.lower())
