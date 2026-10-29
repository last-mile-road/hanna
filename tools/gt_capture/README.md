# gt_capture — ground-truth capture UI

A local page for reading a filled card and filling its sheet in one pass. Each
field gets the widget its concept declares: `+`/`-` buttons for nerve and tick
fields, dropdowns for fixed choices, number boxes for counts, text for handwriting.

**This is throwaway tooling.** Nothing in `core/` or the pipeline imports it.
Delete the folder and only the convenience is lost.

## Run

```bash
cd ~/Documents/hanna_project/hanna_server
~/.venv/bin/python tools/gt_capture/app.py          # http://127.0.0.1:8765
~/.venv/bin/python tools/gt_capture/app.py --port 8800
```

No new dependency: standard library plus `pyyaml` and `Pillow`, both already in
`~/.venv`. Streamlit was considered and not installed on purpose; a 30-package
web framework in the shared project venv buys nothing a page of HTML does not.

## What it does

- left: the filled card, size slider from 480 to 2600 px, scroll to pan;
- right: eight sections in card order, each with a thumbnail of that region on
  the blank template so you know where to look;
- blank stays pending. Only a value you type becomes `verified`;
- save is one POST and writes `countries/mozambique/ground_truth/<form>.json`.

Values are validated before anything is written. A `+` in an integer field, or
`MB` where the options are `MB_adult`/`MB_child`, is rejected with the reason.

## The identifying-field rule

Seven fields are classed `identifying`: name, patient ID, previous ID, phone,
birth year, locality, village. They are captured by default, because the test
cards are dummy cards and those fields must be read correctly.

Storage is split:

- the value goes to `countries/mozambique/ground_truth/_identifying/<form>.json`,
  which is gitignored;
- the committed sheet keeps the field pending.

`benchmarks/benchmark_harness.py` merges the sidecar into the expected values, so
identifying fields are scored like any other. Untick the box to stop them being
submitted; the stored values are left alone.

Reason: the repo is heading for public GitHub, and a sheet full of names would
block that. Splitting costs one file and keeps both properties.

## Layout

| File | What it is |
|---|---|
| `app.py` | HTTP server and HTML. No project imports. |
| `sheet_io.py` | grouping, widget choice, validation, save. Testable without a browser. |

Tests: `tests/test_gt_capture.py` (round trip, validation, sidecar, grouping).
