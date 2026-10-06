"use strict";
const state = {page: "create", category: "drafts", library: [], jobs: [], busy: false, expanded: new Set()};
const $ = query => document.querySelector(query);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const labels = {TOPIC:"Choosing lesson", BRIEF:"Learning brief", EPISODE_SPEC:"Writing episode", LYRICS:"Writing lyrics", MUSIC_SPEC:"Music direction", CREATIVE:"Creative Director", MUSIC:"Generating song", AUDIO_ANALYSIS:"Checking audio", VISUAL_PLAN:"Planning visuals", VISUAL_ASSETS:"Generating images", STORYBOARD:"Building storyboard", RENDER:"Encoding video", MEDIA_QA:"Checking video", METADATA:"Writing metadata", RELEASE:"Checking release gates", YOUTUBE:"Publishing to YouTube"};
const targets = {draft:"Generating Draft", render:"Generating Draft + Render", publish:"Generating Draft + Render + Publish"};
const terminal = job => !["queued", "running"].includes(job.status);
function notice(text) { $("#notice").textContent = text; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Studio could not complete this action.");
  return data;
}
function page(name) {
  if (!["create", "library", "system"].includes(name)) name = "create";
  state.page = name;
  document.querySelectorAll(".page").forEach(item => item.hidden = item.id !== `page-${name}`);
  document.querySelectorAll("[data-page]").forEach(button => button.setAttribute("aria-current", button.dataset.page === name ? "page" : "false"));
  if (name === "library") refreshLibrary();
  if (name === "system") refreshSystem();
}
function jobCard(job) {
  const done = ["complete", "succeeded"].includes(job.status);
  const activeIndex = Math.min(job.completed_steps, Math.max(0, job.steps.length - 1));
  const timeline = job.steps.map((step, index) => `<li class="${index < job.completed_steps ? "done" : index === activeIndex && !done ? "current" : ""}">${index < job.completed_steps ? "✓" : index === activeIndex && !done ? (terminal(job) && job.status !== "pending_provider" ? "✕" : "●") : "○"} ${esc(labels[step] || step)}</li>`).join("");
  const substage = (job.current_substage || "").replace(/_(RUNNING|COMPLETE)$/, "");
  const current = labels[substage] || (substage && !["RUNNING","COMPLETE"].includes(substage) ? substage : labels[job.current_stage]) || job.progress;
  const details = {"Status":job.status, "Stage":job.current_stage, "Substage":job.current_substage, "Started":job.started_at, "Execution":job.execution, "Provider":job.blocker?.provider, "Model":job.blocker?.model, "Request":job.blocker?.request_id, "Episode":job.episode_key};
  const recovery = job.recovery_action === "abandon_remote_result" ? "Continue with next model" : job.recovery_action === "resume_music_task" ? "Resume retained ACE-Step task" : "Retry / Resume";
  return `<article class="task-card" data-job="${esc(job.job_id)}"><div class="task-head"><div><h3>${esc(targets[job.target] || job.operation.replaceAll("_", " "))}</h3><span class="task-meta">${esc(job.stopped ? "Stopped · saved work is in the Library" : job.status.replaceAll("_", " "))}</span></div><span class="percent">${job.progress_percent}%</span></div><div class="progress-track" role="progressbar" aria-label="Whole task progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress_percent}"><div class="progress-fill" style="width:${job.progress_percent}%"></div></div><ul class="timeline">${timeline}</ul>${!terminal(job) ? `<p class="current-message">${esc(current || "Waiting for production")}</p>` : ""}${job.error && !job.stopped ? `<div class="error-card" role="alert"><h4>✕ ${esc(labels[job.current_stage] || "Production paused")}</h4><p>${esc(job.error)}</p><div class="task-buttons">${job.recovery_action ? `<button class="button" data-recover="${esc(job.job_id)}" ${state.busy ? "disabled" : ""}>${recovery}</button>` : ""}<button class="button quiet" data-stop="${esc(job.job_id)}">Stop</button></div></div>` : ""}${done && job.target ? `<div class="complete-result"><p>✓ ${job.target === "draft" ? "Your draft is saved and ready to render." : job.target === "render" ? "Your video is ready to watch or publish." : "Production complete. Find your result in the Library."}</p><button class="button secondary" data-go-library="${job.target === "draft" ? "drafts" : job.target === "render" ? "renders" : "published"}">Open Library ↗</button></div>` : done ? `<p>✓ ${job.operation === "youtube_connect" ? "YouTube channel connected and verified." : "Action completed."}</p>` : ""}<details class="diagnostics"${state.expanded.has(job.job_id) ? " open" : ""}><summary>Technical details</summary><dl>${Object.entries(details).filter(([,value]) => value != null).map(([key,value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl></details></article>`;
}
function renderJobs() {
  state.expanded = new Set(Array.from(document.querySelectorAll(".task-card:has(details[open])")).map(card => card.dataset.job));
  const active = state.jobs.find(job => !terminal(job));
  state.busy = Boolean(active);
  document.querySelectorAll("[data-create]").forEach(button => button.disabled = state.busy);
  $("#active-task").innerHTML = active ? jobCard(active) : `<div class="empty-desk"><span>♫</span><div><h3>Ready for the next little lesson.</h3><p>Choose an action above. You can come back to saved drafts and videos anytime.</p></div></div>`;
  $("#task-history").innerHTML = state.jobs.filter(job => terminal(job) && !job.stopped).slice(0, 5).map(jobCard).join("");
}
async function refreshJobs() {
  try {
    const previous = state.busy;
    state.jobs = await api("/api/jobs");
    renderJobs();
    if (previous && !state.busy && state.page === "library") refreshLibrary();
    if (state.page === "library") renderLibrary();
  } catch (error) { notice(error.message); }
}
function renderLibrary() {
  for (const category of ["drafts", "renders", "published"]) {
    $(`#count-${category}`).textContent = state.library.filter(item => item.category === category).length;
    document.querySelector(`[data-category="${category}"]`).classList.toggle("selected", state.category === category);
    document.querySelector(`[data-category="${category}"]`).setAttribute("aria-pressed", String(state.category === category));
  }
  const items = state.library.filter(item => item.category === state.category || (state.category === "drafts" && item.category === "attention"));
  $("#library-cards").innerHTML = items.length ? items.map(item => `<article class="library-card"><div class="card-art">${item.preview_url ? `<img src="${esc(item.preview_url)}" alt="Illustration for ${esc(item.title)}" loading="lazy">` : item.category === "drafts" ? "♪" : item.category === "renders" ? "▷" : "✦"}</div><div class="card-copy"><span class="badge">${esc(item.historical ? "Historical · immutable" : item.category === "attention" ? "Needs attention" : item.category)}</span><h2>${esc(item.title)}</h2><p>${esc(new Date(item.created_at).toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"}))}${item.category === "published" ? ` · ${esc(item.publication.privacy_status || "published")}` : ""}</p>${item.blocker ? `<p>${esc(item.blocker)}</p>` : ""}<div class="task-buttons">${item.draft ? `<button class="button secondary" data-view-draft="${esc(item.episode_key)}">View Draft</button>` : ""}${item.video_url ? `<button class="button secondary" data-watch="${esc(item.episode_key)}">Watch</button>` : ""}${item.can_render ? `<button class="button" data-continue="${esc(item.episode_key)}" data-target="render" ${state.busy ? "disabled" : ""}>Render ↗</button>` : ""}${item.can_publish ? `<button class="button" data-continue="${esc(item.episode_key)}" data-target="publish" ${state.busy ? "disabled" : ""}>Publish ↗</button>` : ""}</div>${item.publication.watch_url ? `<p><a href="${esc(item.publication.watch_url)}" target="_blank" rel="noopener noreferrer">View on YouTube ↗</a></p>` : ""}</div></article>`).join("") : `<div class="library-empty">No ${esc(state.category)} yet. Your saved work will appear here.</div>`;
}
async function refreshLibrary() { try { state.library = await api("/api/studio/library"); renderLibrary(); } catch (error) { notice(error.message); } }
async function refreshSystem() {
  try {
    const data = await api("/api/system");
    const names = {creative_director:"NVIDIA Creative Director",ace_step:"ACE-Step",comfyui:"Qwen / ComfyUI",comfyui_environment:"ComfyUI environments",ollama:"Ollama",ollama_embedding:"Ollama embeddings",ffmpeg:"FFmpeg / FFprobe",youtube:"YouTube",database:"Database",tovi_pack:"Tovi character pack"};
    $("#system-cards").innerHTML = Object.entries(data.services).map(([name,service]) => `<article class="system-card"><h2>${esc(names[name] || name)}</h2><span class="status ${esc(service.status)}">${esc(service.status)}</span>${service.message ? `<p>${esc(service.message)}</p>` : ""}${["ace_step","comfyui","comfyui_environment","ollama","ollama_embedding"].includes(name) && ["failed","unavailable"].includes(service.status) ? `<button class="button secondary" data-service="${name}">Retry startup</button>` : ""}</article>`).join("");
    $("#system-details").innerHTML = [["Database",data.database_path],["Brand revision",data.brand_revision],["Curriculum revision",data.curriculum_revision],["Configured channel",data.expected_channel_id || "Not configured"]].map(([key,value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("");
    $("#youtube-connect").disabled = state.busy || !data.youtube_enabled;
  } catch (error) { notice(error.message); }
}
function preview(key, video) {
  const item = state.library.find(entry => entry.episode_key === key);
  if (!item) return;
  const draft = item.draft;
  $("#preview-content").innerHTML = `<h2>${esc(item.title)}</h2>${video && item.video_url ? `<video src="${esc(item.video_url)}" controls preload="metadata"></video>` : draft ? `<h3>The lesson</h3><p>${esc(draft.premise)}</p><h3>The hook</h3><p>${esc(draft.hook)}</p><h3>Lyrics</h3>${draft.lyrics.map(line => `<p>${esc(line)}</p>`).join("")}<h3>Music direction</h3><p>${esc(draft.music_direction)}</p>` : ""}`;
  $("#preview").showModal();
}
async function action(button, path, body = {}) {
  button.disabled = true;
  notice("");
  try { await api(path, body); location.hash = "create"; page("create"); await refreshJobs(); }
  catch (error) { notice(error.message); }
  finally { button.disabled = false; renderJobs(); }
}
document.addEventListener("click", async event => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.page) { location.hash = button.dataset.page; page(button.dataset.page); }
  if (button.dataset.category) { state.category = button.dataset.category; renderLibrary(); }
  if (button.dataset.create) await action(button,"/api/studio/create",{target:button.dataset.create});
  if (button.dataset.continue) await action(button,`/api/studio/episodes/${encodeURIComponent(button.dataset.continue)}/continue`,{target:button.dataset.target});
  if (button.dataset.recover) await action(button,`/api/studio/jobs/${encodeURIComponent(button.dataset.recover)}/recover`);
  if (button.dataset.stop) await action(button,`/api/studio/jobs/${encodeURIComponent(button.dataset.stop)}/stop`);
  if (button.dataset.goLibrary) { state.category = button.dataset.goLibrary; location.hash = "library"; page("library"); }
  if (button.dataset.viewDraft) preview(button.dataset.viewDraft,false);
  if (button.dataset.watch) preview(button.dataset.watch,true);
  if (button.dataset.service) {
    try { await api(`/api/system/services/${button.dataset.service}/retry`,{}); await refreshSystem(); }
    catch(error) { notice(error.message); }
  }
});
$("#close-preview").addEventListener("click",() => $("#preview").close());
$("#preview").addEventListener("close",() => { $("#preview-content").innerHTML = ""; });
$("#system-refresh").addEventListener("click",refreshSystem);
$("#youtube-connect").addEventListener("click", async event => { await action(event.target,"/api/youtube/connect"); });
window.addEventListener("hashchange",() => page(location.hash.slice(1)));
refreshJobs();
page(location.hash.slice(1) || "create");
setInterval(async () => { await refreshJobs(); if (state.page === "system") await refreshSystem(); },2500);
