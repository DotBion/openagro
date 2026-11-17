"""BAAI/bge-m3 tokenizer helpers for v2 chunking and the startup manifest."""

from __future__ import annotations

from functools import lru_cache

MODEL_NAME = "BAAI/bge-m3"
EXPECTED_DIM = 1024
CHUNK_VERSION = "v2"


@lru_cache(maxsize=1)
def get_tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(MODEL_NAME)


def count_tokens(text: str | None) -> int:
    if not text:
        return 0
    return len(get_tokenizer().encode(text, add_special_tokens=False))


def decode_ids(ids: list[int]) -> str:
    return get_tokenizer().decode(ids, skip_special_tokens=True).strip()


def encode_ids(text: str) -> list[int]:
    return get_tokenizer().encode(text or "", add_special_tokens=False)
