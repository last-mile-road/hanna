/* Studio — the country team marks what the card asks; the moderator blocks it.
 *
 * Country flow: draw a box per value pair, give it a display name and a
 * description, link it to a WHO concept, mark it required or sensitive.
 * Admin flow (toggle): draw blocks, choose block types, absorb pairs into
 * blocks — the extraction-strategy layer. The country team never sees it.
 *
 * On save, pairs not absorbed into an admin block are wrapped server-side
 * into provisional auto-blocks (marked studio_auto) so the draft loads; the
 * admin pass merges or grows them later.
 */

const $ = (id) => document.getElementById(id);
const state = {
  country: null,
  pack: null,
  pairs: [],           // flat: {id, label_local, description, concept, value_type, sensitivity, bbox, required, required_hint, block: index|null}
  blocks: [],          // admin blocks: {id, block_type, bbox, hint, ...extra}
  selected: null,      // {kind:'pair'|'block', p:index | b:index}
  mode: null,          // null | 'block' | 'pair'
  admin: false,
  dirty: false,
  errors: [],
};

/* ---------- loading ---------- */

async function loadCountries() {
  const countries = await fetch("/api/studio/countries").then((r) => r.json());
  const sel = $("country");
  sel.innerHTML = "";
  for (const c of countries) {
    const opt = document.createElement("option");
    opt.value = c.country;
    opt.textContent = c.country + (c.has_template ? "" : " — sem template");
    sel.appendChild(opt);
  }
}

async function loadPack(country, source = "studio") {
  const response = await fetch(`/api/studio/${country}/pack?source=${source}`);
  if (!response.ok) return null;
  const pack = await response.json();
  state.country = country;
  state.pack = pack;
  state.blocks = (pack.blocks || []).map((b) => ({ ...b, value_pairs: undefined }));
  state.pairs = [];
  (pack.blocks || []).forEach((b, bi) => {
    (b.value_pairs || []).forEach((p) => state.pairs.push({ ...p, block: bi }));
  });
  state.selected = null;
  state.mode = null;
  state.dirty = false;
  state.errors = pack.errors || [];
  render();
  return pack;
}

/* ---------- svg ---------- */

function svgPoint(event) {
  const svg = $("overlay");
  const box = svg.getBoundingClientRect();
  const [W, H] = state.pack.template_size;
  return [
    Math.max(0, Math.min(W, Math.round((event.clientX - box.left) * (W / box.width)))),
    Math.max(0, Math.min(H, Math.round((event.clientY - box.top) * (H / box.height)))),
  ];
}

function rectEl(cls, bbox) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  el.setAttribute("x", bbox[0]); el.setAttribute("y", bbox[1]);
  el.setAttribute("width", Math.max(1, bbox[2] - bbox[0]));
  el.setAttribute("height", Math.max(1, bbox[3] - bbox[1]));
  el.setAttribute("class", cls);
  return el;
}

function labelEl(bbox, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "text");
  el.setAttribute("x", bbox[0] + 4); el.setAttribute("y", bbox[1] + 16);
  el.setAttribute("class", "rect-label");
  el.textContent = text;
  return el;
}

function render() {
  renderHeader();
  renderOverlay();
  renderPanel();
}

function renderHeader() {
  const pack = state.pack;
  $("template-info").textContent = pack.template_size
    ? `template ${pack.template_size[0]}×${pack.template_size[1]} px`
    : "no template yet — upload one above";
  $("source-note").textContent = pack.editable
    ? `editing ${pack.source_file}`
    : `showing the live pack (${pack.source_file}); saving creates your editable Studio copy`;
  $("admin-toggle").classList.remove("hidden");
  const errors = (state.dirty ? state.errors : pack.errors) || [];
  $("errors").classList.toggle("hidden", !errors.length);
  $("errors").innerHTML = errors.length
    ? "The draft has problems (it will run once these are fixed):<ul>"
      + errors.map((e) => `<li>${e}</li>`).join("") + "</ul>"
    : "";
}

function renderOverlay() {
  const svg = $("overlay");
  const [W, H] = state.pack.template_size || [1000, 700];
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.innerHTML = "";
  if (!state.pack.template_size) { $("template").src = ""; return; }
  $("template").src = `/api/studio/${state.country}/template`;

  if (state.admin) {
    state.blocks.forEach((block, bi) => {
      if (!block.bbox) return;
      const sel = state.selected?.kind === "block" && state.selected.b === bi;
      const r = rectEl("block-rect" + (sel ? " selected" : ""), block.bbox);
      r.dataset.b = bi;
      svg.appendChild(r);
      svg.appendChild(labelEl(block.bbox, `▣ ${block.id} · ${block.block_type}`));
    });
  }

  state.pairs.forEach((pair, pi) => {
    if (!pair.bbox) return;
    const sel = state.selected?.kind === "pair" && state.selected.p === pi;
    const r = rectEl("pair-rect" + (sel ? " selected" : ""), pair.bbox);
    r.dataset.p = pi;
    svg.appendChild(r);
    const concept = pair.concept ? ` → ${pair.concept}` : "";
    svg.appendChild(labelEl(pair.bbox, `${pair.label_local || pair.id}${concept}`));
  });
}

/* ---------- panel ---------- */

function renderPanel() {
  const sel = state.selected;
  $("panel-empty").classList.toggle("hidden", !!sel);
  $("editor-block").classList.toggle("hidden", !(sel?.kind === "block" && state.admin));
  $("editor-pair").classList.toggle("hidden", sel?.kind !== "pair");
  $("mode-block").classList.toggle("hidden", !state.admin);
  $("delete").disabled = !sel;
  $("mode-block").classList.toggle("active", state.mode === "block");
  $("mode-pair").classList.toggle("active", state.mode === "pair");

  $("mode-hint").innerHTML = state.mode === "pair"
    ? "Drag a rectangle around one value: a name line, a date, a tick column, a table."
    : state.mode === "block"
      ? "Drag a block region. Pairs whose centre falls inside are absorbed into it."
      : 'Draw a box around each value the card asks for. Give it a display name, describe it in a line, and pick what it means from the WHO list. Blocks come later (admin).';

  if (sel?.kind === "block" && state.admin) {
    const b = state.blocks[sel.b];
    $("b-id").value = b.id;
    $("b-type").value = b.block_type;
    $("b-hint").value = b.hint || "";
  } else if (sel?.kind === "pair") {
    const p = state.pairs[sel.p];
    const concept = (state.pack.concepts || []).find((c) => c.id === p.concept);
    $("p-label").value = p.label_local || "";
    $("p-desc").value = p.description || "";
    $("p-concept-shown").innerHTML = p.concept
      ? `<b>${concept?.who_name || p.concept}</b> <span class="cid">(${p.concept})</span>`
      : "no WHO concept linked yet";
    $("p-type").value = p.value_type || concept?.value_type || "";
    $("p-required").checked = !!p.required;
    $("p-hint").value = p.required_hint || "";
    $("p-sens").value = p.sensitivity || concept?.sensitivity || "clinical";
  }
}

/* ---------- selection & editing ---------- */

function selectFromEvent(event) {
  const target = event.target;
  if (target.dataset.p !== undefined) {
    state.selected = { kind: "pair", p: +target.dataset.p };
  } else if (target.dataset.b !== undefined) {
    state.selected = { kind: "block", b: +target.dataset.b };
  } else {
    state.selected = null;
  }
  state.mode = null;
  render();
}

function markDirty() { state.dirty = true; }

function wirePanel() {
  $("b-id").addEventListener("input", (e) => { block().id = e.target.value; markDirty(); renderOverlay(); });
  $("b-type").addEventListener("change", (e) => { block().block_type = e.target.value; markDirty(); });
  $("b-hint").addEventListener("input", (e) => { block().hint = e.target.value || null; markDirty(); });
  $("p-label").addEventListener("input", (e) => { pair().label_local = e.target.value || null; markDirty(); renderOverlay(); });
  $("p-desc").addEventListener("input", (e) => { pair().description = e.target.value || null; markDirty(); });
  $("p-required").addEventListener("change", (e) => { pair().required = e.target.checked || null; markDirty(); });
  $("p-hint").addEventListener("input", (e) => { pair().required_hint = e.target.value || null; markDirty(); });
  $("p-sens").addEventListener("change", (e) => { pair().sensitivity = e.target.value; markDirty(); });
  $("p-concept-search").addEventListener("input", conceptSearch);
  $("p-concept-search").addEventListener("focus", conceptSearch);
}

function block() { return state.blocks[state.selected.b]; }
function pair() { return state.pairs[state.selected.p]; }

function conceptSearch() {
  const query = $("p-concept-search").value.trim().toLowerCase();
  const list = $("concept-list");
  list.innerHTML = "";
  const concepts = state.pack.concepts || [];
  const hits = concepts.filter((c) =>
    !query ||
    c.id.toLowerCase().includes(query) ||
    c.who_name.toLowerCase().includes(query) ||
    (c.aliases || []).some((a) => a.toLowerCase().includes(query))
  ).slice(0, 12);
  for (const c of hits) {
    const row = document.createElement("div");
    row.innerHTML = `<b>${c.who_name}</b> <span class="cid">${c.id} · ${c.value_type} · ${c.tier}</span>`;
    row.addEventListener("mousedown", (e) => {
      e.preventDefault();
      pair().concept = c.id;
      pair().value_type = c.value_type;
      if (!pair().sensitivity) pair().sensitivity = c.sensitivity;
      $("p-concept-search").value = "";
      list.classList.add("hidden");
      markDirty();
      renderPanel();
      renderOverlay();
    });
    list.appendChild(row);
  }
  list.classList.toggle("hidden", hits.length === 0);
}

/* ---------- drawing ---------- */

function nextPairId() {
  let n = state.pairs.length + 1;
  const taken = new Set(state.pairs.map((p) => p.id));
  while (taken.has(`vp_${String(n).padStart(3, "0")}`)) n += 1;
  return `vp_${String(n).padStart(3, "0")}`;
}

function absorbPairsInto(bi, bbox) {
  // centre test: the pair belongs where its middle sits
  state.pairs.forEach((p) => {
    if (!p.bbox) return;
    const cx = (p.bbox[0] + p.bbox[2]) / 2;
    const cy = (p.bbox[1] + p.bbox[3]) / 2;
    const inside = cx >= bbox[0] && cx <= bbox[2] && cy >= bbox[1] && cy <= bbox[3];
    if (inside) p.block = bi;
    else if (p.block === bi) p.block = null;
  });
}

function wireDrawing() {
  const svg = $("overlay");
  let start = null;
  let rubber = null;

  svg.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (!state.mode) { selectFromEvent(e); return; }
    start = svgPoint(e);
    rubber = rectEl("rubber", [start[0], start[1], start[0] + 1, start[1] + 1]);
    svg.appendChild(rubber);
    e.preventDefault();
  });

  svg.addEventListener("mousemove", (e) => {
    if (!start || !rubber) return;
    const now = svgPoint(e);
    rubber.setAttribute("x", Math.min(start[0], now[0]));
    rubber.setAttribute("y", Math.min(start[1], now[1]));
    rubber.setAttribute("width", Math.abs(now[0] - start[0]));
    rubber.setAttribute("height", Math.abs(now[1] - start[1]));
  });

  svg.addEventListener("mouseup", (e) => {
    if (!start || !rubber) return;
    const end = svgPoint(e);
    const bbox = [
      Math.min(start[0], end[0]), Math.min(start[1], end[1]),
      Math.max(start[0], end[0]), Math.max(start[1], end[1]),
    ];
    start = null; rubber.remove(); rubber = null;
    if (bbox[2] - bbox[0] < 8 || bbox[3] - bbox[1] < 8) return; // a click, not a drag

    if (state.mode === "block" && state.admin) {
      state.blocks.push({
        id: `block_${state.blocks.length + 1}`,
        block_type: "value_pairs",
        bbox, hint: null,
      });
      absorbPairsInto(state.blocks.length - 1, bbox);
      state.selected = { kind: "block", b: state.blocks.length - 1 };
    } else if (state.mode === "pair") {
      state.pairs.push({
        id: nextPairId(),
        label_local: null, description: null, concept: "",
        value_type: null, sensitivity: null,
        bbox, required: null, required_hint: null,
        block: null,
      });
      state.selected = { kind: "pair", p: state.pairs.length - 1 };
    }
    state.mode = null;
    state.dirty = true;
    render();
  });
}

/* ---------- save ---------- */

async function save() {
  const button = $("save");
  button.disabled = true;
  $("save-state").textContent = "saving…";
  const payload = {
    form: state.pack.form || {},
    blocks: state.blocks.map((b) => ({ ...b })),
    pairs: state.pairs
      .filter((p) => p.block === null)
      .map(({ block, ...p }) => p),  // block assignment is not a yaml field
  };
  // pairs absorbed into admin blocks travel inside their block
  payload.blocks.forEach((b, bi) => {
    b.value_pairs = state.pairs
      .filter((p) => p.block === bi)
      .map(({ block, ...p }) => p);
  });

  const response = await fetch(`/api/studio/${state.country}/draft`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  button.disabled = false;
  if (!response.ok) {
    $("save-state").textContent = result.detail || "save failed";
    return;
  }
  state.dirty = false;
  state.errors = result.errors;
  $("save-state").textContent = result.errors.length
    ? `saved with ${result.errors.length} problem(s)`
    : "saved — draft validates clean";
  await loadPack(state.country, "studio");
}

/* ---------- boot ---------- */

async function boot() {
  await loadCountries();
  const initial = new URLSearchParams(location.search).get("country") || $("country").value;
  $("country").value = initial;
  await loadPack(initial, "studio");
}

$("country").addEventListener("change", (e) => {
  location.search = `?country=${e.target.value}`;
});
$("new-country").addEventListener("click", async () => {
  const name = prompt("Country name (a-z, used as folder id), e.g. bangladesh");
  if (!name) return;
  const response = await fetch("/api/studio/countries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const result = await response.json();
  if (!response.ok) { alert(result.detail); return; }
  location.search = `?country=${result.country}`;
});
$("admin-mode").addEventListener("change", (e) => {
  state.admin = e.target.checked;
  state.selected = null;
  state.mode = null;
  render();
});
$("template-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`/api/studio/${state.country}/template`, { method: "POST", body });
  const result = await response.json();
  if (!response.ok) { alert(result.detail); return; }
  const c = result.cleaning || {};
  const skew = c.skew_refused
    ? ` — skew ${c.skew_refused}° too large, please re-scan`
    : c.deskew_applied ? ` — straightened ${c.deskew_applied}°` : "";
  $("template-info").textContent = `template ${result.size[0]}×${result.size[1]} px uploaded${skew}`;
  await loadPack(state.country, "studio");
});
$("mode-block").addEventListener("click", () => {
  if (!state.admin) return;
  state.mode = state.mode === "block" ? null : "block"; render();
});
$("mode-pair").addEventListener("click", () => { state.mode = state.mode === "pair" ? null : "pair"; render(); });
$("delete").addEventListener("click", () => {
  const sel = state.selected;
  if (!sel) return;
  if (sel.kind === "block") {
    const removed = state.blocks.splice(sel.b, 1)[0];
    state.pairs.forEach((p) => { if (p.block === sel.b) p.block = null; else if (p.block > sel.b) p.block -= 1; });
    void removed;
  } else {
    state.pairs.splice(sel.p, 1);
  }
  state.selected = null;
  state.dirty = true;
  render();
});
$("save").addEventListener("click", save);
wirePanel();
wireDrawing();
boot();
