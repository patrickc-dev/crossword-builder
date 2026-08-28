#!/usr/bin/env python3
"""
Crossword Builder
==================
Generates a crossword puzzle grid from a word list and exports a printable HTML file.

Word lists are supplied as a JSON file with three tiers:
  must   — always placed; any random seed that fails to place one of these is discarded
  core   — important words; a few may be dropped to fit the grid
  filler — used to fill remaining space and bridge gaps

JSON format (see demo/input.json for an example):
  {
    "title": "My Crossword",
    "must":   [["WORD", "Clue text"], ...],
    "core":   [["WORD", "Clue text"], ...],
    "filler": [["WORD", "Clue text"], ...]
  }

Usage:
  python generate.py grid [core|total] [size] [--seed N] [--words FILE] [--output-dir DIR]
      Generate grid → <output-dir>/grid.json + <output-dir>/crossword_output.txt

  python generate.py fill [size] [--input FILE] [--output-dir DIR]
      Fill blanks with dictionary words → <output-dir>/grid_with_filler.json + crossword_output_with_filler.txt

  python generate.py pick [size] [--input FILE] [--output-dir DIR]
      Interactively choose fill words (y/n/q per candidate)

  python generate.py html [--input FILE] [--output-dir DIR]
      Render HTML → <output-dir>/crossword.html

  grid core 17   Minimise unplaced core words in a 17x17 grid  (default)
  grid total 19  Maximise total words placed in a 19x19 grid

`--words` selects the input word-list JSON (default: demo/input.json).
`--input` (fill/pick/html) selects a previously generated grid JSON file.
`--output-dir` selects where output files are written (default: output/).
"""

import json
import os
import random
import sys
import urllib.request

_ROOT             = os.path.dirname(__file__)
DEFAULT_WORDS_FILE = os.path.join(_ROOT, "demo", "input.json")
DEFAULT_OUTPUT_DIR = os.path.join(_ROOT, "private", "output")
WORDS_FILE        = os.path.join(_ROOT, "src", "common_words.txt")
WORDS_URL         = "https://raw.githubusercontent.com/first20hours/google-10000-english/master/google-10000-english-no-swears.txt"

# ---------------------------------------------------------------------------
# Word list loading
# ---------------------------------------------------------------------------

def load_words(path):
    """Load a word-list JSON file. Returns (must_words, core_words, filler_words, title)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    must = [tuple(x) for x in data.get("must", [])]
    core = [tuple(x) for x in data.get("core", [])]
    filler = [tuple(x) for x in data.get("filler", [])]
    title = data.get("title", "Crossword Puzzle")
    return must, core, filler, title

# ---------------------------------------------------------------------------
# Crossword grid
# ---------------------------------------------------------------------------

class CrosswordGrid:
    def __init__(self):
        self.grid = {}          # (row, col) -> letter
        self.placed_words = []  # (word, row, col, direction, clue, tier)

    def get(self, r, c):
        return self.grid.get((r, c))

    def can_place(self, word, row, col, direction):
        """Check whether `word` fits at (row, col) in `direction` ('H' or 'V').
        Returns (ok, num_intersections).
        Rules:
          - No letter immediately before/after the word in its direction
          - Empty cells must have no perpendicular neighbours (prevents accidental merges)
          - Occupied cells must match the letter (intersection)
          - Must intersect at least one existing word (except the very first)
        """
        dr, dc = (0, 1) if direction == 'H' else (1, 0)
        intersections = 0

        if self.get(row - dr, col - dc) is not None:
            return False, 0
        if self.get(row + dr * len(word), col + dc * len(word)) is not None:
            return False, 0

        for i, letter in enumerate(word):
            r, c = row + dr * i, col + dc * i
            existing = self.get(r, c)
            if existing is not None:
                if existing != letter:
                    return False, 0
                intersections += 1
            else:
                if direction == 'H':
                    if self.get(r - 1, c) or self.get(r + 1, c):
                        return False, 0
                else:
                    if self.get(r, c - 1) or self.get(r, c + 1):
                        return False, 0

        if self.placed_words and intersections == 0:
            return False, 0
        return True, intersections

    def place(self, word, row, col, direction, clue, tier="filler"):
        dr, dc = (0, 1) if direction == 'H' else (1, 0)
        for i, letter in enumerate(word):
            self.grid[(row + dr * i, col + dc * i)] = letter
        self.placed_words.append((word, row, col, direction, clue, tier))

    def bbox(self):
        rs = [r for r, c in self.grid]
        cs = [c for r, c in self.grid]
        return min(rs), max(rs), min(cs), max(cs)

    def bbox_area(self):
        r0, r1, c0, c1 = self.bbox()
        return (r1 - r0 + 1) * (c1 - c0 + 1)

    def _placement_score(self, word, row, col, direction, intersections):
        """Score a candidate placement. More intersections = better; larger bounding box = worse."""
        dr, dc = (0, 1) if direction == 'H' else (1, 0)
        new_cells = {(row + dr * i, col + dc * i) for i in range(len(word))}
        all_cells = set(self.grid.keys()) | new_cells
        rs = [r for r, c in all_cells]
        cs = [c for r, c in all_cells]
        area = (max(rs) - min(rs) + 1) * (max(cs) - min(cs) + 1)
        return intersections * 500 - area

    def _fits(self, word, row, col, direction, max_size):
        """Return True if placing word keeps the bounding box within max_size x max_size."""
        dr, dc = (0, 1) if direction == 'H' else (1, 0)
        new_cells = {(row + dr * i, col + dc * i) for i in range(len(word))}
        all_cells = set(self.grid.keys()) | new_cells
        rs = [r for r, c in all_cells]
        cs = [c for r, c in all_cells]
        return (max(rs) - min(rs) + 1) <= max_size and (max(cs) - min(cs) + 1) <= max_size

    def find_best_placement(self, word, max_size):
        """Return (row, col, direction, score) for the highest-scoring valid placement, or None."""
        best, best_score = None, float('-inf')
        for pw, pr, pc, pd, _, _ in self.placed_words:
            odr, odc = (0, 1) if pd == 'H' else (1, 0)
            nd = 'V' if pd == 'H' else 'H'
            ndr, ndc = (0, 1) if nd == 'H' else (1, 0)
            for i, pl in enumerate(pw):
                for j, wl in enumerate(word):
                    if pl != wl:
                        continue
                    ir, ic = pr + odr * i, pc + odc * i
                    nr, nc = ir - ndr * j, ic - ndc * j
                    ok, ints = self.can_place(word, nr, nc, nd)
                    if ok and self._fits(word, nr, nc, nd, max_size):
                        score = self._placement_score(word, nr, nc, nd, ints)
                        if score > best_score:
                            best_score, best = score, (nr, nc, nd)
        return (best[0], best[1], best[2], best_score) if best else None

    def display(self):
        r0, r1, c0, c1 = self.bbox()
        return '\n'.join(
            ' '.join(self.get(r, c) or '.' for c in range(c0, c1 + 1))
            for r in range(r0, r1 + 1)
        )

    def clue_list(self):
        """Return (across, down, numbered) where each entry is (number, clue_string)."""
        starts = {}
        for word, row, col, direction, clue, tier in self.placed_words:
            starts.setdefault((row, col), []).append((direction, word, clue, tier))
        numbered = {pos: i + 1 for i, pos in enumerate(sorted(starts))}

        across, down = [], []
        for pos, entries in starts.items():
            n = numbered[pos]
            for direction, word, clue, tier in entries:
                tag = " [filler]" if tier == "filler" else ""
                entry = (n, f"{clue} ({len(word)}){tag}  -> {word}")
                (across if direction == 'H' else down).append(entry)

        across.sort()
        down.sort()
        return across, down, numbered

# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def _run_once(seed, max_size, must_words, core_words, filler_words):
    """Attempt one grid layout using the given random seed.
    Returns (grid, unplaced_must, unplaced_core).
    """
    rng = random.Random(seed)

    grid = CrosswordGrid()

    # Choose a long word as the anchor and place it at the origin
    all_words = must_words + core_words
    anchor = rng.choice([w for w in all_words if len(w[0]) >= 9])
    w, clue = anchor
    tier = "must" if anchor in must_words else "core"
    grid.place(w, 0, -(len(w) // 2), 'H' if seed % 2 == 0 else 'V', clue, tier)

    def place_list(word_list, tier):
        unplaced = []
        for w, clue in word_list:
            if (w, clue) == anchor:
                continue
            result = grid.find_best_placement(w, max_size)
            if result:
                grid.place(w, result[0], result[1], result[2], clue, tier)
            else:
                unplaced.append(w)
        return unplaced

    remaining_must = must_words[:]
    remaining_core = core_words[:]
    rng.shuffle(remaining_must)
    rng.shuffle(remaining_core)

    unplaced_must = place_list(remaining_must, "must")
    unplaced_core = place_list(remaining_core, "core")

    fillers = filler_words[:]
    rng.shuffle(fillers)
    for w, clue in fillers:
        result = grid.find_best_placement(w, max_size)
        if result:
            grid.place(w, result[0], result[1], result[2], clue, "filler")

    return grid, unplaced_must, unplaced_core


def generate_grid(must_words, core_words, filler_words, mode="core", max_size=17, seeds=2000, fixed_seed=None):
    """Run `seeds` random layouts and return the best grid.

    mode='core'  — minimise unplaced core words, then minimise bounding box area
    mode='total' — maximise total words placed, then minimise bounding box area
    fixed_seed   — run only this one seed instead of searching
    """
    best_grid, best_score, best_unplaced_core, best_seed = None, None, None, None

    start = (fixed_seed * 2000) if fixed_seed is not None else 0
    for seed in range(start, start + seeds):
        grid, unplaced_must, unplaced_core = _run_once(seed, max_size, must_words, core_words, filler_words)

        if unplaced_must:           # never accept a grid missing a must word
            continue

        score = (
            (len(unplaced_core), grid.bbox_area())         if mode == "core"
            else (-len(grid.placed_words), grid.bbox_area())
        )

        if best_score is None or score < best_score:
            best_score, best_grid, best_unplaced_core, best_seed = score, grid, unplaced_core, seed

        if mode == "core" and len(unplaced_core) == 0:
            break

    return best_grid, best_unplaced_core, best_seed

# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

def generate_html(grid, title, output_path):
    across, down, numbered = grid.clue_list()
    r0, r1, c0, c1 = grid.bbox()

    rows_html = ""
    for r in range(r0, r1 + 1):
        rows_html += "  <tr>\n"
        for c in range(c0, c1 + 1):
            letter = grid.get(r, c)
            if letter:
                n = numbered.get((r, c), "")
                num_span = f'<span class="num">{n}</span>' if n else ""
                rows_html += f'    <td class="white">{num_span}</td>\n'
            else:
                rows_html += '    <td class="black"></td>\n'
        rows_html += "  </tr>\n"

    def clue_items(clues):
        out = ""
        for n, text in clues:
            display = text.split("  ->")[0].replace(" [filler]", "")
            out += f"  <li><span class='num'>{n}.</span> {display}</li>\n"
        return out

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Georgia, serif; background: #fff; color: #111; padding: 20px; }}
  h1 {{ text-align: center; font-size: 22px; letter-spacing: 2px; margin-bottom: 24px; }}
  .puzzle-wrap {{ display: flex; gap: 32px; align-items: flex-start; justify-content: center; }}
  table {{ border-collapse: collapse; border: 2px solid #111; }}
  td {{ width: 34px; height: 34px; padding: 0; position: relative; }}
  td.black {{ background: #111; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  td.white {{ background: #fff; border: 1px solid #555; }}
  td.white .num {{ position: absolute; top: 2px; left: 2px; font-size: 9px; line-height: 1;
                   font-family: Arial, sans-serif; font-weight: bold; color: #111; }}
  .clues {{ display: flex; gap: 28px; }}
  .clue-section h2 {{ font-size: 13px; font-weight: bold; letter-spacing: 2px;
                      text-transform: uppercase; border-bottom: 1px solid #111;
                      padding-bottom: 4px; margin-bottom: 8px; }}
  .clue-section ol {{ list-style: none; font-size: 12px; line-height: 1.7; max-width: 200px; }}
  .clue-section li .num {{ font-weight: bold; margin-right: 3px; }}
  @media print {{ body {{ padding: 10px; }} @page {{ margin: 1cm; }} }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="puzzle-wrap">
  <div><table>
{rows_html}  </table></div>
  <div class="clues">
    <div class="clue-section">
      <h2>Across</h2>
      <ol>
{clue_items(across)}      </ol>
    </div>
    <div class="clue-section">
      <h2>Down</h2>
      <ol>
{clue_items(down)}      </ol>
    </div>
  </div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML saved to {output_path}")

# ---------------------------------------------------------------------------
# Stage-2 dictionary fill
# ---------------------------------------------------------------------------

def get_word_list():
    """Load (and cache) a common English word list. Downloads on first use."""
    if not os.path.exists(WORDS_FILE):
        print(f"Downloading word list to {WORDS_FILE} ...", flush=True)
        urllib.request.urlretrieve(WORDS_URL, WORDS_FILE)
        print("  Done.", flush=True)

    with open(WORDS_FILE, encoding="utf-8") as f:
        raw_words = f.read().splitlines()

    result = []
    for w in raw_words:
        w = w.strip().upper()
        if w.isalpha() and 3 <= len(w) <= 10:
            result.append(w)
    return result


def _slot_extent(grid, anchor_r, anchor_c, direction, max_size):
    """Return (back, fwd): cells before/after anchor a word can span in direction.
    Stops at perpendicular adjacency violations; passes through existing letters
    (potential extra intersections). Hard-capped by max_size bbox constraint."""
    ndr, ndc = (1, 0) if direction == 'V' else (0, 1)
    r0, r1, c0, c1 = grid.bbox()

    slack = max_size - ((r1 - r0 + 1) if direction == 'V' else (c1 - c0 + 1))
    if direction == 'V':
        hard_back = max(0, anchor_r - (r0 - slack))
        hard_fwd  = max(0, (r1 + slack) - anchor_r)
    else:
        hard_back = max(0, anchor_c - (c0 - slack))
        hard_fwd  = max(0, (c1 + slack) - anchor_c)

    back = 0
    for step in range(1, hard_back + 1):
        r, c = anchor_r - ndr * step, anchor_c - ndc * step
        if grid.get(r, c) is not None:
            back = step   # existing letter — potential intersection, keep going
        else:
            if direction == 'V':
                if grid.get(r, c - 1) or grid.get(r, c + 1):
                    break
            else:
                if grid.get(r - 1, c) or grid.get(r + 1, c):
                    break
            back = step

    fwd = 0
    for step in range(1, hard_fwd + 1):
        r, c = anchor_r + ndr * step, anchor_c + ndc * step
        if grid.get(r, c) is not None:
            fwd = step
        else:
            if direction == 'V':
                if grid.get(r, c - 1) or grid.get(r, c + 1):
                    break
            else:
                if grid.get(r - 1, c) or grid.get(r + 1, c):
                    break
            fwd = step

    return back, fwd


def _build_slot_index(grid, max_size):
    """For every letter in the grid, compute the available slot in the perpendicular direction.
    Returns {letter: [(anchor_r, anchor_c, direction, back, fwd), ...]}"""
    index = {}
    seen = set()
    for pw, pr, pc, pd, _, _ in grid.placed_words:
        perp = 'V' if pd == 'H' else 'H'
        odr, odc = (0, 1) if pd == 'H' else (1, 0)
        for i, letter in enumerate(pw):
            ar, ac = pr + odr * i, pc + odc * i
            key = (ar, ac, perp)
            if key in seen:
                continue
            seen.add(key)
            back, fwd = _slot_extent(grid, ar, ac, perp, max_size)
            if back + 1 + fwd >= 3:
                index.setdefault(letter, []).append((ar, ac, perp, back, fwd))
    return index


def fill_grid(grid, word_list, max_size):
    """Find and place words from word_list into remaining blank slots.
    Pre-filters by slot length; scores by intersections + length bonus (no area penalty).
    Returns list of newly placed words."""
    already_placed = {w for w, *_ in grid.placed_words}
    placed = []

    slot_index = _build_slot_index(grid, max_size)
    max_slot_len = max(
        (back + 1 + fwd for slots in slot_index.values() for _, _, _, back, fwd in slots),
        default=3,
    )

    print(f"  Scanning {len(word_list)} candidates (max slot length: {max_slot_len}) ...", flush=True)
    all_placements = []

    for word in word_list:
        if word in already_placed or len(word) > max_slot_len:
            continue

        best_score = float('-inf')
        best_pos   = None

        for j, letter in enumerate(word):
            for ar, ac, perp, back, fwd in slot_index.get(letter, []):
                # Pre-filter: anchor must land within this slot for this word length
                if j > back or (len(word) - 1 - j) > fwd:
                    continue
                ndr, ndc = (1, 0) if perp == 'V' else (0, 1)
                nr, nc = ar - ndr * j, ac - ndc * j
                ok, ints = grid.can_place(word, nr, nc, perp)
                if ok:
                    score = ints * 500 + len(word) * 50
                    if score > best_score:
                        best_score, best_pos = score, (nr, nc, perp)

        if best_pos:
            all_placements.append((best_score, word, best_pos))

    print(f"  {len(all_placements)} valid placements found. Placing ...", flush=True)

    all_placements.sort(reverse=True)
    for _score, word, (r, c, d) in all_placements:
        if word in already_placed:
            continue
        ok, _ = grid.can_place(word, r, c, d)
        if ok and grid._fits(word, r, c, d, max_size):
            grid.place(word, r, c, d, clue="", tier="filler")
            already_placed.add(word)
            placed.append(word)
            print(f"  Placed: {word}", flush=True)

    return placed


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_grid(grid, unplaced_core, path, totals=None, title=None):
    data = {
        "title": title or "Crossword Puzzle",
        "placed": [
            {"word": w, "row": r, "col": c, "direction": d, "clue": cl, "tier": tier}
            for w, r, c, d, cl, tier in grid.placed_words
        ],
        "unplaced_core": unplaced_core,
        "totals": totals or {},
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)
    print(f"Grid saved to {path}")


def load_grid(path):
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    grid = CrosswordGrid()
    for e in data["placed"]:
        tier = e.get("tier", "filler" if e.get("is_filler") else "core")
        grid.place(e["word"], e["row"], e["col"], e["direction"], e["clue"], tier)
    return grid, data.get("unplaced_core", []), data.get("totals", {}), data.get("title", "Crossword Puzzle")

# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_grid(mode, max_size, words_file, output_dir, fixed_seed=None):
    must_words, core_words, filler_words, title = load_words(words_file)

    grid, unplaced_core, best_seed = generate_grid(
        must_words, core_words, filler_words, mode=mode, max_size=max_size, fixed_seed=fixed_seed
    )

    if grid is None:
        print("ERROR: Could not place all must words. Try a larger grid size.")
        return

    totals = {"must": len(must_words), "core": len(core_words), "filler": len(filler_words)}
    n_must        = sum(1 for w, *_, tier in grid.placed_words if tier == "must")
    n_core        = sum(1 for w, *_, tier in grid.placed_words if tier == "core")
    placed_filler = [w for w, *_, tier in grid.placed_words if tier == "filler"]
    r0, r1, c0, c1 = grid.bbox()

    print(f"Seed:         {best_seed}")
    print(f"Grid size:    {r1-r0+1} rows x {c1-c0+1} cols")
    print(f"Must words:   {n_must} / {totals['must']} placed")
    print(f"Core words:   {n_core} / {totals['core']} placed  |  unplaced: {unplaced_core}")
    print(f"Filler words: {len(placed_filler)} / {totals['filler']} placed  |  placed: {placed_filler}")
    print(f"Total:        {len(grid.placed_words)} words\n")
    print(grid.display())

    across, down, _ = grid.clue_list()
    print("\nACROSS")
    for n, clue in across:
        print(f"  {n}. {clue}")
    print("\nDOWN")
    for n, clue in down:
        print(f"  {n}. {clue}")

    os.makedirs(output_dir, exist_ok=True)
    save_grid(grid, unplaced_core, os.path.join(output_dir, "grid.json"), totals, title)
    write_grid_preview(grid, unplaced_core, totals, os.path.join(output_dir, "crossword_output.txt"))


def _summary_lines(grid, unplaced_core, totals, new_words=None):
    n_must        = sum(1 for w, *_, tier in grid.placed_words if tier == "must")
    n_core        = sum(1 for w, *_, tier in grid.placed_words if tier == "core")
    placed_filler = [w for w, *_, tier in grid.placed_words if tier == "filler"]
    r0, r1, c0, c1 = grid.bbox()
    fill_line = (
        f"Fill words:   {len(new_words)} added  |  words: {', '.join(new_words)}"
        if new_words else
        f"Fill words:   0 added"
    )
    return [
        f"Grid size:    {r1-r0+1} rows x {c1-c0+1} cols",
        f"Must words:   {n_must} / {totals.get('must', n_must)} placed",
        f"Core words:   {n_core} / {totals.get('core', n_core)} placed  |  unplaced: {unplaced_core}",
        f"Filler words: {len(placed_filler)} / {totals.get('filler', len(placed_filler))} placed  |  placed: {placed_filler}",
        fill_line,
        f"Total:        {len(grid.placed_words)} words",
    ]


def _clues_lines(across, down):
    lines = ["ACROSS"]
    for n, text in across:
        clue_part = text.split("  ->")[0].replace(" [filler]", "").strip()
        word = text.split("  ->")[-1].strip()
        if not clue_part.split("(")[0].strip():
            length = clue_part.strip("() ")
            lines.append(f"  {n}. {word}  ({length})  *** ADD CLUE ***")
        else:
            lines.append(f"  {n}. {clue_part}")
    lines += ["", "DOWN"]
    for n, text in down:
        clue_part = text.split("  ->")[0].replace(" [filler]", "").strip()
        word = text.split("  ->")[-1].strip()
        if not clue_part.split("(")[0].strip():
            length = clue_part.strip("() ")
            lines.append(f"  {n}. {word}  ({length})  *** ADD CLUE ***")
        else:
            lines.append(f"  {n}. {clue_part}")
    return lines


def write_grid_preview(grid, unplaced_core, totals, path):
    across, down, _ = grid.clue_list()
    lines = (
        _summary_lines(grid, unplaced_core, totals)
        + ["", grid.display(), ""]
        + _clues_lines(across, down)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Output saved to {path}")


def cmd_html(input_file, output_dir):
    grid, unplaced_core, totals, title = load_grid(input_file)
    os.makedirs(output_dir, exist_ok=True)
    generate_html(grid, title, os.path.join(output_dir, "crossword.html"))


def write_fill_preview(grid, unplaced_core, totals, new_words, path):
    across, down, _ = grid.clue_list()
    lines = (
        _summary_lines(grid, unplaced_core, totals, new_words)
        + ["", grid.display(), ""]
        + _clues_lines(across, down)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Output saved to {path}")


def cmd_fill(max_size, input_file, output_dir):
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    grid, unplaced_core, totals, title = load_grid(input_file)
    print(f"Loaded grid: {len(grid.placed_words)} words placed.")

    word_list = get_word_list()
    print(f"{len(word_list)} candidate words loaded.\n")

    new_words = fill_grid(grid, word_list, max_size)

    r0, r1, c0, c1 = grid.bbox()
    print(f"\nFill complete: {len(new_words)} dictionary words added.")
    print(f"Total words:  {len(grid.placed_words)}")
    print(f"Grid size:    {r1-r0+1} rows x {c1-c0+1} cols")

    os.makedirs(output_dir, exist_ok=True)
    save_grid(grid, unplaced_core, os.path.join(output_dir, "grid_with_filler.json"), totals, title)
    write_fill_preview(grid, unplaced_core, totals, new_words, os.path.join(output_dir, "crossword_output_with_filler.txt"))


def cmd_pick(max_size, input_file, output_dir):
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    grid, unplaced_core, totals, title = load_grid(input_file)
    print(f"Loaded grid: {len(grid.placed_words)} words placed.")

    word_list = get_word_list()
    print(f"{len(word_list)} candidate words loaded.\n")

    already_placed = {w for w, *_ in grid.placed_words}
    slot_index = _build_slot_index(grid, max_size)
    max_slot_len = max(
        (back + 1 + fwd for slots in slot_index.values() for _, _, _, back, fwd in slots),
        default=3,
    )

    all_placements = []
    for word in word_list:
        if word in already_placed or len(word) > max_slot_len:
            continue
        best_score, best_pos = float('-inf'), None
        for j, letter in enumerate(word):
            for ar, ac, perp, back, fwd in slot_index.get(letter, []):
                if j > back or (len(word) - 1 - j) > fwd:
                    continue
                ndr, ndc = (1, 0) if perp == 'V' else (0, 1)
                nr, nc = ar - ndr * j, ac - ndc * j
                ok, ints = grid.can_place(word, nr, nc, perp)
                if ok:
                    score = ints * 500 + len(word) * 50
                    if score > best_score:
                        best_score, best_pos = score, (nr, nc, perp)
        if best_pos:
            all_placements.append((best_score, word, best_pos))

    all_placements.sort(reverse=True)
    print(f"{len(all_placements)} candidates found. Enter=1st  n=next  q=quit\n")

    new_words = []
    skipped = set()
    while True:
        # Recompute top 9 valid candidates after each placement
        valid = []
        for score, word, (r, c, d) in all_placements:
            if word in already_placed or word in skipped:
                continue
            ok, ints = grid.can_place(word, r, c, d)
            if ok and grid._fits(word, r, c, d, max_size):
                valid.append((score, word, r, c, d, ints))
            if len(valid) == 9:
                break

        if not valid:
            print("No more candidates.")
            break

        print(f"Top candidates ({len(new_words)} placed so far):")
        print(f"     {'word':15s} {'dir'}  {'int':>3}  {'score':>6}")
        for idx, (score, word, r, c, d, ints) in enumerate(valid, 1):
            print(f"  {idx}. {word:15s} {d}    {ints:>3}  {score:>6}")

        try:
            answer = input(f"  Pick (1-{len(valid)}/Enter=1/s=skip/q=quit): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if answer == 'q':
            break
        if answer == 's':
            skipped |= {word for _, word, *_ in valid}
            continue
        idx = int(answer) if answer.isdigit() else 1
        idx = max(1, min(idx, len(valid)))
        score, word, r, c, d, ints = valid[idx - 1]
        grid.place(word, r, c, d, clue="", tier="filler")
        already_placed.add(word)
        new_words.append(word)
        skipped.clear()
        print(f"  Placed: {word}\n")

    print(f"\nDone: {len(new_words)} words placed.")
    os.makedirs(output_dir, exist_ok=True)
    save_grid(grid, unplaced_core, os.path.join(output_dir, "grid_with_filler.json"), totals, title)
    write_fill_preview(grid, unplaced_core, totals, new_words, os.path.join(output_dir, "crossword_output_with_filler.txt"))

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _extract_flag(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        value = args[i + 1]
        return value, args[:i] + args[i + 2:]
    return default, args


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else "grid"
    if command == "grid":
        args = sys.argv[2:]
        fixed_seed, args = _extract_flag(args, "--seed")
        fixed_seed = int(fixed_seed) if fixed_seed is not None else None
        words_file, args = _extract_flag(args, "--words", DEFAULT_WORDS_FILE)
        output_dir, args = _extract_flag(args, "--output-dir", DEFAULT_OUTPUT_DIR)
        mode     = args[0] if len(args) > 0 else "core"
        max_size = int(args[1]) if len(args) > 1 else 17
        if mode not in ("core", "total"):
            print("Unknown mode. Use 'core' or 'total'.")
        else:
            start = (fixed_seed * 2000) if fixed_seed is not None else 0
            label = f"seeds {start}–{start + 1999}"
            print(f"Optimising for: {mode}  |  max grid size: {max_size}x{max_size}  |  {label}\n")
            cmd_grid(mode=mode, max_size=max_size, words_file=words_file, output_dir=output_dir, fixed_seed=fixed_seed)
    elif command in ("fill", "pick"):
        args = sys.argv[2:]
        input_file, args = _extract_flag(args, "--input")
        output_dir, args = _extract_flag(args, "--output-dir", DEFAULT_OUTPUT_DIR)
        input_file = input_file or os.path.join(output_dir, "grid.json")
        max_size = int(args[0]) if args else 17
        if command == "fill":
            cmd_fill(max_size=max_size, input_file=input_file, output_dir=output_dir)
        else:
            cmd_pick(max_size=max_size, input_file=input_file, output_dir=output_dir)
    elif command == "html":
        args = sys.argv[2:]
        output_dir, args = _extract_flag(args, "--output-dir", DEFAULT_OUTPUT_DIR)
        input_file, args = _extract_flag(args, "--input")
        input_file = input_file or os.path.join(output_dir, "grid_with_filler.json")
        cmd_html(input_file=input_file, output_dir=output_dir)
    else:
        print(__doc__)
