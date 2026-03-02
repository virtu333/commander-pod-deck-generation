# CLAUDE.md — EDH Collection Deck Builder

## Project Overview

A Python CLI tool that builds 4 balanced Commander (EDH) decks from a user's personal
card collection, with bracket estimation aligned to the official Commander Bracket system.
The primary goal is power-level parity across all 4 decks, targeting Bracket 2-3.

## User Context

- Collection tracked in ManaBox (CSV export includes Scryfall IDs)
- Commander selection is flexible: user provides 1-4 commanders, tool suggests the rest
  (e.g., provide 1 → tool suggests 3; provide 4 → no suggestions needed)
- Goal: 4 balanced decks for casual playgroup, matching power levels more than
  hitting a specific bracket number
- Target: Bracket 2 or 3

## Key Decisions Made

- **Python, not TypeScript**: Simpler for data processing, good library ecosystem (pyedhrec,
  requests), no frontend needed for V1
- **SQLite for caching**: Card data from Scryfall + EDHREC changes rarely; cache locally
  to avoid hammering APIs
- **ManaBox CSV as primary import**: ManaBox exports include Scryfall IDs, making card
  resolution much more reliable than name-only matching
- **Commander Spellbook for combo detection**: Their `estimate-bracket` endpoint
  (https://backend.commanderspellbook.com/estimate-bracket) provides combo classification
  that maps to brackets. This solves the "two-card infinite combo" detection problem.
- **Game Changers via Scryfall**: `is:gamechanger` search returns the current official list
  (~53 cards as of Feb 2026). Cache this and refresh periodically.
- **Bracket estimation is a floor, not a ceiling**: We detect objective violations
  (Game Changers, combos, MLD, extra turns) and set a minimum bracket. The user
  adjusts upward for intent.
- **Balance is the top priority**: The multi-deck allocator should optimize for bracket
  parity across all 4 decks, not just maximize each individual deck's quality.

## Architecture

See PRD Section 6 for full architecture. Key modules:

- `src/collection/` — Import and resolve card collections (ManaBox CSV primary)
- `src/commanders/` — Suggest commanders based on collection overlap with EDHREC data
- `src/deckbuilder/` — Build decks + allocate shared cards across 4 decks
- `src/brackets/` — Estimate brackets using GC list, Spellbook API, MLD/extra turn checks
- `src/export/` — Format decklists for ManaBox/Moxfield/Archidekt

## ManaBox CSV Format Reference

```
Name,Set Code,Set Name,Collector Number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase Price,Misprint,Altered,Condition,Language,Purchase Price Currency
Sol Ring,C21,Commander 2021,199,,rare,1,12345,abc-def-123,...,,,Near Mint,en,
```

Key fields:
- `Quantity` — how many copies owned
- `Name` — card name
- `Set Code` — set abbreviation
- `Scryfall ID` — UUID for precise card resolution (huge advantage over name-only)
- `Collector Number` — for distinguishing printings

Note: ManaBox can export whole collection or individual binders. The binder/list name
may appear as an additional column in collection-wide exports.

## External APIs

### Scryfall
- Base: `https://api.scryfall.com`
- Rate limit: 50-100ms between requests
- Bulk data: `https://api.scryfall.com/bulk-data` (download oracle cards JSON for local use)
- Game Changers: `https://api.scryfall.com/cards/search?q=is:gamechanger`
- Card by Scryfall ID: `https://api.scryfall.com/cards/{scryfall_id}`
  (use this with ManaBox's Scryfall IDs for exact resolution)
- IMPORTANT: Use bulk data for initial card DB, individual lookups only for resolution

### Commander Spellbook
- Bracket estimation: `POST https://backend.commanderspellbook.com/estimate-bracket`
- Find combos: `POST https://backend.commanderspellbook.com/find-my-combos`
- Combo bucket → bracket mapping:
  - Ruthless → 4
  - Spicy → 3
  - Powerful → 3
  - Oddball → 2
  - Precon Appropriate → 2
  - Casual → 1
- Source: github.com/SpaceCowMedia/commander-spellbook-backend

### EDHREC
- No official API; use JSON endpoints at json.edhrec.com
- Library: `pyedhrec` (pip install pyedhrec)
- Key methods: `get_commander_data()`, `get_commander_cards()`,
  `get_commanders_average_deck()`
- Cache for 24hrs

## Commander Bracket Rules (Current as of Feb 2026)

Five brackets: Exhibition (1), Core (2), Upgraded (3), Optimized (4), cEDH (5)

### Game Changers List
~53 cards including: Rhystic Study, Cyclonic Rift, Demonic Tutor, Vampiric Tutor,
Smothering Tithe, The One Ring, Consecrated Sphinx, Necropotence, etc.
Full list: https://scryfall.com/search?q=is:gamechanger

- Brackets 1-2: 0 Game Changers
- Bracket 3: Up to 3 Game Changers
- Brackets 4-5: Unlimited

Recent changes (Feb 2026): Farewell added to Game Changers list.
Recent removals (Oct 2025): Food Chain, Deflecting Swat, Expropriate, Jin-Gitaxias,
Kinnan, Sway of the Stars, Urza, Vorinclex, Winota, Yuriko all removed.

### Other Bracket Criteria
- Mass land denial: Not allowed through Bracket 3
- Extra turn cards: Bracket 1 = none; Bracket 2-3 = low quantity, no chaining; 4+ = unrestricted
- Two-card infinite combos: Bracket 1-2 = none; Bracket 3 = no early-game; 4+ = unrestricted
- Tutor restrictions were REMOVED in Oct 2025 (handled by GC list instead)
- Intent is the most important factor (can't be fully automated)

## Commander Selection Notes

- User provides 1-4 commanders; the tool suggests the remainder to reach 4 total
- When suggesting commanders, maximize color identity diversity across all 4 decks
  (e.g., if the user picks a mono-white commander, suggest from the remaining UBRG space)
- Each user-provided commander constrains the suggestion pool: the tool should avoid
  heavy color overlap and aim for distinct archetypes
- Watch for known combos in each commander's typical shell (e.g., equipment commanders
  often have incidental infinite combos like Puresteel Paladin + Crackdown Construct)
- Commander bracket impact: some commanders are inherently bracket 3+ due to their
  abilities — factor this into balance calculations

## Card Allocation Algorithm

The multi-deck allocator is the core novel piece. Approach:

1. Build a "demand matrix": for each card, which decks want it and how badly (synergy score)
2. For cards with quantity >= number of requesting decks: allocate to all
3. For contested cards (quantity < requesting decks):
   a. Score each (card, deck) pair by: synergy contribution + bracket impact
   b. Assign to the deck where removal would hurt most (greedy allocation)
   c. For the "losing" deck(s), find the best available substitute from the collection
4. After initial allocation, run bracket estimation on all 4 decks
5. If brackets are imbalanced, identify swappable cards that would equalize
   (e.g., move a Game Changer from the highest-bracket deck to a lower one if legal)
6. **Balance priority**: If one deck estimates at Bracket 4 and others at 2-3,
   aggressively downgrade the outlier by replacing Game Changers and combo pieces
   with alternatives, even if the individual deck gets slightly worse

## Current Implementation Status

> Update this section as you make progress

- [x] Project scaffolding (pyproject.toml, directory structure)
- [ ] Scryfall bulk data download + SQLite cache
- [ ] Collection importer (ManaBox CSV format — use Scryfall IDs for resolution)
- [ ] Card resolver (match imported names/IDs to Scryfall data)
- [ ] Commander suggester (EDHREC integration)
- [ ] Single-deck builder (EDHREC average deck + collection intersection)
- [ ] Multi-deck allocator (constraint satisfaction for shared cards)
- [ ] Game Changers detection
- [ ] Commander Spellbook combo detection
- [ ] MLD + extra turn detection
- [ ] Bracket estimator (combining all signals)
- [ ] Balance tuner (cross-deck bracket equalization — HIGH PRIORITY)
- [ ] Export formatters (ManaBox CSV, Moxfield/Archidekt text formats)
- [ ] CLI interface
- [ ] Tests

## Implementation Order (Recommended)

1. **Scryfall integration + caching** — Foundation for everything else
2. **Collection importer (ManaBox CSV)** — Can test with real data immediately
3. **Game Changers + MLD + extra turn detection** — Simple list checks, high value
4. **Commander Spellbook integration** — API call, combo detection
5. **Bracket estimator** — Combines steps 3-4
6. **EDHREC integration** — Commander data, synergy scores
7. **Commander suggester** — Uses EDHREC + collection data
8. **Single-deck builder** — Core deckbuilding logic
9. **Multi-deck allocator** — The hard part; build after single-deck works
10. **Balance tuner** — Critical for the "matching power levels" goal
11. **Export + CLI** — Polish

## Useful Scryfall Searches

- All legal commanders: `is:commander f:commander`
- Game Changers: `is:gamechanger`
- Extra turn cards: `o:"extra turn" f:commander`
- Mass land denial: `o:"destroy all lands" f:commander`
- Cards by color identity: `id<=w f:commander` (mono-white example)
- Card by exact name: `!"Card Name Here"`
