"""Shared loader for the retriever and chat context."""

from __future__ import annotations

from dataclasses import replace

from chat.llm import discover
from chat.pipeline import ChatContext
from rag.retrieve import Retriever
from rag.store import ChunkStore

_store: ChunkStore | None = None
_retriever: Retriever | None = None
_default_variant = "hybrid"


def set_default_variant(variant: str) -> None:
    global _default_variant
    _default_variant = variant


def get_store() -> ChunkStore:
    global _store
    if _store is None:
        _store = ChunkStore.load(require_embeddings=True)
    return _store


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever(get_store())
    return _retriever


def make_context(*, use_llm: bool | None = None, variant: str | None = None, demote_farmdeck: bool = True) -> ChatContext:
    if use_llm is None:
        use_llm = not discover().stub_only
    return ChatContext(
        retriever=get_retriever(),
        retrieval_variant=variant or _default_variant,
        use_llm=use_llm,
        demote_farmdeck=demote_farmdeck,
    )
