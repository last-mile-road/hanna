/* Bench screen 1 — consent, capture, client gate, upload, live stream.
   No dependencies. Every threshold here comes from docs/phase-0-measurements.md. */

const BLUR_MIN = 250;      // Laplacian variance on a 600 px thumbnail; blurred cards sit near 75
const BRIGHT_MIN = 70;     // below this the card is under-lit (dark median was 53)
const BRIGHT_MAX = 215;    // above this it is blown out (overexposed median was 233)
const GLARE_MAX = 0.02;    // share of saturated pixels (a bare reflection measured 0.067)
const CAPTURE_LONG_EDGE = 3332;  // the template's scale; more pixels add upload, not detail

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

const $ = (id) => document.getElementById(id);
const show = (id) => { document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden")); $(id).classList.remove("hidden"); };

let stream = null;
let shotBlob = null;
let shotCanvas = null;
let gateResult = null;
let eventSource = null;
let runId = null;

/* ---------- flow ---------- */

$("consent").addEventListener("change", (e) => { $("consent-next").disabled = !e.target.checked; });
$("consent-next").addEventListener("click", () => show("view-country"));
$("country-next").addEventListener("click", () => { show("view-capture"); startCamera(); });
$("btn-again").addEventListener("click", () => { resetCapture(); show("view-capture"); });
$("btn-done").addEventListener("click", () => { resetAll(); show("view-country"); });

if (/FBAN|FBAV|Instagram|Line\/|WhatsApp/i.test(navigator.userAgent)) {
  $("file-note").textContent = "Está a abrir dentro de outra aplicação. A câmara pode não funcionar: abra o link no Chrome ou Safari.";
  $("file-note").classList.remove("hidden");
}

/* ---------- camera ---------- */

async function startCamera() {
  $("placeholder").textContent = "A abrir a câmara…";
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } },
      audio: false,
    });
  } catch (err) {
    $("placeholder").textContent = "Não foi possível abrir a câmara. Verifique a permissão ou use um ficheiro.";
    $("file-note").textContent = String(err && err.name === "NotAllowedError"
      ? "Permissão da câmara negada. Autorize no navegador, ou escolha um ficheiro."
      : "Câmara indisponível neste navegador. Escolha um ficheiro como alternativa.");
    $("file-note").classList.remove("hidden");
    return;
  }
  const video = $("video");
  video.srcObject = stream;
  video.classList.remove("hidden");
  $("placeholder").classList.add("hidden");
  $("btn-start").classList.add("hidden");
  $("btn-shot").classList.remove("hidden");
}

$("btn-start").addEventListener("click", startCamera);

$("btn-shot").addEventListener("click", () => {
  const video = $("video");
  if (!video.videoWidth) return;
  const scale = Math.min(1, CAPTURE_LONG_EDGE / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  shotCanvas = canvas;

  canvas.toBlob((blob) => {
    shotBlob = blob;
    $("preview").src = URL.createObjectURL(blob);
    $("preview").classList.remove("hidden");
    $("video").classList.add("hidden");
    $("btn-shot").classList.add("hidden");
    $("btn-retake").classList.remove("hidden");
    $("size-note").textContent = `Fotografia: ${Math.round(blob.size / 1024)} KB, ${canvas.width}×${canvas.height} px`;
    gateResult = runGate(canvas);
    renderGate(gateResult);
    if (gateResult.ok) {
      $("btn-upload").classList.remove("hidden");
      $("btn-upload").textContent = "Enviar para análise";
    } else {
      $("btn-upload").classList.add("hidden");
      $("btn-retake").classList.add("hidden");
      $("btn-shot").classList.remove("hidden");
    }
  }, "image/jpeg", 0.9);
});

$("btn-retake").addEventListener("click", resetCapture);

function resetCapture() {
  shotBlob = null; shotCanvas = null; gateResult = null;
  $("btn-upload").disabled = false;
  $("btn-upload").textContent = "Enviar para análise";
  $("preview").classList.add("hidden");
  $("gate").classList.add("hidden");
  $("btn-upload").classList.add("hidden");
  $("btn-retake").classList.add("hidden");
  $("size-note").textContent = "";
  if (stream) { $("video").classList.remove("hidden"); $("btn-shot").classList.remove("hidden"); }
  else { $("placeholder").classList.remove("hidden"); $("btn-start").classList.remove("hidden"); }
}

function resetAll() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  runId = null;
  resetCapture();
  $("blocks").innerHTML = "";
  $("placeholder").classList.remove("hidden");
  $("btn-start").classList.remove("hidden");
}

/* ---------- the client gate ---------- */

function runGate(canvas) {
  const thumb = document.createElement("canvas");
  const ratio = 600 / Math.max(canvas.width, canvas.height);
  thumb.width = Math.round(canvas.width * ratio);
  thumb.height = Math.round(canvas.height * ratio);
  const ctx = thumb.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, thumb.width, thumb.height);
  const { data, width, height } = ctx.getImageData(0, 0, thumb.width, thumb.height);

  const gray = new Float32Array(width * height);
  let bright = 0;
  let saturated = 0;
  for (let i = 0, p = 0; i < data.length; i += 4, p += 1) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    gray[p] = 0.299 * r + 0.587 * g + 0.114 * b;
    bright += gray[p];
    if (r >= 250 && g >= 250 && b >= 250) saturated += 1;
  }
  const brightness = bright / (width * height);
  const glare = saturated / (width * height);

  // 4-neighbour Laplacian, the same kernel cv2.Laplacian uses by default.
  let sum = 0, sumSq = 0, n = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x;
      const v = gray[i - 1] + gray[i + 1] + gray[i - width] + gray[i + width] - 4 * gray[i];
      sum += v; sumSq += v * v; n += 1;
    }
  }
  const mean = sum / n;
  const blur = sumSq / n - mean * mean;

  const reasons = [];
  if (blur < BLUR_MIN) reasons.push("A fotografia está desfocada. Segure o telefone firme e toque no ecrã para focar.");
  if (brightness < BRIGHT_MIN) reasons.push("Pouca luz. Aproxime-se de uma janela ou ligue a luz.");
  if (brightness > BRIGHT_MAX) reasons.push("Demasiada luz: parte do cartão está estourada.");
  if (glare > GLARE_MAX) reasons.push("Reflexo no cartão. Mude o ângulo para evitar o brilho do papel.");
  return { blur, brightness, glare, ok: reasons.length === 0, reasons };
}

function mark(el, ok, warn) {
  el.className = ok ? "ok" : (warn ? "warn" : "bad");
  el.textContent = ok ? "OK" : (warn ? "Atenção" : "Falhou");
}

function renderGate(result) {
  $("gate").classList.remove("hidden");
  mark($("g-blur"), result.blur >= BLUR_MIN, result.blur >= BLUR_MIN * 0.7);
  mark($("g-bright"), result.brightness >= BRIGHT_MIN && result.brightness <= BRIGHT_MAX,
       result.brightness >= BRIGHT_MIN * 0.8 && result.brightness <= BRIGHT_MAX * 1.05);
  mark($("g-glare"), result.glare <= GLARE_MAX, result.glare <= GLARE_MAX * 2);
  $("gate-reason").textContent = result.reasons.join(" ");
}

/* ---------- upload and stream ---------- */

$("btn-upload").addEventListener("click", upload);

async function upload() {
  if (!shotBlob) return;
  $("btn-upload").disabled = true;
  $("btn-upload").textContent = "A enviar…";
  const form = new FormData();
  form.append("file", shotBlob, "cartao.jpg");
  form.append("country", $("country").value);
  form.append("attested_dummy", "true");

  let response;
  try {
    response = await fetch("/api/runs", { method: "POST", body: form });
  } catch (err) {
    $("btn-upload").disabled = false;
    $("btn-upload").textContent = "Tentar enviar de novo";
    $("size-note").textContent = "A ligação falhou durante o envio. Verifique a rede.";
    return;
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    $("btn-upload").disabled = false;
    $("btn-upload").textContent = "Tentar enviar de novo";
    $("size-note").textContent = body.detail || "O servidor recusou a fotografia.";
    return;
  }

  const started = performance.now();
  const data = await response.json();
  runId = data.run_id;
  show("view-progress");
  $("progress-status").textContent = "Em fila…";
  renderBlocks({});
  openStream(runId, started);
}

function renderBlocks(done) {
  const container = $("blocks");
  container.innerHTML = "";
  Object.entries(BLOCK_LABELS).forEach(([id, label]) => {
    const div = document.createElement("div");
    div.className = "block" + (done[id] ? " done" : "");
    div.textContent = label;
    div.dataset.block = id;
    container.appendChild(div);
  });
}

function openStream(id, started) {
  eventSource = new EventSource(`/api/runs/${id}/events`);
  const done = {};
  let gotAny = false;

  // Fallback: some proxies (the Cloudflare quick tunnel does) buffer text/event-stream
  // and deliver everything at the end. If nothing arrives shortly, poll instead. SSE stays
  // the primary path because on a direct connection it is one request instead of thirty.
  const fallback = setTimeout(() => {
    if (gotAny) return;
    if (eventSource) { eventSource.close(); eventSource = null; }
    pollRun(id, started, done);
  }, 3500);

  eventSource.addEventListener("status", (e) => {
    gotAny = true;
    const data = JSON.parse(e.data);
    if (data.status === "queued") $("progress-status").textContent = "Em fila…";
    if (data.status === "running") $("progress-status").textContent = `A ler… ${data.blocks_done} de 8 blocos`;
  });

  eventSource.addEventListener("block", (e) => {
    gotAny = true;
    const data = JSON.parse(e.data);
    done[data.block_id] = true;
    const el = document.querySelector(`[data-block="${data.block_id}"]`);
    if (el) { el.classList.add("done"); }
    $("progress-status").textContent = `A ler… ${Object.keys(done).length} de 8 blocos`;
  });

  eventSource.addEventListener("verdict", (e) => {
    gotAny = true;
    clearTimeout(fallback);
    const data = JSON.parse(e.data);
    eventSource.close();
    showVerdict(data, started);
  });

  eventSource.onerror = () => {
    if (eventSource) { eventSource.close(); eventSource = null; }
    if (!gotAny) { pollRun(id, started, done); return; }
    $("progress-status").textContent = "A ligação caiu. O resultado pode estar pronto; recarregue ou fotografe de novo.";
  };
}

async function pollRun(id, started, done) {
  $("progress-status").textContent = "A ler o cartão…";
  for (;;) {
    let data;
    try {
      const response = await fetch(`/api/runs/${id}`, { cache: "no-store" });
      if (!response.ok) throw new Error("poll");
      data = await response.json();
    } catch (err) {
      $("progress-status").textContent = "Sem ligação ao servidor. Tente novamente.";
      return;
    }
    (data.blocks || []).forEach((block) => {
      if (done[block.block_id]) return;
      done[block.block_id] = true;
      const el = document.querySelector(`[data-block="${block.block_id}"]`);
      if (el) el.classList.add("done");
    });
    const count = Object.keys(done).length;
    if (data.status === "queued") $("progress-status").textContent = "Em fila…";
    else if (count < 8) $("progress-status").textContent = `A ler… ${count} de 8 blocos`;
    if (["done", "partial", "failed"].includes(data.status)) { showVerdict(data, started); return; }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

function showVerdict(data, started) {
  show("view-verdict");
  const seconds = ((performance.now() - started) / 1000).toFixed(0);
  const status = data.validation_status || (data.status === "failed" ? "failed" : "review");
  const labels = {
    blocked: ["Corrija o cartão", "O cartão tem dados obrigatórios em falta ou inválidos. Corrija o cartão e fotografe de novo."],
    review: ["Rever", "Os valores foram lidos. Reveja os campos assinalados antes de submeter."],
    ok: ["Aceite", "Nenhum problema obrigatório encontrado."],
    failed: ["Falhou", "O servidor não conseguiu ler este cartão."],
  };
  const [title, sub] = labels[status] || labels.review;
  $("verdict-title").textContent = title;
  $("verdict-sub").innerHTML = `<span class="badge ${status}">${status}</span> ${sub}`;

  // Only a blocked run needs a new photograph. A review run is a recorded card with notes.
  const blocked = status === "blocked" || status === "failed";
  $("btn-again").textContent = blocked ? "Corrigir o cartão e fotografar de novo" : "Repetir a fotografia";
  $("btn-done").textContent = blocked ? "Terminar" : "Continuar";
  $("btn-again").classList.toggle("secondary", !blocked);
  $("btn-done").classList.toggle("secondary", blocked);

  const issues = (data.validation && data.validation.issues) || [];
  const box = $("verdict-issues");
  box.innerHTML = "";
  if (!issues.length) {
    box.innerHTML = `<p class="hint">${data.error ? data.error : "Sem problemas registados."}</p>`;
  }
  const groups = [
    ["Impede a submissão", issues.filter((i) => i.severity === "blocking")],
    ["Notas — não impedem", issues.filter((i) => i.severity && i.severity !== "blocking")],
  ];
  groups.forEach(([heading, list]) => {
    if (!list.length) return;
    const h = document.createElement("p");
    h.className = "hint";
    h.textContent = heading;
    box.appendChild(h);
    list.forEach((issue) => {
      const div = document.createElement("div");
      div.className = "issue" + (issue.severity === "info" ? " info" : "");
      const advice = issue.advice ? `<span>${issue.advice}</span>` : "";
      div.innerHTML = `<strong>${issue.block_id || issue.pair_id || "problema"}</strong>${advice}`;
      box.appendChild(div);
    });
  });

  $("verdict-meta").textContent =
    `${data.blocks_done} blocos · ${seconds} s · ${data.cost_usd ? "$" + Number(data.cost_usd).toFixed(4) : "custo n/d"}`;
}

/* ---------- network hint ---------- */

function netHint() {
  const c = navigator.connection || {};
  if (!c.effectiveType) return;
  $("net").textContent = `Rede: ${c.effectiveType}${c.downlink ? " · ~" + c.downlink + " Mb/s" : ""}`;
}
netHint();
