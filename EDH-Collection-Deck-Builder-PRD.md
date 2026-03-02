# EDH Collection Deck Builder — PRD & CLAUDE.md

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Goals](#2-goals)
3. [Non-Goals](#3-non-goals)
4. [User Stories](#4-user-stories)
5. [Requirements](#5-requirements)
6. [Technical Architecture](#6-technical-architecture)
7. [Bracket Detection Deep-Dive](#7-bracket-detection-deep-dive)
8. [Success Metrics](#8-success-metrics)
9. [Open Questions](#9-open-questions)
10. [CLAUDE.md (Implementation Guide)](#10-claudemd)

---

## 1. Problem Statement

Commander players who want to build multiple balanced decks for their playgroup face a tedious, manual process: they must cross-reference their card collection against potential commanders, mentally track which cards are already allocated to other decks (especially singles they only own one copy of), and estimate power levels using a bracket system that blends objective card lists with subjective intent evaluation. No existing tool solves all three problems together.

EDHREC provides excellent recommendation data but doesn't know what cards you own. Moxfield and Archidekt track collections and estimate brackets, but don't help you build *multiple balanced decks simultaneously* from a shared card pool. The result is that casual playgroups—the largest segment of Commander players—often end up with lopsided game nights where one deck dramatically outperforms the others.

The user provides 1-4 commanders they want to play, and the tool suggests the remainder to reach 4 total. It then builds all four decks using only cards the user owns, distributes shared cards intelligently, and ensures the decks land in similar brackets. The target is Bracket 2 or 3, with a strong emphasis on balanced power across all four decks rather than hitting a specific number.

---

## 2. Goals

**User Goals**

- Reduce the time to build 4 balanced Commander decks from a personal collection from hours of manual work to under 30 minutes of guided interaction.
- Produce decks that are genuinely playable and land in the same (or adjacent) Commander Bracket, so game nights feel fair.
- Ensure no card appears in two decks unless the user owns multiple copies.

**Project Goals**

- Build a tool that works end-to-end for a single user's collection, demonstrating the core concept before scaling.
- Create reusable infrastructure (Scryfall integration, bracket estimation, collection management) that can be extended into a web app later.

---

## 3. Non-Goals

- **Not a full EDHREC clone.** We are not building a general-purpose recommendation engine or scraping EDHREC's entire database. We use their JSON data and Commander Spellbook's API for specific lookups.
- **Not a real-time multiplayer app.** V1 is a single-user CLI/local tool, not a hosted web service with accounts.
- **Not attempting to perfectly replicate Moxfield/Archidekt bracket calculators.** Those tools themselves acknowledge their estimates are imperfect. We aim for a useful approximation using the same data sources.
- **Not handling collection scanning or OCR.** The user's collection is meticulously documented in ManaBox. We import from ManaBox's CSV export, which conveniently includes Scryfall IDs for precise card matching.
- **Not optimizing for cEDH (Bracket 5).** The target use case is casual-to-upgraded play (Brackets 2-4).

---

## 4. User Stories

**P0 — Core Flow**

- "As a Commander player, I want to import my card collection from Moxfield/Archidekt so that the tool knows exactly which cards I own and how many copies of each."
- "As a Commander player, I want to pick 1-4 commanders and have the tool suggest the remaining commanders from my collection that would produce balanced, interesting decks so that I don't have to manually search for good pairings."
- "As a Commander player, I want the tool to build 4 complete 100-card decks using only cards I own, respecting singleton rules and copy counts, so that I can sleeve up and play immediately."
- "As a Commander player, I want to see the estimated bracket for each deck so that I know they're roughly balanced before I play."

**P1 — Refinement**

- "As a Commander player, I want to swap out specific cards between decks or lock certain cards into a deck so that I can customize the suggestions."
- "As a Commander player, I want to set a target bracket (e.g., 'all decks at Bracket 3') so the tool builds toward that power level."
- "As a Commander player, I want to export the final decklists in a format I can paste into Moxfield/Archidekt so I can track and playtest them."

**P2 — Edge Cases**

- "As a Commander player, I want the tool to warn me if I don't have enough cards in certain colors to build a viable deck for a suggested commander, so I don't waste time on impossible builds."
- "As a Commander player, I want the tool to explain *why* a deck is rated at a certain bracket (which Game Changers it contains, detected combos, extra turn cards) so I can make informed adjustments."

---

## 5. Requirements

### Must-Have (P0)

**R1: Collection Import**
- Accept ManaBox CSV export format (primary): `Name, Set Code, Set Name, Collector Number, Foil, Rarity, Quantity, ManaBox ID, Scryfall ID, Purchase Price, Misprint, Altered, Condition, Language, Purchase Price Currency`
- Accept ManaBox collection export (whole collection or individual binders)
- Accept simple text list format (`1 Card Name` per line) as fallback
- Parse and store card names + quantities, using Scryfall ID from ManaBox export for precise resolution
- Resolve card names against Scryfall API for canonical names, color identity, card types, and legality
- Handle double-faced cards, split cards, and adventure cards
- Note: ManaBox exports include Scryfall IDs, which makes card resolution significantly more reliable than name-only matching

Acceptance Criteria:
- Given a ManaBox CSV with 3,000 cards, when imported, then all cards with valid Scryfall IDs are resolved instantly; cards without IDs are resolved by name+set within 60 seconds total.
- Given a card name that doesn't match Scryfall exactly (e.g., typo or alternate name), when imported, then the tool flags it for manual resolution rather than silently dropping it.

**R2: Commander Suggestion**
- Given 1-4 user-selected commanders, identify all legendary creatures in the collection that are legal as commanders
- Filter candidates whose color identity doesn't overlap too heavily with the already-chosen commanders (configurable — default: allow some overlap)
- Rank candidates by "buildability" — how many cards in the collection fit that commander's color identity and typical strategy
- Use EDHREC data (via `json.edhrec.com` or `pyedhrec`) to identify what percentage of a commander's average decklist the user owns
- Suggest enough commanders to reach 4 total, maximizing collection utilization while providing distinct gameplay experiences (different color identities, different archetypes)

Acceptance Criteria:
- Given 1 user-selected commander and a collection of 3,000+ cards, when suggestions are generated, then 3 commanders are returned with distinct color identities, each having at least 40 buildable cards from the collection in their color identity.
- The suggested commanders should collectively cover a broad range of the color pie for gameplay diversity, complementing the user's chosen commander(s).

**R3: Deck Construction**
- For each of the 4 commanders, build a 100-card deck (99 + commander) using only cards from the collection
- Respect copy limits: if the user owns 1 copy of a card, it can only go in 1 deck. If they own 2+, it can go in multiple decks (up to the owned count)
- Basic lands are unlimited (assume the user has as many basics as needed)
- Follow a reasonable deck structure: ~36-38 lands, ~10 ramp, ~10 draw, ~5 removal (adjustable per archetype)
- Prioritize cards with high EDHREC synergy scores for each commander
- Use a constraint-satisfaction approach: allocate high-demand shared cards to the deck where they contribute most to balance

Acceptance Criteria:
- Given 4 commanders and a collection, when decks are built, then no non-basic card appears in more decks than the user's owned copy count.
- Given the deck construction output, each deck contains exactly 100 cards including the commander.
- Each deck has a functional mana base (correct color distribution for the commander's identity).

**R4: Bracket Estimation**
- Estimate the Commander Bracket (1-5) for each constructed deck
- Check against the Game Changers list (currently ~53 cards, available via Scryfall `is:gamechanger` filter)
- Check for two-card infinite combos via Commander Spellbook's `estimate-bracket` API endpoint (`https://backend.commanderspellbook.com/estimate-bracket`)
- Check for mass land denial cards (Armageddon, Ravages of War, Jokulhaups, Obliterate, Decree of Annihilation, Catastrophe, etc.)
- Check for extra turn cards (Time Warp, Temporal Manipulation, etc.) and flag if more than 1-2 are present
- Produce a bracket estimate with reasoning

Acceptance Criteria:
- Given a deck with 0 Game Changers, no infinite combos, no mass land denial, and ≤1 extra turn spell, then the estimated bracket is 1 or 2.
- Given a deck with 3 Game Changers and no early-game combos, then the estimated bracket is 3.
- Given a deck with 4+ Game Changers or unrestricted combos, then the estimated bracket is 4.

### Nice-to-Have (P1)

**R5: Balance Tuning**
- After initial construction, compare the 4 decks' brackets
- If one deck is significantly higher/lower, suggest swaps: move powerful cards from the overpowered deck to the underpowered one (if color identity allows) or replace Game Changers with alternatives
- Allow the user to set a target bracket and rebuild accordingly

**R6: Export**
- Export decklists in Moxfield-compatible text format
- Export decklists in Archidekt-compatible text format
- Export decklists in ManaBox-compatible CSV format (so the friend can re-import decks into ManaBox)
- Export a summary report showing all 4 decks, their brackets, key cards, and any cards flagged as contentious

**R7: Interactive Mode**
- CLI interactive mode where the user can approve/reject commander suggestions, lock cards into decks, and iterate

### Future Considerations (P2)

**R8: Web UI**
- React-based web interface for the tool
- Direct Moxfield/Archidekt collection import via URL (if APIs become available)

**R9: LLM-Assisted Strategy Matching**
- Use an LLM to evaluate commander strategies and suggest thematic pairings (e.g., "Cloud wants equipment voltron; pair with a spellslinger, a tokens deck, and a reanimator for diverse gameplay")

**R10: Sealed Pool Generator**
- Given a collection, generate sealed-style limited pools for Commander draft nights

---

## 6. Technical Architecture

### Stack

```
Language:       Python 3.11+
Package Mgmt:   Poetry or pip with pyproject.toml
Data Sources:   Scryfall API, Commander Spellbook API, EDHREC JSON
Storage:        Local SQLite for collection + card cache
CLI:            Click or Typer
Testing:        pytest
```

### Project Structure

```
edh-collection-builder/
├── CLAUDE.md                    # Session continuity doc (see Section 10)
├── pyproject.toml
├── README.md
├── src/
│   ├── __init__.py
│   ├── cli.py                   # CLI entry point
│   ├── collection/
│   │   ├── __init__.py
│   │   ├── importer.py          # CSV/text parsing
│   │   ├── models.py            # Card, Collection data models
│   │   └── resolver.py          # Scryfall name resolution + caching
│   ├── commanders/
│   │   ├── __init__.py
│   │   ├── suggester.py         # Commander recommendation engine
│   │   └── edhrec_client.py     # EDHREC data fetching
│   ├── deckbuilder/
│   │   ├── __init__.py
│   │   ├── builder.py           # Core deck construction algorithm
│   │   ├── allocator.py         # Multi-deck card allocation (constraint satisfaction)
│   │   └── templates.py         # Archetype-based deck templates (lands, ramp, draw, etc.)
│   ├── brackets/
│   │   ├── __init__.py
│   │   ├── estimator.py         # Bracket estimation engine
│   │   ├── game_changers.py     # Game Changers list management
│   │   ├── combo_detector.py    # Commander Spellbook integration
│   │   └── rules.py             # Mass land denial, extra turns, tutor detection
│   ├── export/
│   │   ├── __init__.py
│   │   └── formatters.py        # Moxfield/Archidekt export formats
│   └── utils/
│       ├── __init__.py
│       ├── scryfall.py          # Scryfall API client with rate limiting + caching
│       └── cache.py             # SQLite caching layer
├── tests/
│   ├── test_importer.py
│   ├── test_bracket_estimator.py
│   ├── test_allocator.py
│   └── fixtures/
│       ├── sample_collection.csv
│       └── sample_decklist.txt
└── data/
    ├── game_changers.json       # Cached Game Changers list
    ├── mass_land_denial.json    # Known MLD cards
    └── extra_turns.json         # Known extra turn cards
```

### Key Data Flow

```
ManaBox CSV Export (with Scryfall IDs)
       │
       ▼
  [Importer] ──→ Scryfall API (resolve by ID first, name fallback)
       │
       ▼
  [Collection DB] (SQLite: card_name, scryfall_id, quantity, color_identity, types, ...)
       │
       ├──→ [Commander Suggester]
       │         │
       │         ├── Input: 1-4 user-provided commanders
       │         ├── Filter: legendary creatures, commander-legal
       │         ├── Rank: EDHREC average deck overlap with collection
       │         ├── Diversify: spread across color identities + archetypes
       │         └── Suggest (4 - N) commanders to complement user's N picks
       │         │
       │         ▼
       │    [4 Total Commanders] (user-provided + suggestions)
       │
       ▼
  [Deck Builder] (per commander)
       │
       ├── Fetch EDHREC average deck / top cards for commander
       ├── Intersect with collection (what do we own?)
       ├── Score cards by synergy + bracket impact
       └── Fill deck template (lands, ramp, draw, removal, synergy)
       │
       ▼
  [Multi-Deck Allocator]
       │
       ├── Resolve conflicts (1-copy cards wanted by multiple decks)
       ├── Optimize for bracket balance across all 4 decks
       └── Fill gaps with next-best alternatives from collection
       │
       ▼
  [Bracket Estimator] (per deck)
       │
       ├── Count Game Changers
       ├── Query Commander Spellbook estimate-bracket endpoint
       ├── Check MLD cards, extra turn density
       └── Combine signals → bracket estimate + reasoning
       │
       ▼
  [Output: 4 Decklists + Bracket Reports]
```

### External API Usage

**Scryfall API**
- Endpoint: `https://api.scryfall.com/cards/search`
- Rate limit: 50-100ms between requests (be respectful)
- Use bulk data download (`https://api.scryfall.com/bulk-data`) for initial card database — avoids hammering the API
- Cache locally in SQLite
- Key fields: `name`, `color_identity`, `type_line`, `legalities.commander`, `keywords`, `oracle_text`
- Use `is:gamechanger` for Game Changers list

**Commander Spellbook API**
- Bracket estimation: `POST https://backend.commanderspellbook.com/estimate-bracket`
  - Send decklist, get back bracket-relevant combo information
  - Combo classification buckets: Ruthless → Bracket 4, Spicy/Powerful → Bracket 3, Oddball/Precon Appropriate → Bracket 2, Casual → Bracket 1
- Find-my-combos: `POST https://backend.commanderspellbook.com/find-my-combos`
  - Alternative endpoint for full combo enumeration
- Source code reference: `github.com/SpaceCowMedia/commander-spellbook-backend`

**EDHREC Data**
- No official API; use `json.edhrec.com` static JSON endpoints (as documented by `pyedhrec` and `mightstone` libraries)
- Key data: average decklist per commander, synergy scores, card inclusion rates
- Consider using `pyedhrec` library for convenience (pip install pyedhrec)
- Cache aggressively (24hr TTL) — data doesn't change frequently

---

## 7. Bracket Detection Deep-Dive

This is the hardest part of the project, as your friend identified. Here's a detailed breakdown of what's checkable programmatically vs. what requires heuristics.

### Tier 1: Objectively Checkable (High Confidence)

**Game Changers List**
The official list is maintained by WotC and available via Scryfall (`is:gamechanger`). As of February 2026, there are ~53 cards. This is a simple set membership check.

Rules:
- Bracket 1-2: 0 Game Changers allowed
- Bracket 3: Up to 3 Game Changers allowed
- Bracket 4-5: Unlimited

**Mass Land Denial**
A finite, well-known list of cards. Create a curated list:

```
Armageddon, Ravages of War, Catastrophe, Jokulhaups, Obliterate,
Decree of Annihilation, Boom // Bust (Bust half), Worldfire,
Keldon Firebombers, Impending Disaster, Global Ruin, From the Ashes,
Ruination, Blood Moon (debatable), Back to Basics (debatable),
Winter Orb, Static Orb, Stasis, Rising Waters, Hokori Dust Drinker,
Sire of Stagnation (not MLD but anti-land)
```

Rules:
- Bracket 1-3: No mass land denial
- Bracket 4-5: Allowed

**Extra Turn Cards**
Also a finite list (searchable via Scryfall: `o:"extra turn"`):

```
Time Warp, Temporal Manipulation, Capture of Jingzhou, Temporal Mastery,
Beacon of Tomorrows, Time Stretch, Expropriate (delisted as GC but still extra turns),
Nexus of Fate, Alrund's Epiphany, Karn's Temporal Sundering,
Medomai the Ageless, Sage of Hours, etc.
```

Rules:
- Bracket 1: No extra turn cards
- Bracket 2-3: Low quantity (1-2), not intended to chain
- Bracket 4-5: Unrestricted

Count them. If a deck has 3+, it's likely trying to chain them → Bracket 4+.

### Tier 2: Checkable via Commander Spellbook (Medium-High Confidence)

**Two-Card Infinite Combos**
This is where Commander Spellbook's API is invaluable. Their database contains ~80,000+ combos.

- Use the `estimate-bracket` endpoint to get combo classification for a decklist
- They classify combos into buckets (Ruthless, Spicy, Powerful, etc.) that map to brackets
- Two-card combos in Brackets 1-2 are not allowed at all
- In Bracket 3, only late-game two-card combos are acceptable (the "no early-game" qualifier is subjective but Commander Spellbook's classification helps)

The key insight from the Moxfield feedback thread: even Archidekt initially only estimated brackets based on Game Changers because "mass land denial, tutors, and combos aren't as objective." Commander Spellbook filling in the combo detection is a major unlock.

### Tier 3: Heuristic / Intent-Based (Lower Confidence)

**"Intent to chain extra turns"**
Having 1 Time Warp is fine. Having Time Warp + Temporal Manipulation + Beacon of Tomorrows + Archaeomancer + Conjurer's Closet suggests chaining intent. Heuristic: count extra turn spells + recursion enablers.

**Tutor density**
Tutor restrictions were removed from the bracket rules in October 2025, with the reasoning that Game Changers already catches the most powerful tutors (Demonic Tutor, Vampiric Tutor, etc. are Game Changers). So this is largely handled by GC detection. However, high tutor density (5+) in a deck with powerful targets still indicates higher brackets.

**Overall power level / intent**
The bracket system explicitly says you can build a deck that "technically meets all rules of Core (Bracket 2) but plays at the power level of Optimized (Bracket 4)." No programmatic tool can fully capture this. Our approach: flag objective violations, provide a bracket floor estimate, and let the user adjust upward based on their knowledge of the deck's intent.

### Recommended Implementation Strategy

```python
def estimate_bracket(decklist: list[str], commander: str) -> BracketEstimate:
    """
    Returns a BracketEstimate with:
    - estimated_bracket: int (1-5)
    - confidence: str ("high" | "medium" | "low")
    - reasons: list[str] (human-readable explanations)
    - game_changers: list[str] (GCs found in deck)
    - combos: list[ComboInfo] (from Commander Spellbook)
    - mld_cards: list[str]
    - extra_turn_cards: list[str]
    """

    gc_count = count_game_changers(decklist)
    mld_cards = find_mld_cards(decklist)
    extra_turn_cards = find_extra_turn_cards(decklist)
    combo_info = query_spellbook_bracket(decklist)  # API call

    # Start at bracket 2 (baseline functional deck)
    bracket = 2
    reasons = []

    # Game Changers push bracket up
    if gc_count == 0:
        pass  # Could be 1 or 2
    elif gc_count <= 3:
        bracket = max(bracket, 3)
        reasons.append(f"{gc_count} Game Changer(s): {', '.join(game_changers)}")
    else:
        bracket = max(bracket, 4)
        reasons.append(f"{gc_count} Game Changers (exceeds Bracket 3 limit of 3)")

    # MLD pushes to 4+
    if mld_cards:
        bracket = max(bracket, 4)
        reasons.append(f"Mass land denial: {', '.join(mld_cards)}")

    # Extra turns: 3+ suggests chaining intent
    if len(extra_turn_cards) >= 3:
        bracket = max(bracket, 4)
        reasons.append(f"{len(extra_turn_cards)} extra turn cards suggest chaining intent")
    elif len(extra_turn_cards) >= 1 and bracket < 2:
        bracket = max(bracket, 2)

    # Combos from Spellbook
    if combo_info.has_ruthless_combos:
        bracket = max(bracket, 4)
        reasons.append(f"Contains high-power combos: {combo_info.summary}")
    elif combo_info.has_two_card_combos:
        bracket = max(bracket, 3)
        reasons.append(f"Contains two-card combos: {combo_info.summary}")

    return BracketEstimate(
        estimated_bracket=bracket,
        confidence="high" if bracket >= 3 else "medium",
        reasons=reasons,
        ...
    )
```

---

## 8. Success Metrics

Since this is a personal/friends tool, traditional product metrics don't fully apply. Instead:

**Functional Success**
- Tool successfully imports a real Moxfield collection export without errors
- Tool produces 4 legal, 100-card Commander decks from that collection
- No card allocation conflicts (no card used more times than owned)
- Bracket estimates align with manual evaluation (spot-check 2-3 decks against Moxfield/Archidekt calculators)

**Usability Success**
- End-to-end flow (import → suggest → build → export) completes in under 5 minutes
- Decks are "playable as-is" — reasonable mana bases, functional card ratios
- Game night feedback: "the decks felt balanced"

---

## 9. Open Questions

**Blocking (must answer before implementation)**

1. ~~**Where is the friend's collection hosted?**~~ **RESOLVED: ManaBox.** The collection is tracked in ManaBox, which exports CSV with Scryfall IDs included — a significant advantage for card resolution accuracy.
2. ~~**Which Cloud card?**~~ **RESOLVED → GENERALIZED.** Commander selection is now flexible: user provides 1-4 commanders of their choice, and the tool suggests the rest. No hardcoded anchor commander.
3. ~~**Target bracket?**~~ **RESOLVED: Bracket 2 or 3, with emphasis on matching power levels.** The priority is that all 4 decks feel balanced against each other, not that they hit a specific number. This means the balance tuner (R5) is more important than originally scoped — it should be elevated toward P0.

**Non-blocking (can resolve during implementation)**

4. **Commander Spellbook API stability**: The `estimate-bracket` endpoint was contributed specifically for Moxfield integration. Confirm it's still public and stable.
5. **EDHREC JSON endpoint format**: The `json.edhrec.com` endpoints are undocumented. Test with `pyedhrec` and fall back to web scraping if needed.
6. **Hybrid mana rule**: As of February 2026, the Commander Format Panel is still debating whether hybrid mana costs should be treated as "or" instead of "and" for color identity. Current rules say "and." Monitor for changes.
