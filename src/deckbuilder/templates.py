"""Deckbuilding template primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeckTemplate:
    """Deck structure targets used as builder priorities."""

    target_lands: int = 37
    target_ramp: int = 10
    target_draw: int = 10
    target_removal: int = 5


DEFAULT_TEMPLATE = DeckTemplate()
