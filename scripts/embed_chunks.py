#!/usr/bin/env python3
"""Embed chunks_v2 embed_text with BAAI/bge-m3 (dense, 1024-d, CPU) and write kb/index/."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

from rag.embed import EXPECTED_DIM, MODEL_NAME, embed_texts, expected_from_manifest  # noqa: E402
from rag.manifest_guard import check_manifest, load_manifest  # noqa: E402
from rag.paths import (  # noqa: E402
    BM25_PATH,
    CHUNKS_V2,
    EMBED_META_PATH,
    EMBEDDINGS_PATH,
    IDS_PATH,
    INDEX_DIR,
)
from rag.retrieve import build_bm25, save_bm25  # noqa: E402
from rag.store import ChunkStore  # noqa: E402


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    expected = expected_from_manifest()
    if expected["embedding_model"] != MODEL_NAME or expected["expected_dim"] != EXPECTED_DIM:
        print(
            f"Refusing to embed: manifest wants {expected}, this script uses "
            f"model={MODEL_NAME} dim={EXPECTED_DIM}",
            file=sys.stderr,
        )
        return 2

    store = ChunkStore.load(require_embeddings=False)
    texts = []
    missing = 0
    for c in store.chunks:
        t = c.get("embed_text") or c.get("text") or ""
        if not c.get("embed_text"):
            missing += 1
        texts.append(t)
    print(
        f"Embedding {len(texts)} chunks with {MODEL_NAME} on CPU "
        f"(chunk_version={expected['chunk_version']}, dim={EXPECTED_DIM}). "
        f"{missing} lacked embed_text and fell back to text.",
        flush=True,
    )
    vecs = embed_texts(texts, batch_size=4, show_progress=True)
    tmp = EMBEDDINGS_PATH.with_name("embeddings_v2.writing.npy")
    with tmp.open("wb") as f:
        np.save(f, vecs)
    tmp.replace(EMBEDDINGS_PATH)
    IDS_PATH.write_text(json.dumps(store.ids, indent=2) + "\n", encoding="utf-8")
    meta = {
        "embedding_model": MODEL_NAME,
        "tokenizer": MODEL_NAME,
        "expected_dim": EXPECTED_DIM,
        "dim": int(vecs.shape[1]),
        "chunk_version": expected["chunk_version"],
        "n_chunks": int(vecs.shape[0]),
        "n_parents": len({c.get("parent_id") for c in store.chunks}),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "device": "cpu",
        "normalize": "l2",
        "chunks_file": str(CHUNKS_V2),
        "chunks_sha256": file_sha256(CHUNKS_V2),
        "manifest_path": str(expected and ROOT / "kb" / "index_manifest.json"),
    }
    EMBED_META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    check_manifest(meta, load_manifest(), embeddings_shape=tuple(vecs.shape))
    print("Building BM25 over embed_text...", flush=True)
    # Reload store with embeddings so BM25 sits beside a guarded index
    store.embeddings = vecs
    store.embed_meta = meta
    bm25 = build_bm25(store)
    save_bm25(bm25, BM25_PATH)
    print(
        f"Wrote {EMBEDDINGS_PATH} shape={vecs.shape} "
        f"and {BM25_PATH}. Guard passed.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
