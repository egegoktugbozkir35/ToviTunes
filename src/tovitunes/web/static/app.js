"use strict";
// Visual shell and helpers adapted from ollama-mpt-youtube, commit 1b82232.
const state = {page: "generate", jobs: [], videos: [], system: null, busy: false, expanded: new Set(), outcomes: new Set(), lastJobSignature: "", submitting: false};
const $ = query => document.querySelector(query);
const els = {toast: $("#toast")};
let toastTimer = null;
const pageMeta = {dashboard:["Production overview","Dashboard"], generate:["Production control","Generate videos"], videos:["Persisted content","Videos"], settings:["Local configuration","Settings"]};
const labels = {TOPIC:"Choosing lesson", BRIEF:"Learning brief", EPISODE_SPEC:"Writing episode", LYRICS:"Writing lyrics", MUSIC_SPEC:"Music direction", CREATIVE:"Creative Director", MUSIC:"Generating song", AUDIO_ANALYSIS:"Checking audio", VISUAL_PLAN:"Planning visuals", VISUAL_ASSETS:"Generating images", STORYBOARD:"Building storyboard", RENDER:"Encoding video", MEDIA_QA:"Checking video", METADATA:"Writing metadata", RELEASE:"Checking release gates", YOUTUBE:"Publishing to YouTube"};
const targets = {draft:"Generate Draft", render:"Generate + Render", publish:"Generate, Render & Publish"};
const terminal = job => !["queued", "running"].includes(job.status);
const successful = job => ["complete", "succeeded"].includes(job.status);
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function titleCase(value) {
  return String(value ?? "unknown").replaceAll("_", " ").replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "—"
    : new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
}

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle("error", isError);
  els.toast.hidden = false;
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4500);
}

async function api(path, options = {}) {
  const request = { ...options, headers: { ...(options.headers || {}) } };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = payload?.detail;
    let message = typeof detail === "string" ? detail : `Studio action failed (${response.status})`;
    if (Array.isArray(detail)) {
      message = "Check the selected Studio action.";
    }
    throw new Error(message);
  }
  return payload;
}

function navigate(viewName) {
  if (!pageMeta[viewName]) viewName = "generate";
  state.page = viewName;
  document.querySelectorAll(".view").forEach(view => { view.hidden = view.dataset.page !== viewName; });
  document.querySelectorAll(".nav-item").forEach(button => {
    const active = button.dataset.view === viewName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  const [kicker, title] = pageMeta[viewName];
  $("#page-kicker").textContent = kicker;
  $("#page-title").textContent = title;
  if (location.hash !== `#${viewName}`) location.hash = viewName;
  if (viewName === "dashboard" || viewName === "videos") refreshVideos().catch(error => showToast(error.message, true));
  if (viewName === "settings") refreshSystem().catch(error => showToast(error.message, true));
}

function safeMedia(value) { return /^\/api\/media\/[a-zA-Z0-9_-]+$/.test(value || "") ? value : null; }
function safeWatch(value) { return /^https:\/\/www\.youtube\.com\/watch\?v=[a-zA-Z0-9_-]+$/.test(value || "") ? value : null; }
function updateActionAvailability() {
  document.querySelectorAll("[data-create], [data-continue], [data-recover], #youtube-connect").forEach(button => {
    button.disabled = state.busy || state.submitting;
  });
  if (state.system && !state.system.youtube_enabled) $("#youtube-connect").disabled = true;
}

function jobCard(job, current = false) {
  const done = successful(job);
  const steps = job.steps || [];
  const percent = Math.max(0, Math.min(100, Number(job.progress_percent) || 0));
  const activeIndex = Math.min(job.completed_steps || 0, Math.max(0, steps.length - 1));
  const substage = (job.current_substage || "").replace(/_(RUNNING|COMPLETE)$/, "");
  const detail = labels[substage] || labels[job.current_stage] || "Waiting for production";
  const diagnostics = job.creative_diagnostics;
  const attempts = diagnostics?.attempts || [];
  const recovery = job.recovery_action === "resume_music_task" ? "Resume retained ACE-Step task" : "Retry / Resume";
  const status = job.stopped ? "stopped" : job.status;
  const knownStatus = ["running", "queued", "failed", "ambiguous", "interrupted", "pending_provider", "needs_review", "complete", "succeeded"].includes(status) ? status : "neutral";
  const error = diagnostics?.message || job.error;
  const timeline = current ? `<ol class="job-steps">${steps.map((step, index) => `<li class="${index < job.completed_steps ? "done" : index === activeIndex && !done ? "current" : ""}">${index < job.completed_steps ? "✓" : index === activeIndex && !done ? "●" : "○"} ${escapeHtml(labels[step] || step)}</li>`).join("")}</ol>` : "";
  const showDiagnostic = !job.stopped && (error || attempts.length);
  return `<article class="job-card ${current ? "current" : ""} ${done ? "succeeded" : knownStatus}" data-job="${escapeHtml(job.job_id)}">
    <header><strong>${escapeHtml(targets[job.target] || titleCase(job.operation))}</strong><span class="badge ${knownStatus}">${escapeHtml(titleCase(status))}</span></header>
    <p class="job-meta">${escapeHtml(formatDate(job.started_at || job.submitted_at))} · Execution ${Number(job.execution) || 1}</p>
    ${current ? `<div class="job-progress-summary"><div><div class="job-stage">${done ? "Production complete" : escapeHtml(detail)}</div><p class="job-detail">${done ? "Saved in Videos" : "Work is retained as each stage completes"}</p></div><strong class="job-percent">${percent}%</strong></div><div class="job-progress-track" role="progressbar" aria-label="Whole production progress" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><span class="job-progress-fill" style="width:${percent}%"></span></div>${timeline}` : ""}
    ${showDiagnostic && current ? `<div class="typed-diagnostic" role="status"><strong>${diagnostics?.exhausted ? "Creative model chain exhausted" : job.current_stage === "CREATIVE" && job.error ? "Creative model failed" : diagnostics?.message && !terminal(job) ? "Creative model fallback" : "Production diagnostics"}</strong>${error ? `<p>${escapeHtml(error)}</p>` : ""}${attempts.length ? `<details class="model-diagnostics" ${diagnostics?.exhausted || state.outcomes.has(job.job_id) ? "open" : ""}><summary>Per-model outcomes</summary><ol class="model-outcomes">${attempts.map(item => `<li>${escapeHtml(item.message)}${item.reconciled ? "<br><small>Remote outcome stays ambiguous · operator reconciliation recorded</small>" : ""}<br><small>Fallback ${Number(item.fallback_index) || 0}${item.fallback_reason ? ` · ${escapeHtml(titleCase(item.fallback_reason))}` : ""}</small></li>`).join("")}</ol></details>` : ""}</div>` : ""}
    ${job.error && !current ? `<p class="job-error">${escapeHtml(job.error)}</p>` : ""}
    <div class="job-actions">${done && job.target ? `<button class="button" data-navigate="videos">Open Videos →</button>` : ""}${terminal(job) && !done && !job.stopped ? `${job.recovery_action ? `<button class="button solid" data-recover="${escapeHtml(job.job_id)}">${recovery}</button>` : ""}<button class="button" data-stop="${escapeHtml(job.job_id)}">Stop</button>` : ""}</div>
    ${current ? `<details class="job-technical" ${state.expanded.has(job.job_id) ? "open" : ""}><summary>Task details</summary><dl class="system-list">${[["Stage",labels[job.current_stage] || job.current_stage], ["Substage",labels[substage] || substage], ["Task",job.job_id], ["Episode",job.episode_key]].filter(([,value]) => value).map(([label,value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl></details>` : ""}
  </article>`;
}

function renderJobs() {
  state.expanded = new Set(Array.from(document.querySelectorAll(".job-card:has(.job-technical[open])")).map(card => card.dataset.job));
  state.outcomes = new Set(Array.from(document.querySelectorAll(".job-card:has(.model-diagnostics[open])")).map(card => card.dataset.job));
  const visible = state.jobs.filter(job => !job.stopped);
  const active = visible.find(job => !terminal(job));
  const headline = active || visible[0];
  state.busy = Boolean(active);
  $("#current-job").className = headline ? "" : "empty-state";
  $("#current-job").innerHTML = headline ? jobCard(headline, true) : `<span class="empty-glyph" aria-hidden="true">◌</span><strong>No active job</strong><p>Start a run to see its queue and completion state here.</p>`;
  $("#job-history").innerHTML = visible.filter(job => job !== headline).slice(0, 3).map(job => jobCard(job)).join("");
  updateActionAvailability();
}

async function refreshJobs() {
  const wasBusy = state.busy;
  const jobs = await api("/api/jobs");
  // Read the current durable task through its established single-job endpoint.
  const headline = jobs.find(job => !terminal(job)) || jobs.find(job => !job.stopped);
  if (headline) {
    const durable = await api(`/api/jobs/${encodeURIComponent(headline.job_id)}`);
    state.jobs = jobs.map(job => job.job_id === durable.job_id ? durable : job);
  } else state.jobs = jobs;
  const signature = JSON.stringify(state.jobs);
  if (signature !== state.lastJobSignature) { state.lastJobSignature = signature; renderJobs(); }
  else updateActionAvailability();
  if (wasBusy && !state.busy) await refreshVideos();
  if (state.page === "videos") renderVideos();
  if (state.page === "dashboard") renderDashboard();
}

function itemActions(item) {
  const key = escapeHtml(item.episode_key);
  return `${item.draft ? `<button class="button" data-draft="${key}">View Draft</button>` : ""}${safeMedia(item.video_url) ? `<button class="button" data-watch="${key}">Watch</button>` : ""}${item.can_render ? `<button class="button" data-continue="${key}" data-target="render">Render</button>` : ""}${item.can_publish ? `<button class="button" data-continue="${key}" data-target="publish">Publish</button>` : ""}`;
}

function renderVideos() {
  const filter = $("#video-filter").value;
  const items = state.videos.filter(item => !filter || item.category === filter);
  $("#videos-empty").hidden = Boolean(items.length);
  $("#videos-body").innerHTML = items.map(item => `<tr><td>${safeMedia(item.preview_url) ? `<img class="video-thumb" src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.title)}" loading="lazy">` : ""}<strong>${escapeHtml(item.title)}</strong><small>${item.historical ? "Historical · retained" : "ToviTunes episode"}</small></td><td>${item.draft_ready ? "Creative draft" : item.render_ready ? "Video" : "Saved work"}</td><td><span class="badge ${item.category === "published" ? "published" : item.category === "renders" ? "rendered" : item.category === "attention" ? "failed" : "brief_ready"}">${escapeHtml(titleCase(item.category))}</span></td><td>${escapeHtml(titleCase(item.publication?.privacy_status || item.publication?.outcome || "not published"))}</td><td>${escapeHtml(formatDate(item.created_at))}</td><td><div class="video-actions">${itemActions(item)}</div>${item.blocker ? `<p>${escapeHtml(item.blocker)}</p>` : ""}</td></tr>`).join("");
  updateActionAvailability();
}

function renderDashboard() {
  const metrics = [["Drafts", state.videos.filter(v => v.category === "drafts").length], ["Rendered",state.videos.filter(v => v.category === "renders").length], ["Published",state.videos.filter(v => v.category === "published").length], ["Needs attention",state.videos.filter(v => v.category === "attention").length], ["Active jobs",state.jobs.filter(j => !terminal(j)).length]];
  $("#dashboard-metrics").innerHTML = metrics.map(([label,value]) => `<article class="metric-card"><span>${label}</span><strong>${value}</strong><small>Saved Studio evidence</small></article>`).join("");
  $("#dashboard-activity").innerHTML = state.videos.length ? state.videos.slice(0, 8).map(item => `<div class="activity-row"><div><strong>${escapeHtml(item.title)}</strong><small>${item.historical ? "Historical production" : "ToviTunes"}</small></div><span>${escapeHtml(formatDate(item.created_at))}</span><span class="badge">${escapeHtml(titleCase(item.category))}</span><button class="text-button" data-navigate="videos">View →</button></div>`).join("") : `<div class="empty-state"><span class="empty-glyph">□</span><strong>Your studio is ready for its first lesson</strong><p>Generate a draft to start your saved library.</p></div>`;
}

async function refreshVideos() { state.videos = await api("/api/studio/library"); renderVideos(); renderDashboard(); }
function setReadiness(element, text, ready, failed = false) { element.innerHTML = `<span class="mini-dot ${ready ? "ready" : failed ? "error" : ""}"></span>${escapeHtml(text)}`; }
async function refreshSystem() {
  const data = state.system = await api("/api/system");
  const creative = data.services?.creative_director?.status === "ready";
  const youtube = data.services?.youtube?.status === "connected";
  $("#sidebar-status").textContent = "Pipeline ready";
  $("#sidebar-signal").className = "signal-dot ready";
  $("#creative-pill").textContent = creative ? "Creative ready" : "Creative setup needed";
  $("#creative-pill").className = `status-pill ${creative ? "ready" : "error"}`;
  $("#youtube-pill").textContent = youtube ? "YouTube connected" : "YouTube disconnected";
  $("#youtube-pill").className = `status-pill ${youtube ? "ready" : "neutral"}`;
  setReadiness($("#api-readiness"), "Ready", true);
  setReadiness($("#creative-readiness"), creative ? "Ready" : "Check Settings", creative, !creative);
  setReadiness($("#youtube-readiness"), youtube ? "Connected" : "Connect in Settings", youtube);
  $("#publish-note").textContent = youtube ? "Publication follows your configured review and release policy." : "Connect YouTube in Settings before publishing. Drafts and renders can run independently.";
  const names = {creative_director:"NVIDIA Creative Director", ace_step:"ACE-Step", comfyui:"Qwen / ComfyUI", comfyui_environment:"ComfyUI environments", ollama:"Ollama", ollama_embedding:"Ollama embeddings", ffmpeg:"FFmpeg / FFprobe", youtube:"YouTube", database:"Database", tovi_pack:"Tovi character pack"};
  $("#service-list").innerHTML = Object.entries(data.services || {}).map(([name, service]) => `<article class="service-row"><header><h3>${escapeHtml(names[name] || titleCase(name))}</h3><span class="status-pill ${service.status === "ready" || service.status === "connected" ? "ready" : "neutral"}">${escapeHtml(titleCase(service.status))}</span></header>${service.message ? `<p>${escapeHtml(service.message)}</p>` : ""}${["ace_step","comfyui","comfyui_environment","ollama","ollama_embedding"].includes(name) && ["failed","unavailable"].includes(service.status) ? `<button class="button" data-service="${name}">Retry startup</button>` : ""}</article>`).join("");
  $("#system-list").innerHTML = [["Creative planning","Open editorial planning"], ["Database",data.database_path], ["Brand revision",data.brand_revision], ["Curriculum revision",data.curriculum_revision], ["Configured channel",data.expected_channel_id || "Not configured"], ["OAuth files",`Client credentials ${data.youtube_credentials_present ? "present" : "not found"} · Token ${data.youtube_token_present ? "present" : "not found"}`]].map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  updateActionAvailability();
}

function preview(key, watch = false) {
  const item = state.videos.find(video => video.episode_key === key);
  if (!item) return;
  $("#detail-title").textContent = item.title;
  const draft = item.draft;
  const video = safeMedia(item.video_url);
  const youtube = safeWatch(item.publication?.watch_url);
  $("#detail-content").innerHTML = `${watch && video ? `<video class="detail-video" src="${escapeHtml(video)}" controls preload="metadata"></video>` : draft ? `<div class="detail-grid"><section class="detail-block"><h3>The lesson</h3><p>${escapeHtml(draft.premise)}</p></section><section class="detail-block"><h3>The hook</h3><p>${escapeHtml(draft.hook)}</p></section><section class="detail-block full"><h3>Lyrics</h3><p>${escapeHtml((draft.lyrics || []).join("\n"))}</p></section><section class="detail-block full"><h3>Music direction</h3><p>${escapeHtml(draft.music_direction)}</p></section></div>` : ""}<div class="job-actions">${itemActions(item)}${youtube ? `<a href="${escapeHtml(youtube)}" target="_blank" rel="noopener noreferrer">View on YouTube ↗</a>` : ""}</div>`;
  updateActionAvailability();
  if (!$("#video-dialog").open) $("#video-dialog").showModal();
}

async function action(button, path, body = {}) {
  if (state.submitting) return;
  state.submitting = true;
  updateActionAvailability();
  button.disabled = true;
  try {
    await api(path, {method:"POST", body});
    if ($("#video-dialog").open) $("#video-dialog").close();
    navigate("generate");
    await refreshJobs();
  } catch (error) { showToast(error.message, true); }
  finally { state.submitting = false; button.disabled = false; updateActionAvailability(); }
}

document.addEventListener("click", async event => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  if (button.dataset.view || button.dataset.navigate) navigate(button.dataset.view || button.dataset.navigate);
  if (button.dataset.create) await action(button,"/api/studio/create",{target:button.dataset.create});
  if (button.dataset.continue) await action(button,`/api/studio/episodes/${encodeURIComponent(button.dataset.continue)}/continue`,{target:button.dataset.target});
  if (button.dataset.recover) await action(button,`/api/studio/jobs/${encodeURIComponent(button.dataset.recover)}/recover`);
  if (button.dataset.stop) await action(button,`/api/studio/jobs/${encodeURIComponent(button.dataset.stop)}/stop`);
  if (button.dataset.draft) preview(button.dataset.draft);
  if (button.dataset.watch) preview(button.dataset.watch, true);
  if (button.dataset.service) {
    button.disabled = true;
    try { await api(`/api/system/services/${button.dataset.service}/retry`, {method:"POST", body:{}}); await refreshSystem(); }
    catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  }
});
$("#generate-form").addEventListener("submit", event => event.preventDefault());
$("#close-dialog").addEventListener("click", () => $("#video-dialog").close());
$("#video-dialog").addEventListener("close", () => { $("#detail-content").innerHTML = ""; });
$("#video-filter").addEventListener("change", renderVideos);
$("#system-refresh").addEventListener("click", () => refreshSystem().catch(error => showToast(error.message, true)));
$("#youtube-connect").addEventListener("click", event => action(event.target,"/api/youtube/connect"));
window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
navigate(location.hash.slice(1) || "generate");
Promise.all([refreshJobs(),refreshVideos(),refreshSystem()]).catch(error => showToast(error.message, true));
let polling = false;
setInterval(async () => {
  if (polling) return;
  polling = true;
  try { await refreshJobs(); if (state.page === "settings") await refreshSystem(); }
  catch (error) { $("#sidebar-status").textContent = "Connection interrupted"; $("#sidebar-signal").className = "signal-dot error"; }
  finally { polling = false; }
}, 2500);
