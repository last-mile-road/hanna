/* Review screen. The value is an input where it sits, and everything you touch is saved
   as you go — no Save button to forget. Tables mirror the printed card. */

const $ = (id) => document.getElementById(id);
const runId = new URLSearchParams(location.search).get("run");
const from = new URLSearchParams(location.search).get("from");

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
  // The location card keeps developer detail (score, service, reason), so it must be
  // rebuilt when the toggle moves rather than only restyled.
  const current = $("sections").querySelector(".location");
  if (current && data) current.replaceWith(renderLocation() || document.createElement("div"));
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

/* ---------- location card ---------- */

const LEVEL_LABEL = {
  facility: "unidade sanitária",
  locality: "localidade",
  administrative_post: "posto administrativo",
  district: "distrito",
  province: "província",
  service: "serviço",
};

const LEVEL_ORDER = ["province", "district", "administrative_post", "locality", "facility"];

/* The matched level plus one level up, which is the context a clinician needs to judge it:
   "Balama › Impiri" for a facility match, "Cabo Delgado › Balama" for a district one. */
function contextNames(chain, level) {
  if (!chain) return { parent: null, matched: null };
  const index = Math.max(0, LEVEL_ORDER.indexOf(level));
  let matched = null;
  let parent = null;
  for (let i = Math.min(index, LEVEL_ORDER.length - 1); i >= 0; i -= 1) {
    if (chain[LEVEL_ORDER[i]]) {
      matched = { level: LEVEL_ORDER[i], name: chain[LEVEL_ORDER[i]] };
      break;
    }
  }
  const from = matched ? LEVEL_ORDER.indexOf(matched.level) - 1 : index - 1;
  for (let i = from; i >= 0; i -= 1) {
    if (chain[LEVEL_ORDER[i]]) {
      parent = { level: LEVEL_ORDER[i], name: chain[LEVEL_ORDER[i]] };
      break;
    }
  }
  return { parent, matched };
}

function precisionText(metres) {
  if (!metres) return "";
  return metres >= 1000 ? `~${(metres / 1000).toFixed(0)} km de margem`
                        : `~${metres} m de margem`;
}

async function postLocation(payload) {
  const response = await fetch(`/api/runs/${runId}/location`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    flash("Não foi possível guardar a localização");
    return null;
  }
  return response.json();
}

/* Interactive picker, loaded only when the clinician says the pin is wrong. The static
   PNG stays the default so a weak connection still shows where the pin is. */
function openPicker(location, onSaved) {
  if (!window.L) {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.onload = () => openPicker(location, onSaved);
    document.head.appendChild(script);
    return;
  }

  const overlay = document.createElement("div");
  overlay.className = "picker";
  const head = document.createElement("div");
  head.className = "picker-head";
  const title = document.createElement("strong");
  title.textContent = "Arraste o alfinete até à casa do doente";
  const cancel = document.createElement("button");
  cancel.className = "link-button";
  cancel.textContent = "Cancelar";
  cancel.addEventListener("click", () => overlay.remove());
  head.append(title, cancel);

  const mapElement = document.createElement("div");
  mapElement.className = "picker-map";
  const footer = document.createElement("div");
  footer.className = "picker-foot";
  const hint = document.createElement("span");
  hint.className = "hint";
  hint.textContent = "Aproxime com dois dedos. O alfinete fica na casa.";
  const save = document.createElement("button");
  save.className = "mini accept";
  save.textContent = "Guardar";
  save.addEventListener("click", async () => {
    const point = marker.getLatLng();
    const result = await postLocation({
      mode: "chosen", lat: point.lat, lon: point.lng, name: "ajustado no mapa",
    });
    if (result) {
      overlay.remove();
      onSaved(point.lat, point.lng);
    }
  });
  footer.append(hint, save);
  overlay.append(head, mapElement, footer);
  document.body.appendChild(overlay);

  const zoom = location.level === "facility" || location.level === "locality" ? 16 : 13;
  const map = L.map(mapElement).setView([location.lat, location.lon], zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 19,
  }).addTo(map);
  const marker = L.marker([location.lat, location.lon], { draggable: true }).addTo(map);
  setTimeout(() => map.invalidateSize(), 50);
}

function renderLocation() {
  const loc = data.location;
  if (!loc || data.show_location === false) return null;

  const section = document.createElement("section");
  section.className = "section location";

  const head = document.createElement("div");
  head.className = "section-head";
  const title = document.createElement("h2");
  title.textContent = "Onde fica a casa do doente?";
  head.append(title);
  if (loc.review?.mode === "accepted" || loc.review?.mode === "chosen") {
    const done = document.createElement("span");
    done.className = "loc-badge ok";
    done.textContent = "confirmado";
    head.append(done);
  }
  section.appendChild(head);

  const context = contextNames(loc.chosen?.chain || {}, loc.level);
  const chain = document.createElement("p");
  chain.className = "chain";
  chain.textContent = [context.parent?.name, context.matched?.name].filter(Boolean).join(" › ")
                      || "sem correspondência no gazetteer";
  section.appendChild(chain);

  const hasPoint = loc.lat !== null && loc.lat !== undefined
    && loc.lon !== null && loc.lon !== undefined;
  if (hasPoint) {
    const map = document.createElement("img");
    map.className = "locmap";
    map.alt = "Mapa da localização";
    const point = `${loc.review?.lat ?? loc.lat},${loc.review?.lon ?? loc.lon}`;
    map.src = `/api/runs/${runId}/map.png?v=${point}`;
    section.appendChild(map);
  }

  const instruction = document.createElement("p");
  instruction.className = "instruction";
  instruction.textContent = loc.level === "district" || loc.level === "province"
    ? "Só sabemos o distrito. Coloque o alfinete na casa ou, no mínimo, na aldeia do doente."
    : "Confirme se o alfinete está na casa do doente. Se não estiver, coloque-o na casa ou, no mínimo, na aldeia.";
  section.appendChild(instruction);

  const meta = document.createElement("p");
  meta.className = "locmeta";
  meta.textContent = [
    loc.review?.mode === "chosen"
      ? "posição indicada pelo revisor"
      : (context.matched ? (LEVEL_LABEL[context.matched.level] || context.matched.level) : ""),
    loc.review?.mode === "chosen" ? "" : precisionText(loc.precision_m),
    devMode && loc.score ? `confiança ${(loc.score / 100).toFixed(2)}` : "",
    devMode && loc.service_used ? "serviço" : "",
  ].filter(Boolean).join(" · ");
  section.appendChild(meta);

  const actions = document.createElement("div");
  actions.className = "loc-actions";
  const confirmed = loc.review?.mode === "accepted" || loc.review?.mode === "chosen";
  const currentPoint = {
    ...loc,
    lat: loc.review?.lat ?? loc.lat,
    lon: loc.review?.lon ?? loc.lon,
  };
  // The map is never a one-way door: a clinician can come back, look again and move the
  // pin after confirming, because changing one's mind on the way out is normal.
  const openMap = () => openPicker(currentPoint, (lat, lon) => {
    loc.review = { mode: "chosen", lat, lon, changed: true };
    loc.lat = lat;
    loc.lon = lon;
    section.replaceWith(renderLocation());
  });

  if (hasPoint && !confirmed) {
    const accept = document.createElement("button");
    accept.className = "mini accept";
    accept.textContent = "Sim, é aqui";
    accept.addEventListener("click", async () => {
      const result = await postLocation({ mode: "accepted" });
      if (result) {
        loc.review = result.review;
        section.replaceWith(renderLocation());
      }
    });
    const correct = document.createElement("button");
    correct.className = "mini";
    correct.textContent = "Abrir mapa e mover o alfinete";
    correct.addEventListener("click", openMap);
    actions.append(accept, correct);
  } else if (!hasPoint) {
    const pick = document.createElement("button");
    pick.className = "mini accept";
    pick.textContent = "Marcar no mapa";
    pick.addEventListener("click", () => {
      const fallback = { ...loc, lat: -13.0, lon: 38.0 };
      openPicker(fallback, (lat, lon) => {
        loc.review = { mode: "chosen", lat, lon };
        loc.lat = lat;
        loc.lon = lon;
        section.replaceWith(renderLocation());
      });
    });
    actions.append(pick);
  } else {
    const done = document.createElement("span");
    done.className = "hint";
    done.textContent = loc.review?.mode === "chosen"
      ? "posição corrigida pelo revisor" : "confirmado pelo revisor";
    const again = document.createElement("button");
    again.className = "mini";
    again.textContent = "Abrir mapa e mover o alfinete";
    again.addEventListener("click", openMap);
    actions.append(done, again);
  }
  section.appendChild(actions);

  if (devMode) {
    const reason = document.createElement("p");
    reason.className = "hint";
    reason.textContent = loc.reason || "";
    section.appendChild(reason);
  }
  return section;
}

/* ---------- render ---------- */

function render() {
  const container = $("sections");
  container.innerHTML = "";
  const location = renderLocation();
  if (location) container.appendChild(location);
  data.blocks.forEach((block) => {
    const section = document.createElement("section");
    section.className = "section";

    const head = document.createElement("div");
    head.className = "section-head";
    const title = document.createElement("h2");
    title.textContent = (data.block_titles || {})[block.id] || BLOCK_LABELS[block.id] || block.id;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${block.pairs.length} campos`;
    const reviewed = document.createElement("span");
    reviewed.className = "block-count";
    reviewed.dataset.blockCount = block.id;
    const acceptAll = document.createElement("button");
    acceptAll.className = "mini accept";
    acceptAll.dataset.acceptBlock = block.id;
    acceptAll.textContent = (block.tables || []).length ? "Verificado" : "Tudo correcto";
    head.append(title, count, reviewed, acceptAll);
    section.appendChild(head);

    const inTable = new Set();
    (block.tables || []).forEach((table) => {
      (table.rows || []).forEach((row) => row.cells.forEach((cell) => inTable.add(cell)));
      section.appendChild(renderTable(table, block));
    });
    block.pairs
      .filter((pair) => !inTable.has(pair.pair_id))
      .forEach((pair) => section.appendChild(renderPair(pair)));

    acceptAll.addEventListener("click", () => {
      // A table's "Verificado" covers that table only. The complication flags and the
      // notes in the same block are separate judgements and stay untouched.
      const tables = block.tables || [];
      if (tables.length) {
        const cells = new Set();
        tables.forEach((table) => (table.rows || []).forEach((row) =>
          row.cells.forEach((cell) => cells.add(cell))));
        block.pairs.forEach((pair) => {
          if (cells.has(pair.pair_id)) decide(pair.pair_id, "accepted", null);
        });
        return;
      }
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
  if (pair.pair_id === "treatment_grid") {
    row.appendChild(treatmentGrid(pair));
  } else {
    row.appendChild(control(pair));
  }
  if (pair.issue) {
    const note = document.createElement("div");
    note.className = "issue-note" + (pair.issue.severity === "info" ? " info" : "");
    note.textContent = pair.issue.advice || pair.issue.kind;
    row.appendChild(note);
  }
  return row;
}

function treatmentGrid(pair) {
  let grid = null;
  try { grid = JSON.parse(pair.value || ""); } catch (error) { grid = null; }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  if (!grid || !(grid.rows || []).length) {
    wrap.innerHTML = `<p class="table-note">Sem grelha legível. Valor bruto: ${pair.value || "—"}</p>`;
    return wrap;
  }
  const months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  const table = document.createElement("table");
  table.className = "form-table";
  const head = document.createElement("tr");
  head.appendChild(document.createElement("th"));
  months.forEach((month) => {
    const th = document.createElement("th");
    th.textContent = month;
    head.appendChild(th);
  });
  table.appendChild(head);
  grid.rows.forEach((row) => {
    const tr = document.createElement("tr");
    const label = document.createElement("th");
    label.className = "row-label";
    label.textContent = row.year || "—";
    tr.appendChild(label);
    months.forEach((_, index) => {
      const month = index + 1;
      const td = document.createElement("td");
      const select = document.createElement("select");
      select.className = "month-cell";
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "·";
      select.appendChild(blank);
      for (let day = 1; day <= 31; day += 1) {
        const option = document.createElement("option");
        option.value = String(day);
        option.textContent = String(day);
        select.appendChild(option);
      }
      const missed = document.createElement("option");
      missed.value = "F";
      missed.textContent = "F";
      select.appendChild(missed);

      const dose = (row.doses || []).find((entry) => entry.month === month);
      const skipped = (row.missed_months || []).find((entry) => entry.month === month);
      select.value = dose ? String(dose.day) : (skipped ? "F" : "");
      if (select.value === "F") select.classList.add("missed");

      select.addEventListener("change", () => {
        row.doses = (row.doses || []).filter((entry) => entry.month !== month);
        row.missed_months = (row.missed_months || []).filter((entry) => entry.month !== month);
        if (select.value === "F") row.missed_months.push({ month });
        else if (select.value) row.doses.push({ month, day: Number(select.value) });
        row.doses.sort((a, b) => a.month - b.month);
        row.missed_months.sort((a, b) => a.month - b.month);
        select.classList.toggle("missed", select.value === "F");
        decide(pair.pair_id, "corrected", JSON.stringify(grid));
      });
      td.appendChild(select);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
  wrap.appendChild(table);
  const note = document.createElement("p");
  note.className = "table-note";
  note.textContent = "Grelha de tratamento: escolha o dia da toma em cada mês, ou F se o mês foi falhado.";
  wrap.appendChild(note);
  return wrap;
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
  if (table.note) {
    const note = document.createElement("p");
    note.className = "table-note";
    note.textContent = table.note;
    wrap.appendChild(note);
  }
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
  data.blocks.forEach((block) => {
    let total = 0;
    let accepted = 0;
    block.pairs.forEach((pair) => {
      if (pair.dev_only && !devMode) return;
      total += 1;
      if (decisions.get(pair.pair_id)?.mode === "accepted") accepted += 1;
    });
    const counter = document.querySelector(`[data-block-count="${block.id}"]`);
    if (counter) counter.textContent = `${accepted}/${total}`;
    const button = document.querySelector(`[data-accept-block="${block.id}"]`);
    if (button) button.classList.toggle("done", total > 0 && accepted === total);
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

  // Untouched is not the same as accepted: a value nobody looked at must not count as
  // confirmed in the statistics or the training set. Record the remainder as skipped.
  const untouched = [];
  data.blocks.forEach((block) => {
    block.pairs.forEach((pair) => {
      if (pair.dev_only && !devMode) return;
      if (!decisions.has(pair.pair_id)) untouched.push(pair.pair_id);
    });
  });

  if (untouched.length) {
    const ok = confirm(
      `Faltam ${untouched.length} campos por rever.\n\n` +
      "Continuar regista-os como NÃO revistos (não contam como correctos)."
    );
    if (!ok) return;
    $("finish").textContent = "A guardar…";
    try {
      await fetch(`/api/runs/${runId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reviews: untouched.map((pair_id) => ({ pair_id, mode: "skipped" })),
        }),
      });
    } catch (error) {
      $("finish").textContent = "Terminar";
      return;
    }
  }

  $("finish").textContent = "A guardar…";
  await saveQueue;
  if (from === "capture") {
    // This review was opened from the phone capture tab. Keep the tab open:
    // the other side has its own review link there.
    $("finish").textContent = "Revisão guardada";
    $("finish").disabled = true;
    $("saved").textContent = "Revisão guardada";
    $("truth-note").textContent = "Volte ao separador da captura para rever o outro lado e finalizar o caso.";
    return;
  }
  location.href = "/";
});

/* Save the reviewed values as this card's ground truth. Only confirmed and
   corrected values are exported; skipped and untouched values are not truth. */
$("save-truth").addEventListener("click", async () => {
  if (!runId) return;
  const button = $("save-truth");
  button.disabled = true;
  button.textContent = "A guardar…";
  await saveQueue;
  let response;
  try {
    response = await fetch(`/api/runs/${runId}/ground-truth`, { method: "POST" });
  } catch {
    button.disabled = false;
    button.textContent = "Guardar como cartão de verdade";
    $("truth-note").textContent = "A ligação falhou. Tente novamente.";
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    button.disabled = false;
    button.textContent = "Guardar como cartão de verdade";
    $("truth-note").textContent = body.detail || "Não foi possível guardar.";
    return;
  }
  button.textContent = "Guardado";
  $("truth-note").textContent = `${body.saved} valores guardados como verdade de teste.`;
});
