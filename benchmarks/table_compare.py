"""Score the two table readers from their HTML, not from their text stream.

2026-10-30, second pass. The first pass scored MinerU through the docvortex flash tier
(text-only preview) and both readers through raw model text. Both understated the readers:
- MinerU standard (the tier the web UI uses) returns layout + slanet tables + HTML.
- PaddleOCR-VL's doc_parser returns the same tables as HTML.
So the unit of comparison is a cell, and the output is parsed as a table.

    python benchmarks/table_compare.py            # run both readers, score, write JSON
    python benchmarks/table_compare.py --rescore  # reuse saved HTML
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from benchmark_harness import GroundTruth  # noqa: E402
from core import settings  # noqa: E402

WORK = Path("/home/sam/tmp/opencode/tables")
READERS = Path("/home/sam/tmp/opencode/readers")
MINERU_API = "http://127.0.0.1:8001/v1/file_parse"
PADDLE_DIR = Path("/home/sam/Public/PaddleOCR-VL")

FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]
BLOCKS = ["def_exam", "nerve_assessment"]

NERVE_ORDER = ["cubital", "radial_cutaneous", "popliteal_external"]
SIDES = ["right", "left"]
GRADE_ROWS = {"olhos": 0, "mãos": 1, "maos": 1, "pés": 2, "pes": 2, "pês": 2}
MUSCLE = {"n": "Normal", "f": "Weak", "a": "Paralysed"}


# ---------------------------------------------------------------- HTML -> matrix

def table_matrix(fragment: str) -> list[list[str]]:
    """Expand the first <table> into a rectangular grid, applying colspan/rowspan."""
    match = re.search(r"<table[^>]*>(.*?)</table>", fragment, re.S | re.I)
    if not match:
        return []
    body = match.group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S | re.I)
    grid: list[list[str | None]] = []
    spans: dict[tuple[int, int], int] = {}
    for r_index, row in enumerate(rows):
        cells = re.findall(r"<t[dh][^>]*>.*?</t[dh]>", row, re.S | re.I)
        out: list[str | None] = []
        col = 0
        for cell in cells:
            while (r_index, col) in spans:
                out.append(None)
                col += 1
            text = re.sub(r"<[^>]+>", " ", cell)
            text = html.unescape(re.sub(r"\s+", " ", text)).strip()
            cspan = re.search(r'colspan="?(\d+)', cell, re.I)
            rspan = re.search(r'rowspan="?(\d+)', cell, re.I)
            width = int(cspan.group(1)) if cspan else 1
            height = int(rspan.group(1)) if rspan else 1
            out.append(text)
            for extra in range(1, width):
                out.append(None)
            if height > 1:
                for down in range(1, height):
                    spans[(r_index + down, col)] = width
            col += width
        while (r_index, col) in spans:
            out.append(None)
            col += 1
        grid.append(out)
    width = max((len(r) for r in grid), default=0)
    return [[cell if cell is not None else "" for cell in row + [""] * (width - len(row))]
            for row in grid]


def norm_cell(value: str) -> str:
    value = value.strip()
    if value in {"O", "o", "0", "○", "◯", "Ø", "ø", "C", "c"}:
        return "0"
    if value in {"I", "l", "|", "/", "1"}:
        return "1"
    if value in {"Z", "z", "2"}:
        return "2"
    if value in {"十", "七", "t", "T", "†", "+"}:
        return "+"
    if value in {"—", "–", "一", "~", "~", "_", "-", "·"}:
        return "-"
    return value


def value_near(label_row: list[str], index: int) -> str:
    if index < len(label_row):
        return norm_cell(label_row[index])
    return ""


# ---------------------------------------------------------------- readers

def run_mineru(images: list[Path], out: Path) -> dict[str, str]:
    payload = {
        "files": [{"source": {"type": "local", "path": str(p)}} for p in images],
        "tier": "standard",
        "output_formats": ["markdown"],
    }
    req = urllib.request.Request(
        MINERU_API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as response:
        body = json.load(response)
    result: dict[str, str] = {}
    for entry in body["files"]:
        name = Path(entry["name"]).stem
        result[name] = entry["content"]["markdown"] or ""
        (out / f"{name}.html").write_text(result[name])
    return result


def run_paddle(images: list[Path], out: Path) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(PADDLE_DIR / ".venv-client/bin/paddleocr"), "doc_parser",
         "--input", str(images[0].parent), "--save_path", str(out),
         "--vl_rec_backend", "llama-cpp-server",
         "--vl_rec_server_url", "http://localhost:8118/v1",
         "--vl_rec_api_model_name", "paddleocr-vl"],
        check=True, capture_output=True, text=True, timeout=3600,
    )
    result: dict[str, str] = {}
    for stem in (p.stem for p in images):
        candidates = list(out.glob(f"{stem}_res.json")) + list(out.rglob(f"{stem}_res.json"))
        if candidates:
            data = json.loads(candidates[0].read_text())
            blocks = data.get("res", data).get("parsing_res_list", [])
            result[stem] = "\n".join(b.get("block_content", "") for b in blocks)
        else:
            md = list(out.rglob(f"{stem}.md"))
            result[stem] = md[0].read_text() if md else ""
        (out / f"{stem}.html").write_text(result[stem])
    return result


# ---------------------------------------------------------------- scoring

def score_def_exam(matrix: list[list[str]], truth: dict) -> list[dict]:
    rows = {"grades": [], "ticks": [], "muscles": []}
    for row in matrix:
        joined = " ".join(row).lower()
        first, last = row[0] if row else "", row[-1] if row else ""
        labels = [norm_cell(cell).lower() for cell in row]
        for label, index in GRADE_ROWS.items():
            # The label must be its own cell: "Exame dos olhos" is a header, not a grade.
            if label in labels and "grau" not in joined:
                key = ["eye", "hand", "foot"][index]
                rows["grades"].append((f"disability_grade_{key}_right", norm_cell(first)))
                rows["grades"].append((f"disability_grade_{key}_left", norm_cell(last)))
        if "pestanejo" in joined:
            rows["ticks"].append(("eye_blinking_right", norm_cell(first)))
            rows["ticks"].append(("eye_blinking_left", norm_cell(last)))
        if "lago" in joined:
            rows["ticks"].append(("eye_lagophthalmos_right", norm_cell(first)))
            rows["ticks"].append(("eye_lagophthalmos_left", norm_cell(last)))
        if "abdu" in joined or "cedos" in joined:
            rows["muscles"].append(("hand_ulnar_right", MUSCLE.get(first.lower()[:1], first)))
            rows["muscles"].append(("hand_ulnar_left", MUSCLE.get(last.lower()[:1], last)))
        if "dorsi" in joined:
            rows["muscles"].append(("foot_dorsiflex_right", MUSCLE.get(first.lower()[:1], first)))
            rows["muscles"].append(("foot_dorsiflex_left", MUSCLE.get(last.lower()[:1], last)))
    out = []
    for kind, pairs in rows.items():
        for concept, read in pairs:
            expected = truth.get(concept)
            if expected is None:
                continue
            if kind == "ticks":
                got = {"+": "Yes", "-": "No"}.get(read, read)
            else:
                got = read
            out.append({"concept": concept, "read": read, "value": got,
                        "expected": expected, "correct": got == expected, "kind": kind})
    return out


def score_nerve(matrix: list[list[str]], truth: dict) -> list[dict]:
    header: list[str] = []
    body: dict[str, list[str]] = {}
    for row in matrix:
        joined = " ".join(row).lower()
        if "nervo" in joined or "radial" in joined:
            header = [norm_cell(c) for c in row]
        for label in ("grosso", "dor"):
            if row and label in row[0].lower():
                body[label] = [norm_cell(c) for c in row[1:]]
    if len(header) < 7:
        return []

    expected_cells = [
        (f"nerve_{kind}_{nerve}_{side}", truth.get(f"nerve_{kind}_{nerve}_{side}"))
        for kind, label in (("enlargement", "grosso"), ("pain", "dor"))
        for nerve in NERVE_ORDER
        for side in SIDES
    ]
    values_by_label = {
        label: [cell for cell in body.get(label, []) if cell != ""]
        for label in ("grosso", "dor")
    }
    # An empty grid is a reader failure, not twelve minus signs, when the card has pluses.
    blank_grid = not any(values_by_label.values())
    any_plus = any(expected == "+" for _, expected in expected_cells if expected)

    out = []
    for kind, label in (("enlargement", "grosso"), ("pain", "dor")):
        values = body.get(label, [])
        for nerve_index, nerve in enumerate(NERVE_ORDER):
            for side_index, side in enumerate(SIDES):
                column = nerve_index * 2 + side_index
                concept = f"nerve_{kind}_{nerve}_{side}"
                expected = truth.get(concept)
                if expected is None:
                    continue
                if column < len(values) and values[column]:
                    read = values[column]
                else:
                    read = "(empty)" if blank_grid and any_plus else "-"
                out.append({"concept": concept, "read": read, "value": read,
                            "expected": expected, "correct": read == expected,
                            "kind": kind})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readers", default="mineru,paddle")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--blocks", default=",".join(BLOCKS))
    ap.add_argument("--out", default=str(ROOT / "output" / "table_compare.json"))
    args = ap.parse_args()

    forms = args.forms.split(",")
    blocks = args.blocks.split(",")
    inputs = WORK / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    images = []
    for form in forms:
        for block in blocks:
            source = READERS / form / f"{block}.jpg"
            if source.exists():
                target = inputs / f"{form}__{block}.jpg"
                if not target.exists():
                    shutil.copy(source, target)
                images.append(target)

    outputs: dict[str, dict[str, str]] = {}
    for reader in args.readers.split(","):
        out = WORK / reader
        out.mkdir(parents=True, exist_ok=True)
        if args.rescore and any(out.glob("*.html")):
            outputs[reader] = {p.stem: p.read_text() for p in out.glob("*.html")}
        else:
            started = time.monotonic()
            outputs[reader] = run_mineru(images, out) if reader == "mineru" else run_paddle(images, out)
            print(f"{reader}: {len(outputs[reader])} tables in {time.monotonic()-started:.0f}s")

    report = []
    for form in forms:
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR,
        ).expected
        for block in blocks:
            key = f"{form}__{block}"
            for reader in outputs:
                fragment = outputs[reader].get(key, "")
                matrix = table_matrix(fragment) if "<table" in fragment else []
                rows = (score_def_exam(matrix, truth) if block == "def_exam"
                        else score_nerve(matrix, truth))
                report.append({"form": form, "block": block, "reader": reader,
                               "table_found": bool(matrix), "rows": rows})
                ok = sum(r["correct"] for r in rows)
                print(f"  {form:7s} {block:18s} {reader:7s} {ok}/{len(rows)}"
                      f"{'' if matrix else '  (no table)'}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
