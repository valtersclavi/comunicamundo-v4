const $ = (selector) => document.querySelector(selector);
const formatter = new Intl.NumberFormat("pt-BR");
const state = { files: [], current: null, timer: null, history: [], pendingStatus: null };

const labels = {
  queued: "Na fila", input_failed: "Falha na entrada", source_structure_ready: "Fontes estruturadas",
  digesting: "Digerindo fontes", understanding_ready: "Interpretação concluída", axis_proposed: "Eixo aguardando aprovação",
  axis_approved: "Eixo aprovado", project_proposed: "Projeto Editorial pronto", project_approved: "Projeto aprovado", failed: "Falha"
};
const stageNames = { ingestion: "Leitura e normalização das fontes", relational_structure: "Estrutura de parágrafos e frases", digestion: "Digestão editorial", narrative_axis: "Construção do Eixo Narrativo", editorial_project: "Construção do Projeto Editorial" };
const expectedStages = Object.keys(stageNames);

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.body && typeof options.body === "string" ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
  if (!response.ok) {
    let message = `Erro ${response.status}`;
    try { const data = await response.json(); message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data); } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function showError(element, error) { element.textContent = error instanceof Error ? error.message : String(error); element.hidden = false; }
function clearError(element) { element.hidden = true; element.textContent = ""; }
function bytes(value) { if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }
function escapeHtml(value) { const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML; }

function renderFiles() {
  $("#file-list").innerHTML = state.files.map((file, index) => `<div class="file-item"><span>PDF</span><strong>${escapeHtml(file.name)}</strong><span>${bytes(file.size)}</span><button type="button" data-remove="${index}" aria-label="Remover ${escapeHtml(file.name)}">×</button></div>`).join("");
}

function addFiles(files) {
  for (const file of files) {
    if ((file.type && file.type !== "application/pdf") || !file.name.toLowerCase().endsWith(".pdf")) { showError($("#form-error"), `“${file.name}” não é um PDF.`); continue; }
    if (file.size > 1024 ** 3) { showError($("#form-error"), `“${file.name}” ultrapassa 1 GB.`); continue; }
    if (!state.files.some((item) => item.name === file.name && item.size === file.size)) state.files.push(file);
  }
  renderFiles();
}

async function uploadFile(file, index, totalFiles) {
  const button = $("#submit-plan");
  button.textContent = `Enviando PDF ${index + 1} de ${totalFiles}…`;
  const start = await api("/v1/uploads", { method: "POST", body: JSON.stringify({ file_name: file.name, content_type: "application/pdf", size_bytes: file.size }) });
  for (let offset = 0; offset < file.size; offset += start.chunk_size) {
    const end = Math.min(offset + start.chunk_size, file.size);
    const progress = Math.round((end / file.size) * 100);
    button.textContent = `Enviando ${file.name} · ${progress}%`;
    await api(`/v1/uploads/${start.upload_id}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream", "Content-Range": `bytes ${offset}-${end - 1}/${file.size}` }, body: file.slice(offset, end) });
  }
  return api(`/v1/uploads/${start.upload_id}/complete`, { method: "POST" });
}

async function submitPlan(event) {
  event.preventDefault(); clearError($("#form-error"));
  const urls = $("#urls").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  if (!state.files.length && !urls.length) return showError($("#form-error"), "Adicione pelo menos um PDF ou uma URL pública.");
  if (state.files.length + urls.length > 64) return showError($("#form-error"), "Use no máximo 64 fontes por planejamento.");
  if (urls.some((url) => !url.startsWith("https://"))) return showError($("#form-error"), "Cada URL precisa começar com https://.");
  const button = $("#submit-plan"); button.disabled = true;
  try {
    const uploaded = [];
    for (let i = 0; i < state.files.length; i++) uploaded.push(await uploadFile(state.files[i], i, state.files.length));
    button.textContent = "Iniciando o Hefesto…";
    const payload = {
      project_id: crypto.randomUUID(),
      brief: { theme: $("#theme").value, approach: $("#approach").value, duration_minutes: Number($("#duration").value), output_language: $("#language").value, audience: $("#audience").value, product: $("#product").value },
      sources: [...uploaded.map((item) => ({ uri: item.uri, title: item.title, language: $("#language").value })), ...urls.map((url) => ({ uri: url, title: null, language: $("#language").value }))]
    };
    const result = await api("/v1/plans", { method: "POST", body: JSON.stringify(payload) });
    await loadHistory(); await openExecution(result.execution_run_id, payload.brief.theme);
  } catch (error) { showError($("#form-error"), error); }
  finally { button.disabled = false; button.textContent = "Criar planejamento"; }
}

function statusClass(status) { return ["failed", "input_failed"].includes(status) ? "failed" : "completed"; }
function renderRun(run) {
  const status = run.status || "queued";
  $("#run-status").textContent = labels[status] || status;
  $("#execution-title").textContent = status === "project_proposed" ? "Projeto Editorial" : status === "axis_proposed" ? "Eixo Narrativo pronto" : "Hefesto em andamento";
  const known = new Map((run.stages || []).map((item) => [item.stage, item]));
  let activeFound = false;
  $("#stages").innerHTML = expectedStages.map((name) => {
    const item = known.get(name); let kind = item ? statusClass(item.status) : "pending";
    if (!item && !activeFound && !["axis_proposed", "project_proposed", "failed", "input_failed"].includes(status)) { kind = "active"; activeFound = true; }
    const duration = item?.duration_ms != null ? `${(item.duration_ms / 1000).toFixed(1)} s` : kind === "active" ? "em andamento" : "";
    return `<div class="stage ${kind}"><i class="stage-icon"></i><strong>${stageNames[name]}</strong><span>${duration}</span></div>`;
  }).join("");
  const usage = run.usage;
  $("#metrics-panel").hidden = !usage;
  if (usage) {
    $("#metric-calls").textContent = formatter.format(usage.call_count || 0);
    $("#metric-input").textContent = formatter.format(usage.prompt_tokens || 0);
    $("#metric-output").textContent = formatter.format(usage.output_tokens || 0);
    $("#metric-total").textContent = formatter.format(usage.total_tokens || 0);
    $("#metric-cost").textContent = `US$ ${Number(usage.rated_cost_usd || 0).toFixed(6)}`;
  }
  const failed = ["failed", "input_failed"].includes(status);
  $("#run-error").hidden = !failed;
  if (failed) $("#run-error").textContent = `${run.error_class || "Falha no processamento"}: ${run.error_message || "Não foi possível concluir esta execução."}`;
  $("#resume-plan").hidden = status !== "failed" || state.pendingStatus === "failed";
  const axis = run.narrative_axis?.proposal;
  $("#axis-panel").hidden = !axis;
  if (axis) { $("#axis-statement").textContent = axis.statement; $("#axis-rationale").textContent = axis.rationale; $("#approve-axis").hidden = status !== "axis_proposed" || state.pendingStatus === "axis_proposed"; }
  const project = run.editorial_project?.proposal;
  $("#project-panel").hidden = !project;
  if (project) renderProject(project);
  $("#approve-project").hidden = status !== "project_proposed" || state.pendingStatus === "project_proposed";
  $("#project-approval-copy").textContent = status === "project_approved" ? "Projeto Editorial aprovado." : "Revise o projeto antes de concluir o planejamento.";
  return status;
}

function renderProject(project) {
  $("#project-title").textContent = project.working_title;
  $("#project-promise").textContent = project.editorial_promise;
  $("#project-duration").textContent = `${project.duration_minutes} minutos`;
  $("#subjects").innerHTML = project.source_supported_subjects.map((item) => `<article class="subject"><div class="subject-head"><h4>${escapeHtml(item.title)}</h4><strong>${item.allocated_minutes} min</strong></div><p>${escapeHtml(item.editorial_role)}</p><p>${escapeHtml(item.depth)}</p></article>`).join("");
  const blocks = [["Sugestões de conhecimento do modelo", project.model_knowledge_suggestions], ["Oportunidades de enriquecer as fontes", project.source_enrichment_opportunities]];
  $("#suggestions").innerHTML = blocks.filter(([, items]) => items?.length).map(([title, items]) => `<section class="suggestion-block"><h3>${title}</h3><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>`).join("");
}

async function poll() {
  if (!state.current) return;
  try {
    const run = await api(`/v1/plans/${state.current}`); const status = renderRun(run);
    const waitingForAction = state.pendingStatus === status;
    if (state.pendingStatus && !waitingForAction) state.pendingStatus = null;
    if (waitingForAction || !["axis_proposed", "project_proposed", "project_approved", "failed", "input_failed"].includes(status)) state.timer = setTimeout(poll, 4000);
    else await loadHistory();
  } catch (error) { showError($("#run-error"), error); }
}

async function openExecution(id, title) {
  clearTimeout(state.timer); state.current = id; state.pendingStatus = null;
  $("#composer").hidden = true; $("#execution").hidden = false;
  $("#execution-subtitle").textContent = title || id;
  document.querySelectorAll(".history-item").forEach((item) => item.classList.toggle("active", item.dataset.id === id));
  await poll(); window.scrollTo({ top: 0, behavior: "smooth" });
}

async function approveAxis() {
  const button = $("#approve-axis"); button.disabled = true; button.textContent = "Aprovando…";
  try { await api(`/v1/plans/${state.current}/approve-axis`, { method: "POST" }); state.pendingStatus = "axis_proposed"; $("#axis-panel").hidden = true; await poll(); }
  catch (error) { state.pendingStatus = null; showError($("#run-error"), error); }
  finally { button.disabled = false; button.textContent = "Aprovar Eixo e continuar"; }
}

async function runAction(action, button, busyLabel, currentStatus) {
  button.disabled = true; const label = button.textContent; button.textContent = busyLabel;
  try { await api(`/v1/plans/${state.current}/${action}`, { method: "POST" }); state.pendingStatus = currentStatus; await poll(); }
  catch (error) { state.pendingStatus = null; showError($("#run-error"), error); }
  finally { button.disabled = false; button.textContent = label; }
}

async function loadHistory() {
  try {
    const data = await api("/v1/plans"); state.history = data.plans || [];
    $("#history").innerHTML = state.history.length ? state.history.map((item) => { const brief = item.request?.brief || {}; return `<button class="history-item${item.execution_run_id === state.current ? " active" : ""}" type="button" data-id="${item.execution_run_id}" data-title="${escapeHtml(brief.theme || "Planejamento")}"><strong>${escapeHtml(brief.theme || "Planejamento")}</strong><span>${escapeHtml(brief.product || "conteúdo")} · ${brief.duration_minutes || "—"} min</span></button>`; }).join("") : '<p class="muted">Seus planejamentos aparecerão aqui.</p>';
  } catch (error) { $("#history").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; }
}

function newPlan() {
  clearTimeout(state.timer); state.current = null; state.pendingStatus = null; $("#execution").hidden = true; $("#composer").hidden = false; clearError($("#form-error")); window.scrollTo({ top: 0, behavior: "smooth" });
}

document.addEventListener("DOMContentLoaded", async () => {
  try { const me = await api("/v1/me"); $("#identity").textContent = me.email; } catch (_) { $("#identity").textContent = "Sessão indisponível"; }
  $("#plan-form").addEventListener("submit", submitPlan);
  $("#files").addEventListener("change", (event) => addFiles(event.target.files));
  $("#file-list").addEventListener("click", (event) => { const index = event.target.dataset.remove; if (index !== undefined) { state.files.splice(Number(index), 1); renderFiles(); } });
  const dropzone = $("#dropzone");
  for (const name of ["dragenter", "dragover"]) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); });
  for (const name of ["dragleave", "drop"]) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); });
  dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
  $("#approve-axis").addEventListener("click", approveAxis);
  $("#resume-plan").addEventListener("click", () => runAction("resume", $("#resume-plan"), "Retomando…", "failed"));
  $("#approve-project").addEventListener("click", () => runAction("approve-project", $("#approve-project"), "Aprovando…", "project_proposed"));
  $("#new-plan").addEventListener("click", newPlan); $("#back-to-form").addEventListener("click", newPlan);
  $("#history").addEventListener("click", (event) => { const item = event.target.closest(".history-item"); if (item) openExecution(item.dataset.id, item.dataset.title); });
  await loadHistory();
});
