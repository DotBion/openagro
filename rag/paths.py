"""Project paths for the chat/retrieval stack."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
SCRIPTS = ROOT / "scripts"
KB_DIR = ROOT / "kb"
QNA_DIR = ROOT / "qna"
LOG_DIR = KB_DIR / "logs"
INDEX_DIR = KB_DIR / "index"
RESULTS_DIR = ROOT / "results"
TRACES_DIR = ROOT / "logs"
CHUNKS_V2 = KB_DIR / "chunks_v2.jsonl"
MANIFEST_PATH = KB_DIR / "index_manifest.json"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings_v2.npy"
EMBED_META_PATH = INDEX_DIR / "embed_meta.json"
IDS_PATH = INDEX_DIR / "ids.json"
BM25_PATH = INDEX_DIR / "bm25.pkl"
QUERY_EMB_PATH = INDEX_DIR / "eval_v2_query_embeddings.npz"
RERANK_CACHE_PATH = INDEX_DIR / "eval_v2_rerank_cache.jsonl"
TRACES_PATH = TRACES_DIR / "chat_traces.jsonl"

for p in (INDEX_DIR, RESULTS_DIR, TRACES_DIR, LOG_DIR):
    p.mkdir(parents=True, exist_ok=True)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
