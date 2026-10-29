/* Review screen. The value is an input where it sits, and everything you touch is saved
   as you go — no Save button to forget. Tables mirror the printed card. */

const $ = (id) => document.getElementById(id);
const runId = new URLSearchParams(location.search).get("run");

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

let data = null;
let devMode = localStorage.getItem("hanna.devMode") !== "off";
const decisions = new Map();   // pair_id -> {mode, value}
const timers = new Map();      // pair_id -> debounce
let saveQueue = Promise.resolve();
const pairsById = new Map();

if (!runId) {
  $("loading").textContent = "Falta o identificador da análise (?run=…).";
} else {
  load();
}

$("dev-mode").checked = devMode;
document.body.classList.toggle("show-dev", devMode);
$("dev-mode").addEventListener("change", (event) => {
  devMode = event.target.checked;
  localStorage.setItem("hanna.devMode", devMode ? "on" : "off");
  document.body.classList.toggle("show-dev", devMode);
  updateProgress();
});

async function load() {
  const response = await fetch(`/api/runs/${runId}/review`, { cache: "no-store" });
  if (!response.ok) {
    $("loading").textContent = "Não foi possível carregar esta análise.";
    return;
  }
  data = await response.json();
  data.blocks.forEach((block) => block.pairs.forEach((pair) => pairsById.set(pair.pair_id, pair)));
  $("loading").classList.add("hidden");
  $("topline").classList.remove("hidden");
  const badge = $("verdict-badge");
  badge.className = `badge ${data.validation_status || "review"}`;
  badge.textContent = data.validation_status || data.status;
  render();
  const firstIssue = document.querySelector(".has-issue");
  $("next-issue").classList.toggle("hidden", !firstIssue);
  $("next-issue").addEventListener("click", () =>
    document.querySelector(".has-issue")?.scrollIntoView({ behavior: "smooth", block: "center" }));
}

/* ---------- render ---------- */

function render() {
  const container = $("sections");
  container.innerHTML = "";
  data.blocks.forEach((block) => {
    const section = document.createElement("section");
    section.className = "section";

    const head = document.createElement("div");
    head.className = "section-head";
    const title = document.createElement("h2");
    title.textContent = BLOCK_LABELS[block.id] || block.id;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${block.pairs.length} campos`;
    const showCrop = document.createElement("button");
    showCrop.className = "mini";
    showCrop.textContent = "No cartão";
    const acceptAll = document.createElement("button");
    acceptAll.className = "mini";
    acceptAll.textContent = "Tudo correcto";
    head.append(title, count, showCrop, acceptAll);
    section.appendChild(head);

    const crop = document.createElement("div");
    crop.className = "crop hidden";
    section.appendChild(crop);
    showCrop.addEventListener("click", () => {
      const opening = crop.classList.contains("hidden");
      if (opening) paintCrop(crop, block);
      crop.classList.toggle("hidden", !opening);
      showCrop.textContent = opening ? "Fechar" : "No cartão";
    });

    const inTable = new Set();
    (block.tables || []).forEach((table) => {
      (table.rows || []).forEach((row) => row.cells.forEach((cell) => inTable.add(cell)));
      section.appendChild(renderTable(table, block));
    });
    block.pairs
      .filter((pair) => !inTable.has(pair.pair_id))
      .forEach((pair) => section.appendChild(renderPair(pair)));

    acceptAll.addEventListener("click", () => {
      block.pairs.forEach((pair) => {
        if (pair.dev_only && !devMode) return;
        decide(pair.pair_id, "accepted", null);
      });
    });

    container.appendChild(section);
  });
  paint();
  updateProgress();
}

function paintCrop(el, block) {
  const [x1, y1, x2, y2] = block.bbox;
  const width = x2 - x1;
  const height = y2 - y1;
  el.style.backgroundImage = `url(${data.image.url})`;
  el.style.backgroundSize = `${(data.image.width / width) * 100}% ${(data.image.height / height) * 100}%`;
  el.style.backgroundPosition = `-${(x1 / width) * 100}% -${(y1 / height) * 100}%`;
  el.style.aspectRatio = `${width} / ${height}`;
}

function renderPair(pair) {
  const row = document.createElement("div");
  row.className = "pair" + (pair.dev_only ? " dev-row" : "") + (pair.issue ? " has-issue" : "");
  row.dataset.pair = pair.pair_id;

  const top = document.createElement("div");
  top.className = "pair-top";
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = pair.label;
  const ok = document.createElement("button");
  ok.className = "ok";
  ok.textContent = "✓";
  ok.title = "Este valor está correcto";
  ok.addEventListener("click", () => decide(pair.pair_id, "accepted", null));
  top.append(label, ok);
  row.appendChild(top);
  row.appendChild(control(pair));
  if (pair.issue) {
    const note = document.createElement("div");
    note.className = "issue-note" + (pair.issue.severity === "info" ? " info" : "");
    note.textContent = pair.issue.advice || pair.issue.kind;
    row.appendChild(note);
  }
  return row;
}

function control(pair, compact = false) {
  const wrap = document.createElement("div");
  wrap.className = compact ? "cell" : "field";
  wrap.dataset.pair = pair.pair_id;
  const extracted = pair.value ?? "";
  wrap.dataset.extracted = extracted;

  if (pair.value_type === "tick_box") {
    // One button that toggles: fewer taps and half the width, which lets six nerve
    // columns fit a phone screen.
    const tick = document.createElement("div");
    tick.className = "tick";
    const button = document.createElement("button");
    button.textContent = extracted || "·";
    button.addEventListener("click", () => {
      const decision = decisions.get(pair.pair_id);
      const current = decision?.mode === "corrected" ? decision.value : extracted;
      const next = current === "+" ? "-" : "+";
      if (next === extracted) decide(pair.pair_id, "accepted", null);
      else decide(pair.pair_id, "corrected", next);
    });
    tick.appendChild(button);
    wrap.appendChild(tick);
    return wrap;
  }

  const options = pair.options || [];
  let element;
  if (options.length && options.length <= 8) {
    element = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "—";
    element.appendChild(blank);
    options.forEach((option) => {
      const el = document.createElement("option");
      el.value = option;
      el.textContent = pair.option_labels?.[option] || option;
      element.appendChild(el);
    });
  } else if (pair.value_type === "text" || pair.value_type === "person_name") {
    element = document.createElement("textarea");
    element.rows = 1;
  } else {
    element = document.createElement("input");
    element.type = pair.value_type === "integer" || pair.value_type === "percent" ? "number" : "text";
    element.inputMode = pair.value_type === "integer" ? "numeric" : "text";
  }
  element.value = extracted;
  element.dataset.extracted = extracted;
  element.className = "control";
  element.addEventListener("change", () => {
    const value = element.value.trim();
    if (value === extracted.trim()) decide(pair.pair_id, "accepted", null);
    else if (value) decide(pair.pair_id, "corrected", value);
    else clearDecision(pair.pair_id);
  });
  wrap.appendChild(element);
  return wrap;
}

function renderTable(table, block) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const el = document.createElement("table");
  el.className = "form-table";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.appendChild(document.createElement("th"));
  (table.columns || []).forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  el.appendChild(thead);

  const tbody = document.createElement("tbody");
  (table.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const label = document.createElement("th");
    label.className = "row-label";
    label.textContent = row.label;
    tr.appendChild(label);
    row.cells.forEach((pairId) => {
      const pair = pairsById.get(pairId);
      const td = document.createElement("td");
      if (!pair) {
        td.textContent = "—";
      } else {
        tr.dataset.rows = tr.dataset.rows ? `${tr.dataset.rows},${pairId}` : pairId;
        if (pair.issue) tr.classList.add("has-issue");
        td.appendChild(control(pair, true));
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  el.appendChild(tbody);
  wrap.appendChild(el);
  return wrap;
}

/* ---------- state ---------- */

function decide(pairId, mode, value) {
  decisions.set(pairId, { mode, value });
  paint();
  updateProgress();
  clearTimeout(timers.get(pairId));
  timers.set(pairId, setTimeout(() => {
    timers.delete(pairId);
    saveQueue = saveQueue.then(async () => {
      try {
        const response = await fetch(`/api/runs/${runId}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reviews: [{ pair_id: pairId, mode, value }] }),
        });
        flash(response.ok ? "guardado" : "falhou ao guardar");
      } catch (error) {
        flash("falhou ao guardar");
      }
    });
  }, 600));
}

function clearDecision(pairId) {
  decisions.delete(pairId);
  paint();
  updateProgress();
}

function flash(text) {
  $("saved").textContent = text;
  setTimeout(() => { $("saved").textContent = ""; }, 1400);
}

function paint() {
  document.querySelectorAll("[data-pair].field, [data-pair].cell").forEach((wrap) => {
    const pairId = wrap.dataset.pair;
    const decision = decisions.get(pairId);
    const row = wrap.closest(".pair");
    if (row) {
      row.classList.toggle("accepted", decision?.mode === "accepted");
      row.classList.toggle("corrected", decision?.mode === "corrected");
    }
    const element = wrap.querySelector(".control");
    if (element) {
      const extracted = element.dataset.extracted ?? "";
      if (decision?.mode === "corrected") element.value = decision.value;
      else if (decision?.mode === "accepted") element.value = extracted;
    }
    const tick = wrap.querySelector(".tick");
    if (tick) {
      const shown = decision?.mode === "corrected" ? decision.value : (wrap.dataset.extracted || "");
      const button = tick.querySelector("button");
      if (button) {
        button.textContent = shown || "·";
        button.classList.toggle("plus", shown === "+");
        button.classList.toggle("minus", shown === "-");
      }
    }
  });
  document.querySelectorAll(".form-table tr[data-rows]").forEach((tr) => {
    const modes = tr.dataset.rows.split(",").map((id) => decisions.get(id)?.mode);
    tr.classList.toggle("accepted", modes.length > 0 && modes.every((mode) => mode === "accepted"));
  });
}

function updateProgress() {
  if (!data) return;
  let total = 0;
  let done = 0;
  data.blocks.forEach((block) => {
    block.pairs.forEach((pair) => {
      if (pair.dev_only && !devMode) return;
      total += 1;
      if (decisions.has(pair.pair_id)) done += 1;
    });
  });
  $("progress").textContent = `${done} de ${total} revistos`;
}

$("finish").addEventListener("click", async () => {
  timers.forEach((timer) => clearTimeout(timer));
  timers.clear();
  $("finish").textContent = "A guardar…";
  await saveQueue;
  location.href = "/";
});
