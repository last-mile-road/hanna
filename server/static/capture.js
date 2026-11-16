/* Phone route: front and back, client gate, admission, extraction, HANNA number.
   No dependencies. The gate thresholds are the same as Bench screen 1. */

const BLUR_MIN = 250;
const BRIGHT_MIN = 70;
const BRIGHT_MAX = 215;
const GLARE_MAX = 0.02;
const CAPTURE_LONG_EDGE = 3332;

const $ = (id) => document.getElementById(id);
const show = (id) => {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $(id).classList.remove("hidden");
  window.scrollTo(0, 0);
};

let stream = null;
let track = null;
let currentSide = "front";
const shots = { front: null, back: null };
let cameraInfo = {};
let captureBody = null;
let runStates = {};

/* ---------- flow ---------- */

$("consent").addEventListener("change", (e) => { $("consent-next").disabled = !e.target.checked; });
$("consent-next").addEventListener("click", () => show("view-country"));
$("country-next").addEventListener("click", () => {
  currentSide = "front";
  setSideLabel();
  show("view-capture");
  startCamera();
});

if (/FBAN|FBAV|Instagram|Line\/|WhatsApp/i.test(navigator.userAgent)) {
  $("file-note").textContent = "Está a abrir dentro de outra aplicação. A câmara pode não funcionar: abra o link no Chrome ou Safari.";
  $("file-note").classList.remove("hidden");
}

/* ---------- camera ---------- */

async function startCamera() {
  if (stream) {
    $("video").srcObject = stream;
    $("video").classList.remove("hidden");
    $("placeholder").classList.add("hidden");
    $("btn-start").classList.add("hidden");
    $("btn-shot").classList.remove("hidden");
    return;
  }
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
  track = stream.getVideoTracks()[0];
  const video = $("video");
  video.srcObject = stream;
  video.classList.remove("hidden");
  $("placeholder").classList.add("hidden");
  $("btn-start").classList.add("hidden");
  $("btn-shot").classList.remove("hidden");

  const caps = track.getCapabilities ? track.getCapabilities() : {};
  if (caps.torch) $("btn-torch").classList.remove("hidden");
  try {
    if (caps.focusMode && caps.focusMode.includes("continuous")) {
      await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
    }
  } catch { /* the camera chooses its own focus */ }
  cameraInfo = describeCamera(caps);

  // The previous HANNA client switched the light on for the first shot.
  if (caps.torch) {
    try {
      await track.applyConstraints({ advanced: [{ torch: true }] });
      $("btn-torch").textContent = "Luz ligada";
    } catch { /* not supported at runtime */ }
  }
}

$("btn-start").addEventListener("click", startCamera);

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
    track = null;
  }
  $("video").srcObject = null;
  $("video").classList.add("hidden");
  $("btn-torch").classList.add("hidden");
  $("btn-torch").textContent = "Luz da câmara";
}

window.addEventListener("pagehide", stopCamera);

$("btn-torch").addEventListener("click", async () => {
  if (!track || !track.getCapabilities || !track.getCapabilities().torch) return;
  const on = $("btn-torch").textContent.includes("ligada");
  try {
    await track.applyConstraints({ advanced: [{ torch: !on }] });
    $("btn-torch").textContent = !on ? "Luz ligada" : "Luz desligada";
  } catch {
    $("side-hint").textContent = "Este telefone não permite controlar a luz da câmara.";
  }
});

$("video").addEventListener("click", async (event) => {
  if (!track || !track.getCapabilities) return;
  const caps = track.getCapabilities();
  const modes = caps.focusMode || [];
  const dot = $("focus-dot");
  const rect = $("stage").getBoundingClientRect();
  dot.style.left = `${event.clientX - rect.left}px`;
  dot.style.top = `${event.clientY - rect.top}px`;
  dot.classList.remove("hidden");
  setTimeout(() => dot.classList.add("hidden"), 700);
  if (modes.includes("single-shot") || modes.includes("manual")) {
    try {
      await track.applyConstraints({ advanced: [{ focusMode: "single-shot" }] });
      $("side-hint").textContent = "Foco ajustado no ponto tocado.";
    } catch {
      $("side-hint").textContent = "O telefone ajusta o foco automaticamente.";
    }
  } else {
    $("side-hint").textContent = "O telefone ajusta o foco automaticamente.";
  }
});

function describeCamera(caps) {
  const settings = track && track.getSettings ? track.getSettings() : {};
  const connection = navigator.connection || {};
  return {
    user_agent: navigator.userAgent,
    platform: navigator.platform,
    language: navigator.language,
    screen: { width: screen.width, height: screen.height, dpr: window.devicePixelRatio },
    network: {
      effective_type: connection.effectiveType,
      downlink: connection.downlink,
      rtt: connection.rtt,
      save_data: connection.saveData,
    },
    camera: {
      width: settings.width,
      height: settings.height,
      frame_rate: settings.frameRate,
      facing_mode: settings.facingMode,
      torch: Boolean(caps.torch),
      focus_mode: caps.focusMode || [],
      zoom: caps.zoom || null,
    },
  };
}

/* ---------- taking a shot ---------- */

$("btn-shot").addEventListener("click", () => {
  const video = $("video");
  if (!video.videoWidth) return;
  const scale = Math.min(1, CAPTURE_LONG_EDGE / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  acceptCanvas(canvas, currentSide);
});

$("file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, CAPTURE_LONG_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  acceptCanvas(canvas, currentSide);
});

function acceptCanvas(canvas, side) {
  canvas.toBlob((blob) => {
    const gate = runGate(canvas);
    shots[side] = { canvas, blob, gate };
    $("preview").src = URL.createObjectURL(blob);
    $("preview").classList.remove("hidden");
    $("video").classList.add("hidden");
    $("btn-shot").classList.add("hidden");
    $("btn-retake").classList.remove("hidden");
    $("size-note").textContent = `Fotografia: ${Math.round(blob.size / 1024)} KB, ${canvas.width}×${canvas.height} px`;
    renderGate(gate);
    if (!gate.ok) {
      $("btn-continue").classList.add("hidden");
      $("btn-shot").classList.remove("hidden");
      return;
    }
    if (shots.front && shots.back) {
      $("btn-continue").textContent = "Rever as duas fotografias";
    } else if (side === "front") {
      $("btn-continue").textContent = "Fotografar o verso";
    } else {
      $("btn-continue").textContent = "Rever as duas fotografias";
    }
    $("btn-continue").classList.remove("hidden");
  }, "image/jpeg", 0.9);
}

$("btn-retake").addEventListener("click", () => retake(currentSide));

$("btn-continue").addEventListener("click", () => {
  if (shots.front && shots.back) {
    toReview();
    return;
  }
  if (currentSide === "front" && shots.front) {
    currentSide = "back";
    setSideLabel();
    resetStage();
    return;
  }
  toReview();
});

function retake(side) {
  shots[side] = null;
  currentSide = side;
  setSideLabel();
  show("view-capture");
  resetStage();
  if (!stream || !track || track.readyState !== "live") {
    stream = null;
    track = null;
    startCamera();
  } else {
    $("video").srcObject = stream;
    $("video").play().catch(() => {});
  }
  window.scrollTo(0, 0);
}

function setSideLabel() {
  $("side-label").textContent = currentSide === "front" ? "Frente" : "Verso";
  $("side-hint").textContent = currentSide === "front"
    ? "Coloque a frente do cartão no guia, com luz uniforme."
    : "Vire o cartão. Coloque o verso no guia, com luz uniforme.";
}

function resetStage() {
  $("preview").classList.add("hidden");
  $("gate").classList.add("hidden");
  $("size-note").textContent = "";
  $("btn-continue").classList.add("hidden");
  $("btn-retake").classList.add("hidden");
  if (stream) {
    $("video").classList.remove("hidden");
    $("placeholder").classList.add("hidden");
    $("btn-start").classList.add("hidden");
    $("btn-shot").classList.remove("hidden");
  } else {
    $("video").classList.add("hidden");
    $("placeholder").classList.remove("hidden");
    $("btn-start").classList.remove("hidden");
    $("btn-shot").classList.add("hidden");
  }
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
  if (brightness < BRIGHT_MIN) reasons.push("Pouca luz. Aproxime-se de uma janela ou ligue a luz da câmara.");
  if (brightness > BRIGHT_MAX) reasons.push("Demasiada luz: parte do cartão está estourada.");
  if (glare > GLARE_MAX) reasons.push("Reflexo no cartão. Mude o ângulo ou desligue a luz da câmara.");
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

/* ---------- review both sides ---------- */

function toReview() {
  if (!shots.front || !shots.back) return;
  $("thumb-front").src = URL.createObjectURL(shots.front.blob);
  $("thumb-back").src = URL.createObjectURL(shots.back.blob);
  $("send-note").textContent = "";
  $("btn-send").disabled = false;
  show("view-review");
}

document.querySelectorAll("[data-retake]").forEach((button) => {
  button.addEventListener("click", () => retake(button.dataset.retake));
});

$("btn-send").addEventListener("click", sendCapture);

async function sendCapture() {
  if (!shots.front || !shots.back) return;
  $("btn-send").disabled = true;
  $("btn-send").textContent = "A enviar…";
  const form = new FormData();
  form.append("files", shots.front.blob, "frente.jpg");
  form.append("files", shots.back.blob, "verso.jpg");
  form.append("country", $("country").value);
  form.append("attested_dummy", "true");
  form.append("client", JSON.stringify(cameraInfo));

  let response;
  try {
    response = await fetch("/api/captures", { method: "POST", body: form });
  } catch {
    $("btn-send").disabled = false;
    $("btn-send").textContent = "Tentar enviar de novo";
    $("send-note").textContent = "A ligação falhou durante o envio. Verifique a rede.";
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    $("btn-send").disabled = false;
    $("btn-send").textContent = "Tentar enviar de novo";
    $("send-note").textContent = body.detail || "O servidor recusou a fotografia.";
    return;
  }
  if (body.status === "retake") {
    showAdmission(body);
    return;
  }
  stopCamera();
  captureBody = body;
  showAdmission(body);
  setTimeout(() => startProgress(body), 700);
}

function showAdmission(body) {
  show("view-admission");
  const admitted = body.status === "admitted";
  $("admission-title").textContent = admitted ? "Captura aceite" : "Repetir a fotografia";
  $("admission-message").textContent = body.message || "";
  const box = $("admission-pages");
  box.innerHTML = "";
  (body.pages || []).forEach((page) => {
    const div = document.createElement("div");
    div.className = "issue" + (page.admission === "good" ? " info" : "");
    const reasons = (page.reasons || []).join(" ");
    div.innerHTML = `<strong>${page.label || page.side || "página"}${page.rotation ? ` · ${page.rotation}°` : ""}</strong><span>${reasons || (page.admission === "good" ? "Aceite." : "")}</span>`;
    box.appendChild(div);
  });
  const needsRetake = !admitted;
  $("btn-retake-front").classList.toggle("hidden", !needsRetake);
  $("btn-retake-back").classList.toggle("hidden", !needsRetake);
  $("btn-retake-front").onclick = () => retake("front");
  $("btn-retake-back").onclick = () => retake("back");
}

/* ---------- extraction progress ---------- */

function startProgress(body) {
  show("view-progress");
  runStates = {};
  const wrap = $("run-panels");
  wrap.innerHTML = "";
  if (!body.runs || !body.runs.length) {
    wrap.innerHTML = `<div class="run-panel"><p class="muted">A leitura não iniciou. Tente novamente a partir da captura.</p></div>`;
    return;
  }
  body.runs.forEach((run) => {
    const panel = document.createElement("div");
    panel.className = "run-panel";
    panel.id = `run-${run.side}`;
    panel.innerHTML = `
      <h2><span>${run.side === "front" ? "Frente" : "Verso"}</span><span class="badge review" data-status>Em fila</span></h2>
      <p class="muted" data-detail>0 blocos</p>
      <div class="run-review hidden" data-review>
        <span class="badge review" data-review-badge>Por rever</span>
        <a class="btn-review" data-link href="#" target="_blank" rel="noopener">Rever valores</a>
      </div>`;
    wrap.appendChild(panel);
    watchRun(run, panel);
  });
  $("finalise").classList.add("hidden");
  $("case-decision").classList.add("hidden");
  $("decision-message").textContent = "";
  $("number-note").textContent = "";
}

function watchRun(run, panel) {
  let gotAny = false;
  const statusEl = panel.querySelector("[data-status]");
  const detailEl = panel.querySelector("[data-detail]");
  let done = 0;
  const es = new EventSource(run.events_url);

  const fallback = setTimeout(() => {
    if (gotAny) return;
    es.close();
    pollRun(run, panel);
  }, 3500);

  const finish = (data) => {
    clearTimeout(fallback);
    es.close();
    const validation = data.validation_status || (data.status === "failed" ? "failed" : "review");
    statusEl.textContent = validation;
    statusEl.className = `badge ${validation}`;
    const total = data.blocks_done ? `${data.blocks_done} blocos` : "leitura terminada";
    detailEl.textContent = `${total}${data.error ? " · " + data.error : ""}`;
    runStates[run.side] = data;
    revealReview(panel, data);
    maybeDecide();
  };

  es.addEventListener("status", (e) => {
    gotAny = true;
    const data = JSON.parse(e.data);
    if (data.status === "queued") { statusEl.textContent = "Em fila"; statusEl.className = "badge review"; }
    if (data.status === "running") { statusEl.textContent = "A ler"; statusEl.className = "badge review"; }
    detailEl.textContent = `${data.blocks_done || 0} blocos`;
  });
  es.addEventListener("block", () => {
    gotAny = true;
    done += 1;
    detailEl.textContent = `${done} blocos`;
  });
  es.addEventListener("verdict", (e) => {
    gotAny = true;
    finish(JSON.parse(e.data));
  });
  es.onerror = () => {
    if (!gotAny) { es.close(); pollRun(run, panel); }
  };
}

/* The review link is the next step after the reading, so it is a full-width
   button, and its badge says whether the side has been reviewed yet. */
function revealReview(panel, data) {
  const wrap = panel.querySelector("[data-review]");
  if (!wrap) return;
  wrap.classList.remove("hidden");
  const finished = Boolean(data.review && data.review.finished);
  const badge = wrap.querySelector("[data-review-badge]");
  const link = wrap.querySelector("[data-link]");
  const armed = link.getAttribute("href") && link.getAttribute("href") !== "#";
  link.classList.toggle("waiting", !armed);
  wrap.classList.toggle("confirmed", finished);
  if (finished) {
    badge.textContent = "Confirmado";
    badge.className = "badge ok";
    link.textContent = "Ver valores";
    link.classList.add("secondary-link");
  } else {
    const done = data.review ? `${data.review.reviewed} de ${data.review.total} campos` : "";
    badge.textContent = done ? `Por rever · ${done}` : "Por rever";
    badge.className = "badge review";
    link.textContent = armed ? "Rever valores" : "A preparar a revisão…";
    link.classList.remove("secondary-link");
  }
}

async function pollRun(run, panel) {
  const statusEl = panel.querySelector("[data-status]");
  const detailEl = panel.querySelector("[data-detail]");
  let misses = 0;
  for (;;) {
    let data;
    try {
      const response = await fetch(`/api/runs/${run.run_id}`, { cache: "no-store" });
      if (!response.ok) throw new Error("poll");
      data = await response.json();
      misses = 0;
    } catch {
      // A phone on mobile data loses the connection for a few seconds at a
      // time. Keep polling: the run carries on on the server. Give up only
      // after five minutes without an answer.
      misses += 1;
      detailEl.textContent =
        misses < 15
          ? "A reconectar…"
          : "Sem ligação ao servidor. A tentar de novo…";
      if (misses >= 150) {
        detailEl.textContent =
          "Sem ligação ao servidor. Verifique a rede e recarregue a página.";
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
      continue;
    }
    const done = data.blocks_done || 0;
    detailEl.textContent = data.status === "queued" ? "Em fila" : `${done} blocos`;
    if (["done", "partial", "failed"].includes(data.status)) {
      const validation = data.validation_status || (data.status === "failed" ? "failed" : "review");
      statusEl.textContent = validation;
      statusEl.className = `badge ${validation}`;
      runStates[run.side] = data;
      revealReview(panel, data);
      maybeDecide();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

function hannaDisplay(number) {
  const digits = String(number || "").replace(/\D/g, "");
  if (digits.length !== 9) return digits;
  return `${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6)}`;
}

async function maybeDecide() {
  if (!captureBody) return;
  if (Object.keys(runStates).length !== (captureBody.runs || []).length) return;
  let verdict;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await fetch(`/api/cases/${captureBody.case_id}/decision`, { cache: "no-store" });
      if (!response.ok) throw new Error("decision");
      verdict = await response.json();
      break;
    } catch {
      if (attempt >= 5) {
        $("case-decision").classList.remove("hidden");
        $("decision-message").textContent = "A decisão do caso não está disponível. Recarregue a página.";
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  $("case-decision").classList.remove("hidden");
  (captureBody.runs || []).forEach((run) => {
    const panel = document.getElementById(`run-${run.side}`);
    if (!panel) return;
    const other = (captureBody.runs || []).find((item) => item.side !== run.side);
    const link = panel.querySelector("[data-link]");
    if (!verdict.review_ready) {
      link.removeAttribute("href");
      return;
    }
    link.href = `/review?run=${run.run_id}&from=capture${
      other ? `&other=${other.run_id}&otherside=${other.side}` : ""
    }`;
    if (runStates[run.side]) revealReview(panel, runStates[run.side]);
  });
  const identity = verdict.identity || {};
  const head = [];
  if (identity.number_read) {
    head.push(
      `Número HANNA lido no cartão: <strong>${hannaDisplay(identity.number_read)}</strong>` +
        (identity.number_valid === false ? " · dígito de controlo inválido" : "")
    );
  }
  const messages = (verdict.feedback || []).map((item) => item.message).filter(Boolean);

  if (verdict.already_closed) {
    // The card carries a number we already have and the case is closed. Nothing
    // new is added; the answer is the number it already has.
    (captureBody.runs || []).forEach((run) => {
      const panel = document.getElementById(`run-${run.side}`);
      const review = panel && panel.querySelector("[data-review]");
      if (review) review.classList.add("hidden");
    });
    const existing = identity.hanna_number;
    if (existing) {
      head.push(
        `Caso já notificado. Número HANNA: <strong>${hannaDisplay(existing)}</strong>`
      );
    }
    $("decision-message").innerHTML = head.concat(messages).join("<br>");
    $("finalise").classList.add("hidden");
    return;
  }

  // Finalising is the last step: the number is only offered once both sides
  // have been looked at. Until then the review buttons are the next action.
  const reviewed = (captureBody.runs || []).every(
    (run) => runStates[run.side] && runStates[run.side].review && runStates[run.side].review.finished
  );
  if (verdict.can_allocate && !reviewed) {
    head.push("Reveja a frente e o verso para poder finalizar.");
  }
  $("decision-message").innerHTML = head.concat(messages).join("<br>");
  if (verdict.can_allocate && reviewed) $("finalise").classList.remove("hidden");
  else $("finalise").classList.add("hidden");
  if (verdict.can_allocate && !reviewed) watchReviews();
}

/* The review opens in another tab. Poll the two runs until both are reviewed,
   so the Finalizar button appears on this screen without a reload. */
let reviewWatch = null;
function watchReviews() {
  if (reviewWatch) return;
  reviewWatch = setInterval(async () => {
    let finished = true;
    for (const run of captureBody.runs || []) {
      try {
        const response = await fetch(`/api/runs/${run.run_id}`, { cache: "no-store" });
        if (!response.ok) throw new Error("poll");
        const data = await response.json();
        runStates[run.side] = data;
        const panel = document.getElementById(`run-${run.side}`);
        if (panel) revealReview(panel, data);
        if (!(data.review && data.review.finished)) finished = false;
      } catch {
        finished = false;
      }
    }
    maybeDecide();
    if (finished) {
      clearInterval(reviewWatch);
      reviewWatch = null;
    }
  }, 4000);
}

$("btn-finalise").addEventListener("click", finalise);

async function finalise() {
  if (!captureBody) return;
  $("btn-finalise").disabled = true;
  $("number-note").textContent = "A finalizar…";
  let response;
  try {
    response = await fetch(`/api/cases/${captureBody.case_id}/allocate`, { method: "POST" });
  } catch {
    $("btn-finalise").disabled = false;
    $("number-note").textContent = "A ligação falhou. Tente novamente.";
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    $("btn-finalise").disabled = false;
    $("number-note").textContent = body.detail || "Ainda não é possível atribuir o número.";
    return;
  }
  $("number-note").innerHTML = `Número HANNA: <strong>${body.hanna_number_display}</strong><br><span class="hint">Escreva-o no cartão, na frente, no canto superior esquerdo, acima do bloco NIL. Depois digitalize outra vez: o número é lido daí.</span>`;
  $("btn-finalise").classList.add("hidden");
}

/* ---------- network hint ---------- */

function netHint() {
  const c = navigator.connection || {};
  if (!c.effectiveType) return;
  $("net").textContent = `Rede: ${c.effectiveType}${c.downlink ? " · ~" + c.downlink + " Mb/s" : ""}`;
}
netHint();
