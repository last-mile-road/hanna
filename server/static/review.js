/* Review screen: one row per declared pair, accept or correct, then submit in one go. */

const $ = (id) => document.getElementById(id);
const runId = new URLSearchParams(location.search).get("run");

let data = null;
const decisions = new Map();  // pair_id -> {mode, value}

const BLOCK_LABELS = {
  general_demographic: "Identificação",
  classification: "Classificação",
  clinical_history: "História clínica",
  skin_patch: "Manchas na pele",
  hand_feet_assessment: "Mãos e pés",
  nerve_assessment: "Nervos",
  def_exam: "Grau de incapacidade",
  treatment_follow: "Tratamento",
};

function blockLabel(id) {
  return BLOCK_LABELS[id] || id;
}

if (!runId) {
  $("loading").textContent = "Falta o identificador da análise (?run=…).";
} else {
  load();
}

async function load() {
  const response = await fetch(`/api/runs/${runId}/review`, { cache: "no-store" });
  if (!response.ok) {
    $("loading").textContent = "Não foi possível carregar esta análise.";
    return;
  }
  data = await response.json();
  $("loading").classList.add("hidden");
  $("overview").classList.remove("hidden");
  renderOverview();
  renderSections();
  updateProgress();
}

/* ---------- overview ---------- */

function renderOverview() {
  const img = $("card");
  img.src = data.image.url;
  img.addEventListener("load", () => {
    const bbox = data.blocks[0] && data.blocks[0].bbox;
    if (bbox) showHighlight(bbox);
  });

  const badge = $("verdict-badge");
  badge.className = `badge ${data.validation_status || "review"}`;
  badge.textContent = data.validation_status || data.status;

  const chips = $("block-chips");
  chips.innerHTML = "";
  data.blocks.forEach((block, index) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (block.issue ? " issue" : "");
    chip.textContent = blockLabel(block.id);
    chip.addEventListener("click", () => {
      document.querySelector(`[data-section="${block.id}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
      if (block.bbox) showHighlight(block.bbox);
      setActiveChip(index);
    });
    chips.appendChild(chip);
  });
  setActiveChip(0);

  const firstIssue = data.blocks.find((block) =>
    block.pairs.some((pair) => pair.issue) || block.issue);
  $("first-issue").classList.toggle("hidden", !firstIssue);
  $("first-issue").addEventListener("click", () => {
    const target = firstIssue && (firstIssue.pairs.find((p) => p.issue)?.pair_id || null);
    const el = target
      ? document.querySelector(`[data-pair="${target}"]`)
      : document.querySelector(`[data-section="${firstIssue?.id}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function setActiveChip(index) {
  document.querySelectorAll("#block-chips .chip").forEach((chip, i) => {
    chip.classList.toggle("active", i === index);
  });
  const block = data.blocks[index];
  if (block && block.bbox) showHighlight(block.bbox);
}

function showHighlight(bbox) {
  const img = $("card");
  if (!img.naturalWidth) return;
  const [x1, y1, x2, y2] = bbox;
  const box = $("highlight");
  box.style.display = "block";
  box.style.left = `${(x1 / data.image.width) * 100}%`;
  box.style.top = `${(y1 / data.image.height) * 100}%`;
  box.style.width = `${((x2 - x1) / data.image.width) * 100}%`;
  box.style.height = `${((y2 - y1) / data.image.height) * 100}%`;
}

/* ---------- rows ---------- */

function renderSections() {
  const container = $("sections");
  container.innerHTML = "";
  data.blocks.forEach((block) => {
    const section = document.createElement("section");
    section.className = "section";
    section.dataset.section = block.id;

    const heading = document.createElement("h2");
    heading.innerHTML = `${blockLabel(block.id)}<span class="count">${block.pairs.length} campos</span>`;
    section.appendChild(heading);

    block.pairs.forEach((pair) => section.appendChild(renderPair(pair, block)));
    container.appendChild(section);
  });
}

function renderPair(pair, block) {
  const row = document.createElement("div");
  row.className = "pair";
  row.dataset.pair = pair.pair_id;

  const head = document.createElement("div");
  head.className = "pair-head";
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = pair.label;
  const value = document.createElement("span");
  value.className = "value" + (pair.value ? "" : " empty");
  value.textContent = pair.value || "— sem valor";
  value.dataset.extracted = pair.value || "";
  head.append(label, value);
  row.appendChild(head);

  if (pair.issue) {
    const note = document.createElement("div");
    note.className = "issue-note" + (pair.issue.severity === "info" ? " info" : "");
    note.textContent = pair.issue.advice || pair.issue.kind;
    row.appendChild(note);
  }

  const actions = document.createElement("div");
  actions.className = "pair-actions";
  const ok = document.createElement("button");
  ok.textContent = "Correcto";
  ok.addEventListener("click", () => decide(pair, "accepted", null, row, value));
  const fix = document.createElement("button");
  fix.className = "secondary";
  fix.textContent = "Corrigir";
  fix.addEventListener("click", () => openEditor(pair, row, value));
  actions.append(ok, fix);
  row.appendChild(actions);

  if (pair.review_mode) {
    applyState(row, pair.review_mode, pair.reviewed_value, value);
  }
  return row;
}

function openEditor(pair, row, valueEl) {
  if (row.querySelector(".editor")) return;
  const editor = document.createElement("div");
  editor.className = "editor";

  let input;
  if (pair.options && pair.options.length) {
    input = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "— escolher —";
    input.appendChild(blank);
    pair.options.forEach((option) => {
      const el = document.createElement("option");
      el.value = option;
      el.textContent = option;
      input.appendChild(el);
    });
    input.value = pair.value || "";
  } else {
    input = document.createElement("input");
    input.type = pair.value_type === "integer" ? "number" : "text";
    input.value = pair.value || "";
    input.placeholder = "valor correcto";
  }

  const save = document.createElement("button");
  save.textContent = "Guardar";
  save.addEventListener("click", () => {
    const value = input.value.trim();
    if (!value) return;
    decide(pair, "corrected", value, row, valueEl);
    editor.remove();
  });

  editor.append(input, save);
  row.appendChild(editor);
  input.focus();
}

function decide(pair, mode, value, row, valueEl) {
  decisions.set(pair.pair_id, { mode, value });
  applyState(row, mode, value, valueEl);
  updateProgress();
}

function applyState(row, mode, value, valueEl) {
  row.classList.remove("accepted", "corrected", "skipped");
  row.classList.add(mode);
  const extracted = valueEl.dataset.extracted || "";
  if (mode === "corrected") {
    valueEl.classList.remove("empty");
    valueEl.textContent = extracted
      ? `${value}  ·  lido: ${extracted}`
      : value;
  } else if (mode === "accepted") {
    valueEl.textContent = extracted || "— sem valor";
    valueEl.classList.toggle("empty", !extracted);
  }
}

/* ---------- progress and submit ---------- */

function updateProgress() {
  const total = data ? data.blocks.reduce((sum, block) => sum + block.pairs.length, 0) : 0;
  const done = decisions.size;
  $("progress").textContent = `${done} de ${total} revistos`;
  $("submit").disabled = done === 0;
}

$("submit").addEventListener("click", async () => {
  $("submit").disabled = true;
  $("submit").textContent = "A enviar…";
  const reviews = [...decisions.entries()].map(([pair_id, decision]) => ({
    pair_id, mode: decision.mode, value: decision.value,
  }));
  const response = await fetch(`/api/runs/${runId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviews }),
  });
  if (!response.ok) {
    $("submit").disabled = false;
    $("submit").textContent = "Tentar de novo";
    $("summary").classList.remove("hidden");
    $("summary").textContent = "O servidor recusou a revisão. Tente novamente.";
    return;
  }
  const result = await response.json();
  const corrected = result.corrected.length;
  $("submit").textContent = "Revisto";
  $("summary").classList.remove("hidden");
  $("summary").innerHTML =
    `<strong>Revisão registada.</strong><p>${result.updated} valores guardados, ` +
    `${corrected} corrigidos${result.not_in_ledger.length ? `, ${result.not_in_ledger.length} sem registo` : ""}.</p>` +
    (corrected ? `<p class="corrected-list">Corrigidos: ${result.corrected.join(", ")}</p>` : "");
  $("summary").scrollIntoView({ behavior: "smooth", block: "center" });
});
