"""Bracket estimation engine.

This estimator combines objective local signals and Spellbook output to produce a
floor estimate in the range 1-5.
"""

from __future__ import annotations

from src.brackets.combo_detector import ComboDetector
from src.brackets.game_changers import GameChangerDetector
from src.brackets.rules import RuleChecker
from src.collection.models import BracketEstimate, Card, ComboInfo


class BracketEstimator:
    """Combine local rules and Spellbook data into a bracket floor estimate.

    The output bracket always stays within the 1-5 Commander bracket contract.
    """

    def __init__(
        self,
        game_changers: GameChangerDetector,
        rules: RuleChecker,
        combo_detector: ComboDetector,
    ) -> None:
        self.game_changers = game_changers
        self.rules = rules
        self.combo_detector = combo_detector

    def estimate(self, cards: list[Card], commanders: list[Card]) -> BracketEstimate:
        pool_cards = self._combined_pool(cards, commanders)
        reasons: list[str] = []
        bracket = 1

        game_changers, gc_success = self.game_changers.detect(pool_cards)
        game_changer_names = self._unique_names(game_changers)
        if gc_success:
            gc_count = len(game_changer_names)
            if gc_count >= 4:
                bracket = max(bracket, 4)
                reasons.append(f"{gc_count} Game Changers found")
            elif gc_count >= 1:
                bracket = max(bracket, 3)
                reasons.append(f"{gc_count} Game Changer(s) found")
        else:
            game_changer_names = []
            reasons.append("Game Changer data unavailable - bracket may be underestimated")

        mld_cards = self.rules.find_mld(pool_cards)
        mld_names = self._unique_names(mld_cards)
        if mld_names:
            bracket = max(bracket, 4)
            reasons.append(f"Mass land denial cards found: {', '.join(mld_names)}")

        extra_turn_cards = self.rules.find_extra_turns(pool_cards)
        extra_turn_names = self._unique_names(extra_turn_cards)
        if len(extra_turn_names) >= 3:
            bracket = max(bracket, 4)
            reasons.append(f"{len(extra_turn_names)} extra-turn cards found")
        elif extra_turn_names:
            bracket = max(bracket, 3)
            reasons.append(f"{len(extra_turn_names)} extra-turn card(s) found")

        spellbook_result = self.combo_detector.estimate_bracket(
            card_names=[card.name for card in cards],
            commander_names=[commander.name for commander in commanders],
        )
        combos: list[ComboInfo] = []
        spellbook_tag: str | None = None
        if spellbook_result is not None:
            combos = spellbook_result.combos
            spellbook_tag = spellbook_result.bracket_tag
            reasons.append(
                "Spellbook bracket tag "
                f"{spellbook_tag} suggests bracket {spellbook_result.bracket}"
            )
            bracket = max(bracket, spellbook_result.bracket)

            two_card_brackets = [
                combo.bracket for combo in combos if combo.is_two_card
            ]
            if two_card_brackets:
                combo_floor = max(two_card_brackets)
                bracket = max(bracket, combo_floor)
                reasons.append(f"Two-card combos detected (floor {combo_floor})")
        else:
            reasons.append("Commander Spellbook unavailable - combo data missing")

        confidence = self._confidence(
            bracket=bracket,
            spellbook_success=spellbook_result is not None,
            game_changers_success=gc_success,
        )
        return BracketEstimate(
            bracket=bracket,
            confidence=confidence,
            game_changer_cards=game_changer_names,
            mld_cards=mld_names,
            extra_turn_cards=extra_turn_names,
            combos=combos,
            reasons=reasons,
            spellbook_bracket_tag=spellbook_tag,
        )

    @staticmethod
    def _combined_pool(cards: list[Card], commanders: list[Card]) -> list[Card]:
        combined: list[Card] = []
        seen: set[str] = set()
        for card in [*cards, *commanders]:
            key = card.scryfall_id.strip() or card.name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            combined.append(card)
        return combined

    @staticmethod
    def _unique_names(cards: list[Card]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for card in cards:
            key = card.name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(card.name)
        return names

    @staticmethod
    def _confidence(
        bracket: int,
        spellbook_success: bool,
        game_changers_success: bool,
    ) -> str:
        if spellbook_success and game_changers_success:
            return "high"
        if not spellbook_success and not game_changers_success:
            return "low"
        if spellbook_success and not game_changers_success:
            return "medium"
        if not spellbook_success and bracket >= 3:
            return "medium"
        return "low"
