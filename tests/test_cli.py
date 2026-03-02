"""CLI behavior tests for suggest/build/estimate-bracket commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from src import cli
from src.collection.models import BracketEstimate, Card, Collection, OwnedCard
from src.deckbuilder.builder import BuiltDeck

runner = CliRunner()


class _FakeCache:
    def __enter__(self) -> _FakeCache:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _card(name: str, colors: list[str] | None = None, *, type_line: str = "Creature") -> Card:
    return Card(
        scryfall_id=name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=colors or [],
        type_line=type_line,
        oracle_text="can be your commander" if "commander" in name.casefold() else "",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="common",
    )


def _collection() -> Collection:
    commander_a = _card("Commander A", ["W"], type_line="Legendary Creature")
    commander_b = _card("Commander B", ["U"], type_line="Legendary Creature")
    commander_c = _card("Commander C", ["B"], type_line="Legendary Creature")
    commander_d = _card("Commander D", ["R"], type_line="Legendary Creature")
    support = _card("Support", ["W"], type_line="Instant")
    return Collection(
        cards=[
            OwnedCard(commander_a, 1),
            OwnedCard(commander_b, 1),
            OwnedCard(commander_c, 1),
            OwnedCard(commander_d, 1),
            OwnedCard(support, 10),
        ],
        unresolved=[],
        import_date="2026-03-02",
    )


def _built(commander: Card) -> BuiltDeck:
    cards = [_card(f"{commander.name} Card {index}", commander.color_identity) for index in range(1, 100)]
    return BuiltDeck(
        commander=commander,
        cards=cards,
        scores={card.name.casefold(): 0.0 for card in cards},
        basics_added={},
        edhrec_available=False,
    )


def _estimate(bracket: int = 2) -> BracketEstimate:
    return BracketEstimate(
        bracket=bracket,
        confidence="high",
        game_changer_cards=[],
        mld_cards=[],
        extra_turn_cards=[],
        combos=[],
        reasons=["test reason"],
        spellbook_bracket_tag="O",
    )


def test_build_command_success(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    deck_collection = _collection()

    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: object())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: deck_collection)
    monkeypatch.setattr(cli, "write_deck_exports", lambda decks, output_dir, formats: [tmp_path / "deck1.txt"])

    class _FakeDeckBuilder:
        def __init__(self, edhrec=None) -> None:
            self.edhrec = edhrec

        def build(self, commander: Card, collection: Collection) -> BuiltDeck:
            return _built(commander)

    class _FakeAllocator:
        def allocate(self, collection: Collection, decks: list[BuiltDeck]) -> list[BuiltDeck]:
            return decks

    class _FakeEstimator:
        def __init__(self, game_changers, rules, combo_detector) -> None:  # noqa: ANN001
            return None

        def estimate(self, cards: list[Card], commanders: list[Card]) -> BracketEstimate:
            return _estimate(2)

    monkeypatch.setattr(cli, "DeckBuilder", _FakeDeckBuilder)
    monkeypatch.setattr(cli, "MultiDeckAllocator", _FakeAllocator)
    monkeypatch.setattr(cli, "BracketEstimator", _FakeEstimator)
    monkeypatch.setattr(cli, "EDHRecClient", lambda cache: object())
    monkeypatch.setattr(cli, "GameChangerDetector", lambda scryfall: object())
    monkeypatch.setattr(cli, "RuleChecker", lambda: object())
    monkeypatch.setattr(cli, "ComboDetector", lambda cache: object())

    collection_file = tmp_path / "collection.csv"
    collection_file.write_text("Name,Quantity\nCard,1\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        [
            "build",
            "--collection",
            str(collection_file),
            "--commanders",
            "Commander A",
            "--commanders",
            "Commander B",
            "--commanders",
            "Commander C",
            "--commanders",
            "Commander D",
        ],
    )
    assert result.exit_code == 0
    assert "Built 4 deck(s)" in result.stdout


def test_build_rejects_invalid_export_format(tmp_path: Path) -> None:
    collection_file = tmp_path / "collection.csv"
    collection_file.write_text("Name,Quantity\nCard,1\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["build", "--collection", str(collection_file), "--format", "invalid-format"],
    )
    assert result.exit_code != 0
    assert "Unsupported format" in result.stdout


def test_suggest_returns_nonzero_for_missing_file() -> None:
    result = runner.invoke(cli.app, ["suggest", "--collection", "missing.csv"])
    assert result.exit_code != 0
    assert "Input file not found" in result.stdout


def test_suggest_command_success(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    deck_collection = _collection()
    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: object())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: deck_collection)
    monkeypatch.setattr(cli, "EDHRecClient", lambda cache: object())

    class _Candidate:
        def __init__(self, card: Card) -> None:
            self.card = card
            self.score = 1.0
            self.collection_overlap = 0.5
            self.color_diversity_score = 0.4
            self.buildable_count = 50

    class _FakeSuggester:
        def __init__(self, edhrec) -> None:  # noqa: ANN001
            return None

        def suggest(self, collection, selected, count):  # noqa: ANN001
            return [_Candidate(_card("Commander B", ["U"], type_line="Legendary Creature"))]

    monkeypatch.setattr(cli, "CommanderSuggester", _FakeSuggester)

    collection_file = tmp_path / "collection.csv"
    collection_file.write_text("Name,Quantity\nCard,1\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["suggest", "--collection", str(collection_file)])
    assert result.exit_code == 0
    assert "Top 1 suggestion" in result.stdout


def test_estimate_bracket_command_success(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    commander = _card("Commander A", ["W"], type_line="Legendary Creature")
    support = _card("Support Spell", ["W"], type_line="Instant")
    estimate_collection = Collection(
        cards=[OwnedCard(commander, 1), OwnedCard(support, 1)],
        unresolved=[],
        import_date="2026-03-02",
    )

    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: object())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: estimate_collection)

    class _FakeEstimator:
        def __init__(self, game_changers, rules, combo_detector) -> None:  # noqa: ANN001
            return None

        def estimate(self, cards: list[Card], commanders: list[Card]) -> BracketEstimate:
            return _estimate(3)

    monkeypatch.setattr(cli, "BracketEstimator", _FakeEstimator)
    monkeypatch.setattr(cli, "GameChangerDetector", lambda scryfall: object())
    monkeypatch.setattr(cli, "RuleChecker", lambda: object())
    monkeypatch.setattr(cli, "ComboDetector", lambda cache: object())

    deck_file = tmp_path / "deck.txt"
    deck_file.write_text("1 Commander A\n1 Support Spell\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        [
            "estimate-bracket",
            "--decklist",
            str(deck_file),
            "--commander",
            "Commander A",
        ],
    )
    assert result.exit_code == 0
    assert "Estimated Bracket: 3" in result.stdout


def test_estimate_bracket_requires_explicit_commander_when_ambiguous(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    commander_a = _card("Commander A", ["W"], type_line="Legendary Creature")
    commander_b = _card("Commander B", ["U"], type_line="Legendary Creature")
    estimate_collection = Collection(
        cards=[OwnedCard(commander_a, 1), OwnedCard(commander_b, 1)],
        unresolved=[],
        import_date="2026-03-02",
    )
    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: object())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: estimate_collection)

    deck_file = tmp_path / "deck.txt"
    deck_file.write_text("1 Commander A\n1 Commander B\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["estimate-bracket", "--decklist", str(deck_file)],
    )
    assert result.exit_code != 0
    assert "Unable to auto-detect a single commander" in result.stdout


def test_build_output_count_not_inflated_by_duplicate_format_flags(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    deck_collection = _collection()
    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: object())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: deck_collection)

    class _FakeDeckBuilder:
        def __init__(self, edhrec=None) -> None:
            self.edhrec = edhrec

        def build(self, commander: Card, collection: Collection) -> BuiltDeck:
            return _built(commander)

    class _FakeAllocator:
        def allocate(self, collection: Collection, decks: list[BuiltDeck]) -> list[BuiltDeck]:
            return decks

    class _FakeEstimator:
        def __init__(self, game_changers, rules, combo_detector) -> None:  # noqa: ANN001
            return None

        def estimate(self, cards: list[Card], commanders: list[Card]) -> BracketEstimate:
            return _estimate(2)

    monkeypatch.setattr(cli, "DeckBuilder", _FakeDeckBuilder)
    monkeypatch.setattr(cli, "MultiDeckAllocator", _FakeAllocator)
    monkeypatch.setattr(cli, "BracketEstimator", _FakeEstimator)
    monkeypatch.setattr(cli, "EDHRecClient", lambda cache: object())
    monkeypatch.setattr(cli, "GameChangerDetector", lambda scryfall: object())
    monkeypatch.setattr(cli, "RuleChecker", lambda: object())
    monkeypatch.setattr(cli, "ComboDetector", lambda cache: object())

    collection_file = tmp_path / "collection.csv"
    output_dir = tmp_path / "out"
    collection_file.write_text("Name,Quantity\nCard,1\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        [
            "build",
            "--collection",
            str(collection_file),
            "--output-dir",
            str(output_dir),
            "--commanders",
            "Commander A",
            "--commanders",
            "Commander B",
            "--commanders",
            "Commander C",
            "--commanders",
            "Commander D",
            "--format",
            "moxfield",
            "--format",
            "moxfield",
            "--format",
            "archidekt",
        ],
    )
    assert result.exit_code == 0
    assert "Exported 8 file(s)" in result.stdout
    assert len(list(output_dir.iterdir())) == 8


def test_estimate_bracket_accepts_explicit_commander_not_in_decklist_via_scryfall_lookup(  # noqa: ANN001
    monkeypatch,
    tmp_path: Path,
) -> None:
    support = _card("Support Spell", ["W"], type_line="Instant")
    estimate_collection = Collection(
        cards=[OwnedCard(support, 1)],
        unresolved=[],
        import_date="2026-03-02",
    )

    class _FakeScryfallClient:
        def search(self, query: str) -> list[Card]:
            if query == '!"Commander Outside Deck"':
                return [_card("Commander Outside Deck", ["W"], type_line="Legendary Creature")]
            return []

    monkeypatch.setattr(cli, "CardCache", _FakeCache)
    monkeypatch.setattr(cli, "ScryfallClient", lambda cache: _FakeScryfallClient())
    monkeypatch.setattr(cli, "_resolve_collection", lambda collection, scryfall: estimate_collection)

    class _FakeEstimator:
        def __init__(self, game_changers, rules, combo_detector) -> None:  # noqa: ANN001
            return None

        def estimate(self, cards: list[Card], commanders: list[Card]) -> BracketEstimate:
            return _estimate(3)

    monkeypatch.setattr(cli, "BracketEstimator", _FakeEstimator)
    monkeypatch.setattr(cli, "GameChangerDetector", lambda scryfall: object())
    monkeypatch.setattr(cli, "RuleChecker", lambda: object())
    monkeypatch.setattr(cli, "ComboDetector", lambda cache: object())

    deck_file = tmp_path / "deck.txt"
    deck_file.write_text("1 Support Spell\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        [
            "estimate-bracket",
            "--decklist",
            str(deck_file),
            "--commander",
            "Commander Outside Deck",
        ],
    )
    assert result.exit_code == 0
    assert "Estimated Bracket: 3" in result.stdout
