/* Studio — define the card. Three moves: draw a block, draw a value pair in it,
   link the pair to a WHO concept. Everything else is editing text. */

const $ = (id) => document.getElementById(id);
const state = {
  country: null,
  pack: null,
  blocks: [],          // [{id, block_type, bbox, hint, value_pairs:[{id, concept, ...}]}]
  selected: null,      // {kind:'block'|'pair', b:index, p:index|null}
  mode: null,          // null | 'block' | 'pair'
  dirty: false,
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
  return countries;
}

async function loadPack(country, source = "studio") {
  const response = await fetch(`/api/studio/${country}/pack?source=${source}`);
  if (!response.ok) return null;
  const pack = await response.json();
  state.country = country;
  state.pack = pack;
  state.blocks = JSON.parse(JSON.stringify(pack.blocks || []));
  state.selected = null;
  state.mode = null;
  state.dirty = false;
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

function rectEl(cls, bbox, title) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  el.setAttribute("x", bbox[0]); el.setAttribute("y", bbox[1]);
  el.setAttribute("width", Math.max(1, bbox[2] - bbox[0]));
  el.setAttribute("height", Math.max(1, bbox[3] - bbox[1]));
  el.setAttribute("class", cls);
  if (title) el.setAttribute("data-title", title);
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

  state.blocks.forEach((block, bi) => {
    if (!block.bbox) return;
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const selB = state.selected?.kind === "block" && state.selected.b === bi;
    const bRect = rectEl("block-rect" + (selB ? " selected" : ""), block.bbox);
    bRect.dataset.b = bi;
    group.appendChild(bRect);
    group.appendChild(labelEl(block.bbox, `▣ ${block.id} · ${block.block_type}`));
    (block.value_pairs || []).forEach((pair, pi) => {
      if (!pair.bbox) return;
      const selP = state.selected?.kind === "pair" && state.selected.b === bi && state.selected.p === pi;
      const pr = rectEl("pair-rect" + (selP ? " selected" : ""), pair.bbox);
      pr.dataset.b = bi; pr.dataset.p = pi;
      group.appendChild(pr);
      group.appendChild(labelEl(pair.bbox, pair.label_local || pair.id));
    });
    svg.appendChild(group);
  });
}

/* ---------- panel ---------- */

function renderPanel() {
  const sel = state.selected;
  $("panel-empty").classList.toggle("hidden", !!sel);
  $("editor-block").classList.toggle("hidden", sel?.kind !== "block");
  $("editor-pair").classList.toggle("hidden", sel?.kind !== "pair");
  $("mode-pair").disabled = sel?.kind !== "block";
  $("delete").disabled = !sel;
  $("mode-block").classList.toggle("active", state.mode === "block");
  $("mode-pair").classList.toggle("active", state.mode === "pair");

  $("mode-hint").innerHTML = state.mode === "block"
    ? "Drag a rectangle on the card."
    : state.mode === "pair"
      ? "Drag a rectangle <b>inside the selected block</b>."
      : 'Draw a box around a <b>region</b> of the card (demographics, treatment grid…). Then select it and draw smaller boxes for each value the card asks for.';

  if (sel?.kind === "block") {
    const b = state.blocks[sel.b];
    $("b-id").value = b.id;
    $("b-type").value = b.block_type;
    $("b-hint").value = b.hint || "";
  } else if (sel?.kind === "pair") {
    const p = state.blocks[sel.b].value_pairs[sel.p];
    const concept = (state.pack.concepts || []).find((c) => c.id === p.concept);
    $("p-id").value = p.id;
    $("p-label").value = p.label_local || "";
    $("p-concept-shown").innerHTML = p.concept
      ? `<b>${concept?.who_name || p.concept}</b> <span class="cid">(${p.concept})</span>`
      : "no concept linked yet";
    $("p-type").value = p.value_type || concept?.value_type || "";
    $("p-sens").value = p.sensitivity || concept?.sensitivity || "";
  }
}

/* ---------- selection & editing ---------- */

function selectFromEvent(event) {
  const target = event.target;
  if (target.dataset.p !== undefined) {
    state.selected = { kind: "pair", b: +target.dataset.b, p: +target.dataset.p };
  } else if (target.dataset.b !== undefined) {
    state.selected = { kind: "block", b: +target.dataset.b, p: null };
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
  $("p-id").addEventListener("input", (e) => { pair().id = e.target.value; markDirty(); renderOverlay(); });
  $("p-label").addEventListener("input", (e) => { pair().label_local = e.target.value || null; markDirty(); });
  $("p-concept-search").addEventListener("input", conceptSearch);
  $("p-concept-search").addEventListener("focus", conceptSearch);
}

function block() { return state.blocks[state.selected.b]; }
function pair() { return block().value_pairs[state.selected.p]; }

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
    row.innerHTML = `<b>${c.who_name}</b> <span class="cid">${c.id} · ${c.value_type}</span>`;
    row.addEventListener("mousedown", (e) => {
      e.preventDefault();
      pair().concept = c.id;
      pair().value_type = c.value_type;
      pair().sensitivity = c.sensitivity;
      $("p-concept-search").value = "";
      list.classList.add("hidden");
      markDirty();
      renderPanel();
    });
    list.appendChild(row);
  }
  list.classList.toggle("hidden", hits.length === 0);
}

/* ---------- drawing ---------- */

function wireDrawing() {
  const svg = $("overlay");
  let start = null;
  let rubber = null;

  svg.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (!state.mode) { selectFromEvent(e); return; }
    if (state.mode === "pair" && state.selected?.kind !== "block") { state.mode = null; render(); return; }
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

    if (state.mode === "block") {
      state.blocks.push({
        id: `block_${state.blocks.length + 1}`,
        block_type: "value_pairs",
        bbox,
        hint: null,
        value_pairs: [],
      });
      state.selected = { kind: "block", b: state.blocks.length - 1, p: null };
    } else if (state.mode === "pair") {
      const b = block();
      // the contract: a pair sits inside its block. Clamp the drawing rather
      // than silently grow the crop the model will see.
      bbox[0] = Math.max(bbox[0], b.bbox[0]); bbox[1] = Math.max(bbox[1], b.bbox[1]);
      bbox[2] = Math.min(bbox[2], b.bbox[2]); bbox[3] = Math.min(bbox[3], b.bbox[3]);
      b.value_pairs.push({
        id: `pair_${b.value_pairs.length + 1}`,
        concept: "",
        value_type: null,
        bbox,
        label_local: null,
        sensitivity: null,
      });
      state.selected = { kind: "pair", b: state.blocks.indexOf(b), p: b.value_pairs.length - 1 };
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
    blocks: state.blocks,
  };
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
$("template-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`/api/studio/${state.country}/template`, { method: "POST", body });
  const result = await response.json();
  if (!response.ok) { alert(result.detail); return; }
  $("template-info").textContent = `template ${result.size[0]}×${result.size[1]} px uploaded`;
  await loadPack(state.country, "studio");
});
$("mode-block").addEventListener("click", () => { state.mode = state.mode === "block" ? null : "block"; render(); });
$("mode-pair").addEventListener("click", () => { state.mode = state.mode === "pair" ? null : "pair"; render(); });
$("delete").addEventListener("click", () => {
  const sel = state.selected;
  if (!sel) return;
  if (sel.kind === "block") state.blocks.splice(sel.b, 1);
  else state.blocks[sel.b].value_pairs.splice(sel.p, 1);
  state.selected = null;
  state.dirty = true;
  render();
});
$("save").addEventListener("click", save);
wirePanel();
wireDrawing();
boot();
