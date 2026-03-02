# EDH Collection Deck Builder

A tool that takes your personal Magic: The Gathering card collection and builds **4 balanced Commander (EDH) decks** from it. It suggests commanders, builds decks using data from EDHREC, distributes shared cards fairly across all 4 decks, and estimates each deck's power bracket (targeting Bracket 2-3 for casual play).

**What you need to get started:**
- Your card collection tracked in [ManaBox](https://manabox.app/) (free app)
- A computer with Python installed (version 3.11 or newer)

---

## Step 1: Install Python (if you don't have it)

If you've never used Python before:

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest version (3.12 or newer)
3. Run the installer
   - **Important:** Check the box that says **"Add Python to PATH"** during installation
4. To verify it worked, open a terminal and type:
   ```
   python --version
   ```
   You should see something like `Python 3.12.10`.

**Opening a terminal:**
- **Windows:** Press the Windows key, type `cmd`, and open **Command Prompt** (or search for **Terminal**)
- **Mac:** Open **Terminal** from Applications > Utilities

## Step 2: Download this project

1. On this GitHub page, click the green **Code** button near the top
2. Click **Download ZIP**
3. Unzip the downloaded file to a folder you'll remember (e.g., your Documents folder)
4. Open a terminal and navigate to that folder:
   ```
   cd "C:\Users\YourName\Documents\Commander Deck Creator"
   ```
   (Replace the path with wherever you unzipped it.)

## Step 3: Install the tool

In your terminal (from the project folder), run:

```
pip install .
```

This installs the `edh-builder` command and everything it needs. You only need to do this once.

---

## Step 4: Export your collection from ManaBox

1. Open ManaBox on your phone/tablet
2. Go to your **collection** (or a specific binder you want to build from)
3. Tap the **share/export** button
4. Choose **CSV** format
5. Send the file to your computer (email it to yourself, AirDrop, Google Drive, etc.)
6. Save it somewhere easy to find (e.g., your Downloads folder)

## Step 5 (Optional but Recommended): Load the card database

By default, the tool looks up each card online one at a time, which can take several minutes for large collections. You can speed this up dramatically by downloading Scryfall's card database once:

1. Go to [scryfall.com/docs/api/bulk-data](https://scryfall.com/docs/api/bulk-data)
2. Find **Oracle Cards** in the list and click the download link (it's about 160 MB)
3. Save the file to your computer
4. Run this command (adjust the path to wherever you saved the file):
   ```
   edh-builder load-bulk-data "C:\Users\YourName\Downloads\oracle-cards-20260302100247.json"
   ```

This only needs to be done once. After loading, collection resolution takes seconds instead of minutes.

---

## Building your decks

Now for the fun part. You have three main commands:

### Build 4 decks

```
edh-builder build --collection my_cards.csv
```

This will:
- Import your collection
- Suggest 4 commanders that work well with your cards
- Build a 100-card deck for each commander
- Distribute shared cards fairly so no single deck hogs the best stuff
- Estimate each deck's power bracket
- Export decklists you can import into other apps

**Want to pick some commanders yourself?** Provide 1 to 4, and the tool suggests the rest:

```
edh-builder build --collection my_cards.csv --commanders "Atraxa, Praetors' Voice"
```

```
edh-builder build --collection my_cards.csv --commanders "Atraxa, Praetors' Voice" --commanders "Krenko, Mob Boss"
```

**Choose where files are saved and what format:**

```
edh-builder build --collection my_cards.csv --output-dir my_decks --format moxfield
```

Supported formats: `manabox`, `moxfield`, `archidekt` (you can use `--format` multiple times for several formats at once).

### Get commander suggestions

Not sure which commanders to build around? This command analyzes your collection and recommends commanders you have the best cards for:

```
edh-builder suggest --collection my_cards.csv
```

Already picked one or two? Tell the tool so it suggests complementary commanders:

```
edh-builder suggest --collection my_cards.csv --selected "Atraxa, Praetors' Voice"
```

### Estimate a deck's bracket

Have an existing decklist and want to know its bracket? The tool checks for Game Changers, infinite combos, mass land destruction, and extra turn cards:

```
edh-builder estimate-bracket --decklist my_deck.txt
```

If the commander isn't auto-detected, specify it:

```
edh-builder estimate-bracket --decklist my_deck.txt --commander "Atraxa, Praetors' Voice"
```

---

## Troubleshooting

**"edh-builder is not recognized"** — Python's script folder isn't in your system PATH. Try running the command as `python -m src.cli` instead, or re-install Python with the "Add to PATH" option checked.

**"No module named src"** — Make sure your terminal is in the project folder (the one with `pyproject.toml` in it) and that you ran `pip install .`

**First run is slow** — This is normal if you skipped Step 5. The tool is downloading card data from Scryfall one at a time. It gets cached locally, so the second run will be fast.

---

## For developers

Install with dev dependencies:

```
pip install -e ".[dev]"
```

Run tests:

```
pytest tests/ -v
```
