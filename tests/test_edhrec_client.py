"""Tests for EDHREC client parsing, caching, and degradation behavior."""

from __future__ import annotations

from pathlib import Path

from src.commanders.edhrec_client import EDHRecClient
from src.utils.cache import CardCache


class FakeEDHRec:
    def __init__(self) -> None:
        self._profiles: dict[str, object] = {}
        self._average_decks: dict[str, object] = {}
        self.profile_calls: list[str] = []
        self.average_calls: list[str] = []

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    def set_profile(self, commander_name: str, payload: object) -> None:
        self._profiles[self._key(commander_name)] = payload

    def set_average_deck(self, commander_name: str, payload: object) -> None:
        self._average_decks[self._key(commander_name)] = payload

    def get_commander_data(self, commander_name: str) -> dict:
        self.profile_calls.append(commander_name)
        value = self._profiles.get(self._key(commander_name))
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, dict):
            raise RuntimeError(f"No fake profile configured for '{commander_name}'")
        return value

    def get_commanders_average_deck(self, commander_name: str) -> dict:
        self.average_calls.append(commander_name)
        value = self._average_decks.get(self._key(commander_name))
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, dict):
            raise RuntimeError(f"No fake average deck configured for '{commander_name}'")
        return value


def _cardview(
    name: str,
    card_id: str,
    synergy: float = 0.1,
    inclusion: int = 10,
    potential_decks: int = 100,
) -> dict:
    return {
        "id": card_id,
        "name": name,
        "synergy": synergy,
        "inclusion": inclusion,
        "potential_decks": potential_decks,
    }


def _cardlist(header: str, tag: str, cards: list[object]) -> dict:
    return {"header": header, "tag": tag, "cardviews": cards}


def _edhrec_response(
    cardlists: list[dict],
    commander_name: str = "Korvold, Fae-Cursed King",
    num_decks_avg: int = 1000,
) -> dict:
    return {
        "num_decks_avg": num_decks_avg,
        "container": {
            "json_dict": {
                "card": {"name": commander_name},
                "cardlists": cardlists,
            }
        },
    }


def test_profile_happy_path(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold, Fae-Cursed King",
        _edhrec_response(
            [
                _cardlist(
                    "High Synergy Cards",
                    "highsynergycards",
                    [_cardview("Mayhem Devil", "id-1", synergy=0.49, inclusion=14025, potential_decks=18682)],
                )
            ],
            num_decks_avg=18682,
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold, Fae-Cursed King")

    assert profile is not None
    assert profile.commander_name == "Korvold, Fae-Cursed King"
    assert profile.num_decks == 18682
    assert len(profile.cards) == 1
    card = profile.cards[0]
    assert card.name == "Mayhem Devil"
    assert card.synergy == 0.49
    assert card.inclusion_rate == 14025 / 18682


def test_profile_deduplicates_and_merges_across_categories(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold, Fae-Cursed King",
        _edhrec_response(
            [
                _cardlist(
                    "Top Cards",
                    "topcards",
                    [_cardview("Mayhem Devil", "id-1", synergy=0.25, inclusion=4000, potential_decks=10000)],
                ),
                _cardlist(
                    "Creatures",
                    "creatures",
                    [_cardview("Mayhem Devil", "id-1", synergy=0.55, inclusion=7500, potential_decks=10000)],
                ),
            ]
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold, Fae-Cursed King")

    assert profile is not None
    assert len(profile.cards) == 1
    card = profile.cards[0]
    assert card.synergy == 0.55
    assert card.inclusion_rate == 0.75
    assert "Top Cards" in card.category
    assert "Creatures" in card.category


def test_profile_skips_lands_and_keeps_utility_lands(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response(
            [
                _cardlist("Lands", "lands", [_cardview("Forest", "id-forest")]),
                _cardlist("Utility Lands", "utilitylands", [_cardview("Bojuka Bog", "id-bog")]),
            ]
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is not None
    names = [card.name for card in profile.cards]
    assert "Forest" not in names
    assert "Bojuka Bog" in names


def test_profile_includes_unknown_non_land_tags(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Atraxa",
        _edhrec_response(
            [
                _cardlist("Battles", "battles", [_cardview("Invasion of Zendikar", "id-battle")]),
            ]
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Atraxa")

    assert profile is not None
    assert [card.name for card in profile.cards] == ["Invasion of Zendikar"]


def test_profile_network_error_returns_none(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile("Korvold", RuntimeError("boom"))
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is None


def test_profile_malformed_nested_container_returns_none(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile("Korvold", {"container": None})
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is None


def test_profile_cached_response_skips_api(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response([_cardlist("Top Cards", "topcards", [_cardview("Sol Ring", "id-1")])]),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    first = client.get_commander_profile("Korvold")
    second = client.get_commander_profile("Korvold")

    assert first is not None
    assert second is not None
    assert len(fake.profile_calls) == 1


def test_profile_malformed_cardviews_skipped(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response(
            [
                _cardlist(
                    "Top Cards",
                    "topcards",
                    [
                        {"id": "missing-name"},
                        "not-a-dict",
                        _cardview("Arcane Signet", "id-2"),
                    ],
                )
            ]
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is not None
    assert [card.name for card in profile.cards] == ["Arcane Signet"]


def test_profile_potential_decks_zero_sets_inclusion_rate_zero(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response(
            [
                _cardlist(
                    "Top Cards",
                    "topcards",
                    [_cardview("Dockside Extortionist", "id-3", inclusion=999, potential_decks=0)],
                )
            ]
        ),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is not None
    assert profile.cards[0].inclusion_rate == 0.0


def test_average_deck_happy_path(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_average_deck(
        "Korvold",
        {"commander": "Korvold", "decklist": ["1 Sol Ring", "1 Arcane Signet"]},
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    deck = client.get_average_deck("Korvold")

    assert deck == ["1 Sol Ring", "1 Arcane Signet"]


def test_average_deck_network_error_returns_none(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_average_deck("Korvold", RuntimeError("boom"))
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    deck = client.get_average_deck("Korvold")

    assert deck is None


def test_average_deck_cached_response_skips_api(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_average_deck(
        "Korvold",
        {"commander": "Korvold", "decklist": ["1 Sol Ring"]},
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    first = client.get_average_deck("Korvold")
    second = client.get_average_deck("Korvold")

    assert first == ["1 Sol Ring"]
    assert second == ["1 Sol Ring"]
    assert len(fake.average_calls) == 1


def test_average_deck_empty_decklist_returns_none(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_average_deck("Korvold", {"commander": "Korvold", "decklist": []})
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    deck = client.get_average_deck("Korvold")

    assert deck is None


def test_cache_keys_are_case_insensitive(tmp_path: Path) -> None:
    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response([_cardlist("Top Cards", "topcards", [_cardview("Sol Ring", "id-1")])]),
    )
    client = EDHRecClient(CardCache(str(tmp_path / "cache.db")), edhrec=fake)

    first = client.get_commander_profile("Korvold")
    second = client.get_commander_profile("KORVOLD")

    assert first is not None
    assert second is not None
    assert len(fake.profile_calls) == 1


def test_corrupt_cached_profile_is_deleted_and_refetched(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    cache.put("edhrec:profile:korvold", {"bad": "payload"}, ttl_hours=24)

    fake = FakeEDHRec()
    fake.set_profile(
        "Korvold",
        _edhrec_response([_cardlist("Top Cards", "topcards", [_cardview("Sol Ring", "id-1")])]),
    )
    client = EDHRecClient(cache, edhrec=fake)

    profile = client.get_commander_profile("Korvold")

    assert profile is not None
    assert [card.name for card in profile.cards] == ["Sol Ring"]
    assert len(fake.profile_calls) == 1
