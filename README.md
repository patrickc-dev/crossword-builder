# Crossword Builder

A crossword puzzle generator: give it a word list, get a filled grid, printable HTML, and a clue list.

## TL;DR

```
python generate.py grid total 17 --words demo/input.json --output-dir demo    # 1. start the grid with your words
python generate.py fill --input demo/grid.json --output-dir demo              # 2. fill it automatically (or `pick` for manual)
python generate.py html --input demo/grid_with_filler.json --output-dir demo  # 3. export as HTML
```

1. **Start the grid** with your words — input: [`demo/input.json`](demo/input.json) → output: [`demo/grid.json`](demo/grid.json), [`demo/crossword_output.txt`](demo/crossword_output.txt)
2. **Fill the grid** automatically (`fill`) or manually (`pick`) — output: [`demo/grid_with_filler.json`](demo/grid_with_filler.json), [`demo/crossword_output_with_filler.txt`](demo/crossword_output_with_filler.txt)
3. **Export as HTML** — output: [`demo/crossword.html`](demo/crossword.html)

## Files

| File | Description |
|------|-------------|
| `generate.py` | Main script — grid logic, dictionary fill, HTML export |
| `demo/input.json` | Example word list you can generate a puzzle from |
| `demo/grid.json` | Example grid after step 1 (must/core words placed) |
| `demo/crossword_output.txt` | Text preview of the grid + clues after step 1 |
| `demo/grid_with_filler.json` | Example grid after step 2 (dictionary-filled) |
| `demo/crossword_output_with_filler.txt` | Text preview of the grid + clues after step 2 |
| `demo/crossword.html` | Example printable puzzle after step 3 |

Your own word lists and generated puzzles are written to `private/output/` by default, and the whole `private/` folder is gitignored — keep any personal or private word lists out of version control.

---

## Usage

### 1. Generate a grid
```
python generate.py grid [mode] [size] [--words FILE] [--output-dir DIR]
```

| Argument | Options | Default | Description |
|----------|---------|---------|-------------|
| `mode` | `core` or `total` | `core` | Optimisation strategy (see below) |
| `size` | any integer | `17` | Maximum grid width and height |
| `--words` | path to a word-list JSON | `demo/input.json` | The words and clues to build the puzzle from |
| `--output-dir` | path | `private/output/` | Where generated files are written |

**Optimisation modes:**
- `core` — minimise the number of unplaced core words. Best when you want as many important clues as possible.
- `total` — maximise the total number of words placed (core + filler). Trades some core words for a denser, more filled grid.

Runs up to 2000 random seeds and picks the best result. Saves to `<output-dir>/grid.json`.

**Examples:**
```
python generate.py grid                                   # demo word list, core mode, 17x17
python generate.py grid core 15 --words my_words.json      # your own word list, 15x15
python generate.py grid total 19 --output-dir build        # write output to build/ instead
```

### 2. Export to HTML
```
python generate.py html [--input FILE] [--output-dir DIR]
```
Loads a grid JSON file (`<output-dir>/grid_with_filler.json` by default) and writes `<output-dir>/crossword.html`.
Open in a browser and use `Ctrl+P` to print or save as PDF.

### 3. Fill remaining space with dictionary words
```
python generate.py fill [size] [--input FILE] [--output-dir DIR]
python generate.py pick [size] [--input FILE] [--output-dir DIR]   # interactive version
```
Loads a previously generated grid and fills empty slots with common English words, downloaded on first use.

---

## How it works

### Word tiers

Your word list JSON splits words into three priority tiers:

- **must** — always placed. If a random seed fails to place any of these, it is discarded entirely.
- **core** — important words. A few may be dropped to fit the grid.
- **filler** — generic words used to bridge gaps and make the grid denser.

### Word list format

```json
{
  "title": "My Crossword",
  "must":   [["WORD", "Clue text"], ["OTHER", "Another clue"]],
  "core":   [["ANSWER", "Clue text"]],
  "filler": [["FILL", "Clue text"]]
}
```

See `demo/input.json` for a complete example.

### Grid algorithm

1. Pick a random long word (from `must` + `core`) as the **anchor** and place it at the centre.
2. Place **must words** in random order, each perpendicular to an existing word at a shared letter.
3. Place **core words** in random order, same strategy.
4. Fill remaining space with **filler words**.
5. Each placement is scored by: **intersections × 500 − bounding box area** — favouring words that cross more existing words and keep the grid compact.
6. Repeat with different random seeds. Keep the best result according to the chosen optimisation mode.
