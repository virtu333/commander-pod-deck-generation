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
# Build 4 decks from one collection.
# Provide 1-4 commanders; missing slots are suggested automatically.
edh-builder build \
  --collection my_cards.csv \
  --commanders "Commander A" \
  --output-dir out/decks \
  --format moxfield --format archidekt --format manabox

# Get commander suggestions (optionally complement existing picks)
edh-builder suggest --collection my_cards.csv --selected "Commander A"

# Estimate bracket for an existing decklist
# (pass commander explicitly when auto-detection is ambiguous)
edh-builder estimate-bracket --decklist my_deck.txt --commander "Commander A"
```

## Exporting from ManaBox

1. Open ManaBox on your device
2. Go to your collection (or a specific binder)
3. Tap the share/export button
4. Choose **CSV** format
5. Save the file and pass it to `--collection`

The CSV includes Scryfall IDs, which the tool uses for precise card resolution.
