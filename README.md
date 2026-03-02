# EDH Collection Deck Builder

Build 4 balanced Commander (EDH) decks from your personal card collection. Imports from ManaBox CSV, suggests commanders, estimates brackets, and distributes shared cards to keep power levels even.

## Install

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Build 4 decks, providing 1-4 commanders (tool suggests the rest)
edh-builder build --collection my_cards.csv --commanders "Commander A" "Commander B"

# Get commander suggestions based on your collection
edh-builder suggest --collection my_cards.csv

# Estimate bracket for an existing decklist
edh-builder estimate-bracket --decklist my_deck.txt
```

## Exporting from ManaBox

1. Open ManaBox on your device
2. Go to your collection (or a specific binder)
3. Tap the share/export button
4. Choose **CSV** format
5. Save the file and pass it to `--collection`

The CSV includes Scryfall IDs, which the tool uses for precise card resolution.
