"""Mass land denial and extra-turn card checks."""

from __future__ import annotations

from src.collection.models import Card


MLD_NAMES = {
    "Armageddon",
    "Ravages of War",
    "Catastrophe",
    "Jokulhaups",
    "Obliterate",
    "Decree of Annihilation",
    "Worldfire",
    "Keldon Firebombers",
    "Impending Disaster",
    "Boom // Bust",
}

EXTRA_TURN_NAMES = {
    "Time Warp",
    "Temporal Manipulation",
    "Capture of Jingzhou",
    "Temporal Mastery",
    "Beacon of Tomorrows",
    "Time Stretch",
    "Expropriate",
    "Nexus of Fate",
    "Alrund's Epiphany",
    "Karn's Temporal Sundering",
    "Medomai the Ageless",
    "Sage of Hours",
    "Part the Waterveil",
    "Temporal Trespass",
    "Walk the Aeons",
    "Notorious Throng",
    "Savor the Moment",
    "Lighthouse Chronologist",
}

MLD_CASEFOLDED = {name.casefold() for name in MLD_NAMES}
EXTRA_TURNS_CASEFOLDED = {name.casefold() for name in EXTRA_TURN_NAMES}


def _name_variants(name: str) -> list[str]:
    normalized = name.strip().casefold()
    variants = [normalized]
    if "//" in name:
        parts = [
            part.strip().casefold()
            for part in name.split("//")
            if part.strip()
        ]
        variants.extend(parts)
    return variants


def _matches(name: str, candidates: set[str]) -> bool:
    return any(variant in candidates for variant in _name_variants(name))


class RuleChecker:
    """Check curated bracket rule card sets against a deck card list."""

    def find_mld(self, cards: list[Card]) -> list[Card]:
        return [card for card in cards if _matches(card.name, MLD_CASEFOLDED)]

    def find_extra_turns(self, cards: list[Card]) -> list[Card]:
        return [card for card in cards if _matches(card.name, EXTRA_TURNS_CASEFOLDED)]
