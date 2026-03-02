"""Deckbuilding template primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeckTemplate:
    """Minimal template for the single-deck builder."""

    target_lands: int = 37


DEFAULT_TEMPLATE = DeckTemplate()
