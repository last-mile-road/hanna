#!/usr/bin/env python3
"""Ground-truth capture UI. Standalone, stdlib only, no project imports.

    ~/.venv/bin/python tools/gt_capture/app.py            # http://127.0.0.1:8765
    ~/.venv/bin/python tools/gt_capture/app.py --port 8800

It writes to countries/mozambique/ground_truth/<form>.json and nowhere else.
Delete this folder and nothing in the pipeline breaks.
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sheet_io  # noqa: E402

ROOT = sheet_io.ROOT
FORM_YAML = ROOT / "countries/mozambique/form.draft.yaml"
BLOCK_FOR_GROUP = {
    "Identity": "general_demographic",
    "Registration": "classification",
    "Clinical history": "clinical_history",
    "Skin patches": "skin_patch",
    "Nerves": "nerve_assessment",
    "Eye, hand and foot exam": "def_exam",
    "Sensation and notes": "hand_feet_assessment",
    "WHO disability grade (EHF)": "def_exam",
    "Treatment": "treatment_follow",
}

CSS = """
:root{--ink:#1b1b1b;--dim:#6b6b6b;--line:#d8d8d8;--accent:#0b6b3a;--warn:#8a4b00;--bg:#fbfbf9}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:15px;margin:0 10px 0 0}
nav a{padding:3px 8px;border:1px solid var(--line);border-radius:4px;margin-right:4px;text-decoration:none;color:var(--ink);font-size:13px}
nav a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{display:grid;grid-template-columns:minmax(320px,46%) 1fr;gap:16px;padding:16px;align-items:start}
.viewer{position:sticky;top:64px}
.imgwrap{overflow:auto;max-height:78vh;border:1px solid var(--line);background:#fff}
.imgwrap img{display:block;max-width:none}
.zoom{display:flex;gap:8px;align-items:center;margin-bottom:8px;font-size:12px;color:var(--dim)}
.zoom input[type=range]{width:180px}
section{background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:14px}
section h2{font-size:14px;margin:0 0 10px;display:flex;justify-content:space-between;align-items:center;gap:10px}
section h2 img{max-height:54px;border:1px solid var(--line);border-radius:3px}
.row{display:grid;grid-template-columns:210px 1fr 54px;gap:10px;align-items:center;padding:5px 0;border-top:1px solid #f0f0f0}
.row:first-of-type{border-top:0}
.lbl{font-size:13px}
.lbl small{display:block;color:var(--dim);font-size:11px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:4px;overflow:hidden}
.seg label{padding:4px 12px;cursor:pointer;border-right:1px solid var(--line);font-size:13px}
.seg label:last-child{border-right:0}
.seg input{display:none}
.seg input:checked+span{background:var(--accent);color:#fff}
.seg span{display:block;padding:4px 12px;margin:-4px -12px}
input[type=text],input[type=number],select,textarea{width:100%;padding:5px 7px;border:1px solid var(--line);border-radius:4px;font:inherit;background:#fff}
textarea{min-height:44px;resize:vertical}
.chip{font-size:11px;color:var(--dim);text-align:right}
.chip.v{color:var(--accent)}
.locked{opacity:.45}
footer{position:sticky;bottom:0;background:#fff;border-top:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:7px 16px;border-radius:4px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.ghost{background:#fff;color:var(--ink);border-color:var(--line)}
.msg{font-size:13px}
.msg.err{color:#a11212}
.msg.ok{color:var(--accent)}
"""

JS = """
const zoom = document.getElementById('zoom');
const card = document.getElementById('card');
if (zoom && card) {
  const set = () => { card.style.width = zoom.value + 'px'; };
  zoom.addEventListener('input', set); set();
}
const toggle = document.getElementById('ident');
if (toggle) {
  const apply = () => {
    document.querySelectorAll('.ident').forEach(r => {
      r.classList.toggle('locked', !toggle.checked);
      r.querySelectorAll('input,select,textarea').forEach(i => i.disabled = !toggle.checked);
    });
  };
  toggle.addEventListener('change', apply);
  apply();  // honoured on load: the box starts ticked
}
document.getElementById('save').addEventListener('click', async () => {
  const form = document.getElementById('sheet');
  const values = {};
  new FormData(form).forEach((v, k) => { values[k] = v; });
  const body = {
    form: form.dataset.form,
    reviewer: document.getElementById('reviewer').value,
    include_identifying: document.getElementById('ident').checked,
    values,
  };
  const out = document.getElementById('msg');
  out.className = 'msg'; out.textContent = 'saving...';
  const res = await fetch('/api/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await res.json();
  if (res.ok) {
    out.className = 'msg ok';
    out.textContent = `saved: ${data.fields_verified}/${data.fields_total} verified`;
    setTimeout(() => location.reload(), 700);
  } else {
    out.className = 'msg err';
    out.textContent = (data.problems || ['save failed']).join(' | ');
  }
});
"""


def field_row(cid: str, concept: dict, current: dict) -> str:
    esc = html.escape
    value = current.get("value") or ""
    verified = current.get("status") == "verified"
    widget = sheet_io.widget_for(concept)
    options = sheet_io.allowed_values(concept)
    label = esc(concept.get("who_name") or cid)
    ident = concept.get("sensitivity") == "identifying"
    row_class = "row ident locked" if ident else "row"
    disabled = " disabled" if ident else ""

    if widget == "radio":
        opts = options or ["Yes", "No"]
        parts = [
            f'<label><input type="radio" name="{cid}" value=""'
            f'{" checked" if value == "" else ""}{disabled}><span>·</span></label>'
        ]
        for opt in opts:
            checked = " checked" if value == opt else ""
            parts.append(
                f'<label><input type="radio" name="{cid}" value="{esc(opt)}"{checked}{disabled}>'
                f'<span>{esc(opt)}</span></label>'
            )
        control = f'<div class="seg">{"".join(parts)}</div>'
    elif widget == "select":
        opts = ['<option value="">—</option>']
        for opt in options:
            sel = " selected" if value == opt else ""
            opts.append(f'<option value="{esc(opt)}"{sel}>{esc(opt)}</option>')
        control = f'<select name="{cid}"{disabled}>{"".join(opts)}</select>'
    elif widget == "number":
        control = f'<input type="number" step="1" name="{cid}" value="{esc(value)}"{disabled}>'
    elif widget == "date":
        control = (
            f'<input type="text" name="{cid}" value="{esc(value)}" '
            f'placeholder="YYYY, YYYY-MM or YYYY-MM-DD"{disabled}>'
        )
    elif widget == "textarea":
        control = f'<textarea name="{cid}"{disabled}>{esc(value)}</textarea>'
    else:
        control = f'<input type="text" name="{cid}" value="{esc(value)}"{disabled}>'

    chip = "✓" if verified else ""
    chip_class = "chip v" if verified else "chip"
    return (
        f'<div class="{row_class}"><div class="lbl">{label}<small>{cid}</small></div>'
        f'<div>{control}</div><div class="{chip_class}">{chip}</div></div>'
    )


def render_page(form_id: str) -> bytes:
    esc = html.escape
    concepts = sheet_io.load_concepts()
    sheet = sheet_io.load_sheet(form_id)
    template = ROOT / "countries/mozambique/template.jpg"

    nav = []
    for fid in sheet_io.FORMS:
        try:
            s = sheet_io.load_sheet(fid)
            _, v, t = sheet_io.compute_status(s.get("fields", {}), concepts)
            state = f"{v}/{t}"
        except FileNotFoundError:
            state = "missing"
        cls = ' class="on"' if fid == form_id else ""
        nav.append(f'<a href="/?form={fid}"{cls}>{fid.replace("form", "#")} · {state}</a>')

    _, verified, scoreable = sheet_io.compute_status(sheet.get("fields", {}), concepts)

    # Identifying values live in the gitignored sidecar. They are still shown and
    # still scored locally; they just never enter the committed sheet.
    sidecar = sheet_io.load_identifying(form_id)
    identifying_ids = [c["id"] for c in concepts.values() if c.get("sensitivity") == "identifying"]
    ident_have = sum(
        1 for cid in identifying_ids
        if sidecar.get(cid) or sheet["fields"].get(cid, {}).get("status") == "verified"
    )

    def merged(cid: str) -> dict:
        field = dict(sheet["fields"].get(cid, {}))
        if cid in sidecar and field.get("status") != "verified":
            field = {"value": sidecar[cid], "status": "verified", "source": "sidecar (local)"}
        return field

    sections = []
    keep = set(sheet_io.scored_ids())
    for group, ids in sheet_io.GROUPS.items():
        block = BLOCK_FOR_GROUP.get(group)
        thumb = f'<img src="/crop?block={block}" alt="template region">' if block else ""
        rows = "".join(
            field_row(cid, concepts[cid], merged(cid))
            for cid in ids
            if cid in concepts and cid in keep
        )
        if rows:
            sections.append(f'<section><h2><span>{esc(group)}</span>{thumb}</h2>{rows}</section>')

    body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Ground truth — {form_id}</title><style>{CSS}</style></head><body>
<header><h1>Ground truth capture</h1><nav>{"".join(nav)}</nav>
<span style="font-size:12px;color:#6b6b6b">{esc(form_id)} · {verified} of {scoreable} scoreable verified · {len(sheet["fields"]) - scoreable} identifying, {ident_have} captured locally · template {"present" if template.exists() else "MISSING"}</span></header>
<form id="sheet" data-form="{form_id}"><main>
<div class="viewer">
  <div class="zoom"><span>zoom</span><input id="zoom" type="range" min="480" max="2600" step="40" value="1100">
  <span>drag the image to pan</span></div>
  <div class="imgwrap"><img id="card" src="/card?form={form_id}" alt="{form_id}"></div>
</div>
<div>{"".join(sections)}</div>
</main></form>
<footer>
  <label style="font-size:13px">reviewed by <input id="reviewer" type="text" style="width:150px" value="{esc(sheet.get("reviewed_by") or "")}"></label>
  <label style="font-size:13px"><input id="ident" type="checkbox" checked> capture identifying fields (local sidecar, not committed)</label>
  <button id="save" type="button">Save sheet</button>
  <span id="msg" class="msg"></span>
  <span style="font-size:12px;color:#6b6b6b">blank = still pending</span>
</footer>
<script>{JS}</script></body></html>"""
    return body.encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "gt-capture/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        try:
            if url.path == "/":
                self._send(200, render_page((qs.get("form") or ["form16"])[0]))
            elif url.path == "/card":
                form = (qs.get("form") or ["form16"])[0]
                path = sheet_io.IMAGES_DIR / f"{form}.jpg"
                if not path.exists():
                    self._send(404, b"card image not found", "text/plain")
                    return
                self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/jpeg")
            elif url.path == "/crop":
                self._send_crop((qs.get("block") or [""])[0])
            elif url.path == "/api/sheet":
                self._json(200, sheet_io.load_sheet((qs.get("form") or ["form16"])[0]))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as exc:  # keep serving; show the reason instead of dying
            self._send(500, f"error: {exc}".encode(), "text/plain")

    def _send_crop(self, block_id: str):
        from io import BytesIO

        from PIL import Image

        spec = yaml.safe_load(FORM_YAML.read_text())
        block = next((b for b in spec["blocks"] if b["id"] == block_id), None)
        if not block or not block.get("bbox"):
            self._send(404, b"no such block", "text/plain")
            return
        x, y, w, h = block["bbox"]
        template = Image.open(ROOT / "countries/mozambique/template.jpg")
        crop = template.crop((x, y, x + w, y + h))
        crop.thumbnail((520, 520))
        buf = BytesIO()
        crop.save(buf, format="PNG")
        self._send(200, buf.getvalue(), "image/png")

    def do_POST(self):
        if urlparse(self.path).path != "/api/save":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"problems": ["malformed JSON"]})
            return
        form = payload.get("form")
        if form not in sheet_io.FORMS:
            self._json(400, {"problems": [f"unknown form {form}"]})
            return
        try:
            sheet, problems = sheet_io.save_sheet(
                form,
                payload.get("values") or {},
                reviewer=(payload.get("reviewer") or "").strip(),
                include_identifying=bool(payload.get("include_identifying")),
            )
        except FileNotFoundError as exc:
            self._json(404, {"problems": [str(exc)]})
            return
        if problems:
            self._json(400, {"problems": problems})
            return
        self._json(200, {"fields_verified": sheet["fields_verified"], "fields_total": sheet["fields_total"]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ground-truth capture on http://{args.host}:{args.port}  (ctrl-c to stop)")
    print(f"sheets: {sheet_io.GT_DIR}")
    print(f"cards:  {sheet_io.IMAGES_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
