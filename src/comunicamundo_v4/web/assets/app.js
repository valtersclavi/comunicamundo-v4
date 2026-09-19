const $ = (selector) => document.querySelector(selector);
const formatter = new Intl.NumberFormat("pt-BR");
const state = { files: [], current: null, timer: null, history: [], pendingStatus: null, pendingSignature: null, currentStatus: null, currentProjectSignature: null };

const labels = {
  queued: "Na fila", input_failed: "Falha na entrada", provisional_project_proposed: "Direção provisória pronta", searching_sources: "Procurando fontes", source_structure_ready: "Fontes estruturadas",
  selecting: "Selecionando material", comprehending: "Compreendendo fontes",
  project_proposed: "Projeto Editorial pronto", project_approved: "Projeto aprovado",
  producing_text: "Escrevendo texto final", text_ready: "Texto final pronto", failed: "Falha"
};
const stageNames = {
  provisional_editorial_project: "Direção editorial provisória",
  source_research: "Busca e seleção editorial de fontes",
  source_acquisition: "Aquisição das fontes selecionadas",
  redigestion: "Redigestão das fontes adquiridas",
  ingestion: "Leitura e normalização das fontes",
  relational_structure: "Estrutura de parágrafos e frases",
  selection: "Triagem orientada pelo briefing",
  editorial_comprehension: "Compreensão e Projeto Editorial",
  content_draft: "Redação do conteúdo",
  content_review: "Revisão editorial do conteúdo",
  content_revision: "Correção pelo redator",
  content_contract_correction: "Ajuste do contrato de duração",
  product_styling: "Forma e estilo do produto",
  final_verification: "Verificação final de preservação",
  product_style_correction: "Correção de forma e estilo",
  final_reverification: "Verificação final da correção",
  final_text: "Geração do texto final"
};
const expectedStages = ["ingestion", "relational_structure", "selection", "editorial_comprehension", "content_draft", "content_review", "content_revision", "product_styling", "final_verification"];

function formatSeconds(totalSeconds) {
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes} min ${seconds.toString().padStart(2, "0")} s`;
}

function stageCompleted(known, name) { return known.get(name)?.status === "completed"; }

function deliverable(label, available, detail = "") {
  return `<div class="deliverable ${available ? "available" : "pending"}"><strong>${escapeHtml(label)}</strong><span>${available ? "Disponível" : "Pendente"}${detail ? ` · ${escapeHtml(detail)}` : ""}</span></div>`;
}

function renderSummary(run, known, project) {
  const usage = run.usage || {};
  const stages = run.stages || [];
  const elapsedMs = stages.reduce((sum, item) => sum + Number(item.duration_ms || 0), 0);
  const finalProject = run.editorial_project?.proposal;
  const finalText = run.final_text?.proposal;
  const sourceResearch = known.get("source_research");
  const acquisition = known.get("source_acquisition");
  $("#summary-panel").hidden = false;
  $("#summary-cost").textContent = `US$ ${Number(usage.rated_cost_usd || 0).toFixed(6)}`;
  $("#summary-time").textContent = formatSeconds(elapsedMs / 1000);
  $("#summary-calls").textContent = formatter.format(usage.call_count || 0);
  $("#summary-tokens").textContent = formatter.format(usage.total_tokens || 0);
  $("#deliverables").innerHTML = [
    deliverable("Eixo editorial", Boolean(project?.narrative_axis), project?.narrative_axis?.statement || ""),
    deliverable("Projeto Editorial", Boolean(project), project?.duration_minutes ? `${project.duration_minutes} min` : ""),
    deliverable(
      "Fontes encontradas",
      stageCompleted(known, "source_research"),
      sourceResearch?.candidate_count != null ? `${sourceResearch.candidate_count} candidatas · ${sourceResearch.selected_count || 0} selecionadas` : ""
    ),
    deliverable(
      "Fontes adquiridas",
      stageCompleted(known, "source_acquisition"),
      acquisition?.acquired_count != null ? `${acquisition.acquired_count} adquiridas · ${acquisition.failed_count || 0} falhas` : ""
    ),
    deliverable("Projeto Editorial final", Boolean(finalProject), finalProject?.duration_minutes ? `${finalProject.duration_minutes} min` : ""),
    deliverable("Texto final", Boolean(finalText), run.final_text?.word_count ? `${formatter.format(run.final_text.word_count)} palavras` : ""),
  ].join("");
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.body && typeof options.body === "string" ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
  if (!response.ok) {
    let message = `Erro ${response.status}`;
    try { const data = await response.json(); message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data); } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function showLogin() {
  clearTimeout(state.timer);
  $("#login-screen").hidden = false;
  $("#login-password").value = "";
}

async function login(event) {
  event.preventDefault(); clearError($("#login-error"));
  const button = $("#login-button"); button.disabled = true; button.textContent = "Entrando…";
  try {
    await api("/v1/login", { method: "POST", body: JSON.stringify({ username: $("#login-username").value, password: $("#login-password").value }) });
    const me = await api("/v1/me");
    $("#identity").textContent = me.username;
    $("#login-screen").hidden = true;
    await loadHistory();
  } catch (error) { showError($("#login-error"), error); }
  finally { button.disabled = false; button.textContent = "Entrar"; }
}

async function logout() {
  try { await api("/v1/logout", { method: "POST" }); } catch (_) {}
  showLogin();
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
  const findForMe = $("#find-sources").checked;
  const urls = $("#urls").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  if (!findForMe && !state.files.length && !urls.length) return showError($("#form-error"), "Adicione pelo menos um PDF ou uma URL pública, ou escolha procurar as fontes.");
  if (state.files.length + urls.length > 64) return showError($("#form-error"), "Use no máximo 64 fontes por planejamento.");
  if (urls.some((url) => !url.startsWith("https://"))) return showError($("#form-error"), "Cada URL precisa começar com https://.");
  const button = $("#submit-plan"); button.disabled = true;
  try {
    const uploaded = [];
    if (!findForMe) for (let i = 0; i < state.files.length; i++) uploaded.push(await uploadFile(state.files[i], i, state.files.length));
    button.textContent = "Iniciando o Hefesto…";
    const payload = {
      project_id: crypto.randomUUID(),
      brief: { theme: $("#theme").value, approach: $("#approach").value, duration_minutes: Number($("#duration").value), output_language: $("#language").value, audience: $("#audience").value, product: $("#product").value },
      source_mode: findForMe ? "find_for_me" : "provided",
      sources: findForMe ? [] : [...uploaded.map((item) => ({ uri: item.uri, title: item.title, language: $("#language").value })), ...urls.map((url) => ({ uri: url, title: null, language: $("#language").value }))]
    };
    const result = await api("/v1/plans", { method: "POST", body: JSON.stringify(payload) });
    await loadHistory(); await openExecution(result.execution_run_id, payload.brief.theme);
  } catch (error) { showError($("#form-error"), error); }
  finally { button.disabled = false; button.textContent = "Criar planejamento"; }
}

function statusClass(status) { return ["failed", "input_failed"].includes(status) ? "failed" : "completed"; }
function renderRun(run) {
  const status = run.status || "queued";
  state.currentStatus = status;
  $("#run-status").textContent = labels[status] || status;
  $("#execution-title").textContent = status === "text_ready" ? "Texto final" : ["project_proposed", "project_approved"].includes(status) ? "Projeto Editorial" : "Hefesto em andamento";
  const known = new Map((run.stages || []).map((item) => [item.stage, item]));
  const zeroSource = ["provisional_project_proposed", "searching_sources"].includes(status) || known.has("source_research") || known.has("redigestion");
  const baseStages = zeroSource ? ["provisional_editorial_project", "source_research", "source_acquisition", "redigestion", ...expectedStages.slice(1)] : expectedStages;
  const displayedStages = [...baseStages, ...(run.stages || []).map((item) => item.stage).filter((name) => !baseStages.includes(name))];
  const project = run.editorial_project?.proposal || run.provisional_editorial_project;
  renderSummary(run, known, project);
  let activeFound = false;
  $("#stages").innerHTML = displayedStages.map((name) => {
    const item = known.get(name); let kind = item ? statusClass(item.status) : "pending";
    if (!item && !activeFound && !["project_proposed", "project_approved", "text_ready", "failed", "input_failed"].includes(status)) { kind = "active"; activeFound = true; }
    const details = [];
    if (item?.duration_ms != null) details.push(`${(item.duration_ms / 1000).toFixed(1)} s`);
    else if (kind === "active") details.push("em andamento");
    if (item?.model_invocations) details.push(`${formatter.format(item.model_invocations)} chamada${item.model_invocations === 1 ? "" : "s"}`);
    if (item?.total_tokens) details.push(`${formatter.format(item.total_tokens)} tokens`);
    if (Number(item?.rated_cost_usd || 0)) details.push(`US$ ${Number(item.rated_cost_usd).toFixed(6)}`);
    return `<div class="stage ${kind}"><i class="stage-icon"></i><strong>${stageNames[name] || escapeHtml(name)}</strong><span>${details.join(" · ")}</span></div>`;
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
  const actionError = run.last_action_error;
  $("#run-error").hidden = !(failed || actionError);
  if (failed) $("#run-error").textContent = `${run.error_class || "Falha no processamento"}: ${run.error_message || "Não foi possível concluir esta execução."}`;
  else if (actionError) $("#run-error").textContent = `${actionError.error_class || "Falha na ação"}: ${actionError.error_message || "Não foi possível concluir esta ação."}`;
  $("#resume-plan").hidden = status !== "failed" || state.pendingStatus === "failed";
  const provisional = status === "provisional_project_proposed" || status === "searching_sources";
  state.currentProjectSignature = project ? JSON.stringify(project) : null;
  $("#project-panel").hidden = !project;
  if (project) renderProject(project, provisional);
  $("#approve-project").hidden = !["project_proposed", "provisional_project_proposed"].includes(status) || state.pendingStatus === status;
  $("#provisional-chat").hidden = status !== "provisional_project_proposed";
  $("#produce-text").hidden = status !== "project_approved" || Boolean(run.final_text) || state.pendingStatus === "project_approved";
  $("#project-approval-copy").textContent = ["project_approved", "producing_text", "text_ready"].includes(status) ? "Projeto Editorial aprovado." : "Revise o projeto antes de gerar o texto final.";
  $("#text-panel").hidden = !run.final_text;
  if (run.final_text) renderFinalText(run.final_text);
  return status;
}

function renderProject(project, provisional = false) {
  $("#project-title").textContent = project.working_title;
  $("#project-promise").textContent = project.editorial_promise;
  $("#axis-statement").textContent = provisional ? `Eixo editorial pensado: ${project.narrative_axis.statement}, a ser sustentado pelas fontes encontradas` : project.narrative_axis.statement;
  $("#axis-rationale").textContent = project.narrative_axis.rationale;
  $("#project-duration").textContent = `${project.duration_minutes} minutos`;
  const subjects = project.source_supported_subjects || project.subjects || [];
  $("#subjects").innerHTML = subjects.map((item) => `<article class="subject"><div class="subject-head"><h4>${escapeHtml(item.title)}</h4><strong>${item.allocated_minutes} min</strong></div><p>${escapeHtml(item.editorial_role)}</p><p>${escapeHtml(item.depth || item.desired_depth)}</p></article>`).join("") + (provisional ? '<p class="muted">Projeto editorial a ser sustentado pelas fontes encontradas.</p>' : "");
}

function renderFinalText(record) {
  $("#text-title").textContent = record.proposal.title;
  $("#text-meta").textContent = `${formatter.format(record.word_count)} palavras`;
  $("#text-body").textContent = record.proposal.body;
}

async function poll() {
  if (!state.current) return;
  try {
    const run = await api(`/v1/plans/${state.current}`); const status = renderRun(run);
    const waitingForAction = state.pendingStatus === status && (!state.pendingSignature || state.pendingSignature === state.currentProjectSignature);
    if (state.pendingStatus && !waitingForAction) { state.pendingStatus = null; state.pendingSignature = null; }
    if (waitingForAction || !["project_proposed", "project_approved", "text_ready", "failed", "input_failed"].includes(status)) state.timer = setTimeout(poll, 4000);
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

async function runAction(action, button, busyLabel, currentStatus) {
  button.disabled = true; const label = button.textContent; button.textContent = busyLabel;
  try { await api(`/v1/plans/${state.current}/${action}`, { method: "POST" }); state.pendingStatus = currentStatus; await poll(); }
  catch (error) { state.pendingStatus = null; state.pendingSignature = null; showError($("#run-error"), error); }
  finally { button.disabled = false; button.textContent = label; }
}

async function reviseProvisional() {
  const message = $("#provisional-message").value.trim();
  if (!message) return showError($("#run-error"), "Escreva a alteração desejada.");
  clearError($("#run-error"));
  const button = $("#revise-provisional"); button.disabled = true; button.textContent = "Revisando…";
  try {
    await api(`/v1/plans/${state.current}/revise-provisional-project`, { method: "POST", body: JSON.stringify({ message }) });
    state.pendingSignature = state.currentProjectSignature; state.pendingStatus = "provisional_project_proposed"; $("#provisional-message").value = ""; await poll();
  } catch (error) { state.pendingStatus = null; state.pendingSignature = null; showError($("#run-error"), error); }
  finally { button.disabled = false; button.textContent = "Enviar alterações"; }
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
  $("#login-form").addEventListener("submit", login);
  $("#logout").addEventListener("click", logout);
  $("#plan-form").addEventListener("submit", submitPlan);
  $("#find-sources").addEventListener("change", (event) => { $("#provided-sources").hidden = event.target.checked; });
  $("#files").addEventListener("change", (event) => addFiles(event.target.files));
  $("#file-list").addEventListener("click", (event) => { const index = event.target.dataset.remove; if (index !== undefined) { state.files.splice(Number(index), 1); renderFiles(); } });
  const dropzone = $("#dropzone");
  for (const name of ["dragenter", "dragover"]) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); });
  for (const name of ["dragleave", "drop"]) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); });
  dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
  $("#resume-plan").addEventListener("click", () => runAction("resume", $("#resume-plan"), "Retomando…", "failed"));
  $("#approve-project").addEventListener("click", () => { const provisional = state.currentStatus === "provisional_project_proposed"; runAction(provisional ? "approve-provisional-project" : "approve-project", $("#approve-project"), provisional ? "Iniciando busca…" : "Aprovando…", state.currentStatus); });
  $("#revise-provisional").addEventListener("click", reviseProvisional);
  $("#produce-text").addEventListener("click", () => runAction("produce-text", $("#produce-text"), "Gerando…", "project_approved"));
  $("#new-plan").addEventListener("click", newPlan); $("#back-to-form").addEventListener("click", newPlan);
  $("#history").addEventListener("click", (event) => { const item = event.target.closest(".history-item"); if (item) openExecution(item.dataset.id, item.dataset.title); });
  try {
    const me = await api("/v1/me");
    $("#identity").textContent = me.username;
    $("#login-screen").hidden = true;
    await loadHistory();
  } catch (_) { showLogin(); }
});
