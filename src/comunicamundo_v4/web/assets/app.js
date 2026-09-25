const $ = (selector) => document.querySelector(selector);
const formatter = new Intl.NumberFormat("pt-BR");
const state = { files: [], current: null, timer: null, history: [], pendingStatus: null, pendingSignature: null, currentStatus: null, currentProjectSignature: null };
const sourceFormats = {
  pdf: ["application/pdf", "PDF"], docx: ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Word"],
  pptx: ["application/vnd.openxmlformats-officedocument.presentationml.presentation", "PowerPoint"], xlsx: ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel"],
  html: ["text/html", "HTML"], htm: ["text/html", "HTML"], xhtml: ["application/xhtml+xml", "XHTML"], epub: ["application/epub+zip", "EPUB"],
  md: ["text/markdown", "Markdown"], txt: ["text/plain", "Texto"],
  png: ["image/png", "Imagem"], jpg: ["image/jpeg", "Imagem"], jpeg: ["image/jpeg", "Imagem"], tif: ["image/tiff", "Imagem"], tiff: ["image/tiff", "Imagem"], webp: ["image/webp", "Imagem"],
  wav: ["audio/wav", "Áudio"], mp3: ["audio/mpeg", "Áudio"], aac: ["audio/aac", "Áudio"],
  mp4: ["video/mp4", "Vídeo"], avi: ["video/x-msvideo", "Vídeo"], mov: ["video/quicktime", "Vídeo"], webm: ["video/webm", "Vídeo"]
};

const labels = {
  queued: "Na fila", ingesting: "Lendo arquivos", input_failed: "Falha na entrada", provisional_project_proposed: "Direção provisória pronta", searching_sources: "Procurando fontes", source_structure_ready: "Fontes estruturadas",
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
  const provisionalProject = run.provisional_editorial_project || run.request?.provisional_project;
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
    deliverable("Pré-Projeto Editorial", Boolean(provisionalProject), provisionalProject?.duration_minutes ? `${provisionalProject.duration_minutes} min` : ""),
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
function escapeAttribute(value) { return escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;"); }
function sourceFormat(file) { return sourceFormats[file.name.split(".").pop().toLowerCase()] || null; }

function renderFiles() {
  $("#file-list").innerHTML = state.files.map((file, index) => `<div class="file-item"><span>${sourceFormat(file)?.[1] || "Arquivo"}</span><strong>${escapeHtml(file.name)}</strong><span>${bytes(file.size)}</span><button type="button" data-remove="${index}" aria-label="Remover ${escapeHtml(file.name)}">×</button></div>`).join("");
}

function addFiles(files) {
  for (const file of files) {
    if (!sourceFormat(file)) { showError($("#form-error"), `“${file.name}” não está em um formato aceito.`); continue; }
    if (file.size > 200 * 1024 ** 2) { showError($("#form-error"), `“${file.name}” ultrapassa 200 MB.`); continue; }
    if (!state.files.some((item) => item.name === file.name && item.size === file.size)) state.files.push(file);
  }
  renderFiles();
}

async function uploadFile(file, index, totalFiles) {
  const button = $("#submit-plan");
  button.textContent = `Enviando arquivo ${index + 1} de ${totalFiles}…`;
  const start = await api("/v1/uploads", { method: "POST", body: JSON.stringify({ file_name: file.name, content_type: sourceFormat(file)[0], size_bytes: file.size }) });
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
  if (!findForMe && !state.files.length && !urls.length) return showError($("#form-error"), "Adicione pelo menos um arquivo ou uma URL pública, ou escolha procurar as fontes.");
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
      sources: findForMe ? [] : [...uploaded.map((item) => ({ uri: item.uri, title: item.title, content_type: item.content_type, language: $("#language").value })), ...urls.map((url) => ({ uri: url, title: null, language: $("#language").value }))]
    };
    const engine = $("#matrix-preview").checked ? "matrix" : "legacy";
    const result = await api("/v1/plans", { method: "POST", headers: { "X-Editorial-Engine": engine }, body: JSON.stringify(payload) });
    await loadHistory(); await openExecution(result.execution_run_id, payload.brief.theme);
  } catch (error) { showError($("#form-error"), error); }
  finally { button.disabled = false; button.textContent = "Criar planejamento"; }
}

function statusClass(status) { return ["failed", "input_failed"].includes(status) ? "failed" : "completed"; }

function renderSourceManifest(known, provisionalProject) {
  const research = known.get("source_research");
  const acquisition = known.get("source_acquisition");
  const records = Array.isArray(acquisition?.sources) ? acquisition.sources : [];
  const coverage = research?.subject_coverage || {};
  const subjects = provisionalProject?.subjects || provisionalProject?.source_supported_subjects || [];
  const panel = $("#sources-panel");
  panel.hidden = records.length === 0;
  if (!records.length) { $("#source-manifest").innerHTML = ""; return; }
  $("#source-manifest").innerHTML = records.map((record, ordinal) => {
    const candidate = record.candidate || {};
    const indexes = Array.isArray(coverage[candidate.candidate_id]) ? coverage[candidate.candidate_id] : [];
    const covered = indexes.map((index) => subjects[index]?.title || `Assunto ${index + 1}`);
    const status = record.status === "acquired" ? "Adquirida" : record.status === "partial" ? "Parcial" : "Falhou";
    const route = record.selected_route ? ` · ${record.selected_route}` : "";
    const url = record.final_url || candidate.url;
    const safeUrl = typeof url === "string" && url.startsWith("https://") ? url : null;
    const link = safeUrl ? `<a href="${escapeAttribute(safeUrl)}" target="_blank" rel="noopener noreferrer">Abrir fonte</a>` : "";
    return `<article class="source-record">
      <div class="source-record-head"><span>Fonte ${ordinal + 1}</span><strong class="source-status ${escapeHtml(record.status || "failed")}">${status}${escapeHtml(route)}</strong></div>
      <h3>${escapeHtml(candidate.title || "Fonte sem título")}</h3>
      <p class="source-meta">${escapeHtml(candidate.publisher || candidate.provider || "Origem não informada")}${candidate.doi ? ` · DOI ${escapeHtml(candidate.doi)}` : ""} ${link}</p>
      <div class="source-coverage"><span>Cobre</span>${covered.length ? covered.map((title) => `<em>${escapeHtml(title)}</em>`).join("") : "<em>Cobertura não registrada</em>"}</div>
    </article>`;
  }).join("");
}

function renderInputSources(input, runStatus) {
  const panel = $("#input-sources-panel");
  const successful = Array.isArray(input?.sources) ? input.sources : [];
  const progress = Array.isArray(input?.source_progress) ? input.source_progress : [];
  const failed = [
    ...(Array.isArray(input?.failed_normalizations) ? input.failed_normalizations : []),
    ...(Array.isArray(input?.failed_acquisitions) ? input.failed_acquisitions : [])
  ];
  const submitted = Array.isArray(input?.submitted_sources) && input.submitted_sources.length
    ? input.submitted_sources
    : progress.length ? progress : [...successful, ...failed];
  panel.hidden = !submitted.length;
  if (!submitted.length) { $("#input-sources-manifest").innerHTML = ""; return; }
  const stored = submitted.filter((source) => successful.some((item) => item.input_uri === source.input_uri && item.persisted) || progress.some((item) => item.input_uri === source.input_uri && item.status === "persisted")).length;
  $("#input-sources-title").textContent = `${stored} de ${submitted.length} arquivos com frases no banco`;
  $("#input-sources-manifest").innerHTML = submitted.map((source, index) => {
    const item = successful.find((record) => record.input_uri === source.input_uri);
    const current = progress[index]?.input_uri === source.input_uri ? progress[index] : progress.find((record) => record.input_uri === source.input_uri);
    const error = failed.find((record) => record.input_uri === source.input_uri) || (current?.status === "failed" ? current : null);
    const name = source.title || source.input_uri?.split("/").pop() || `Arquivo ${index + 1}`;
    const persisted = Boolean(item?.persisted || current?.status === "persisted");
    const digesting = persisted && !["queued", "ingesting", "input_failed"].includes(runStatus);
    const outcome = error ? "Falhou" : digesting ? "Em digestão" : persisted ? "Frases no banco" : item?.status === "partial" ? "Parcial" : item ? "Normalizado" : ({reading: "Lendo", normalizing: "Preparando extração", extracting: "Extraindo texto", normalized: "Texto preparado", queued: "Aguardando"}[current?.status] || "Aguardando");
    const phases = [
      ["Recebido", true],
      ["Lendo", Boolean(item || ["reading", "normalizing", "extracting", "normalized"].includes(current?.status))],
      ["Texto", Boolean(item || ["normalized"].includes(current?.status) || current?.status === "extracting" && current?.pages_done === current?.pages)],
      ["Frases no banco", persisted],
      ["Digestão", Boolean(digesting)]
    ];
    const pageProgress = current?.status === "extracting" && current?.pages > 0
      ? `${formatter.format(current.pages_done || 0)} de ${formatter.format(current.pages)} páginas lidas (${Math.round(100 * (current.pages_done || 0) / current.pages)}%)`
      : "";
    const steps = item ? [
      item.original_stored ? "Original guardado" : "Original: sem confirmação",
      item.normalized ? "Texto extraído" : "Extração: sem confirmação",
      `${formatter.format(item.paragraphs || 0)} parágrafos · ${formatter.format(item.sentences || 0)} frases`,
      persisted ? `${formatter.format(item.stored_sentences || current?.sentences || 0)} frases conferidas no banco` : "Banco: sem confirmação"
    ] : error ? [`Falha em ${error.failed_step || "aquisição ou normalização"}`]
      : [persisted ? `${formatter.format(current?.sentences || 0)} frases conferidas no banco` : current?.status === "normalized" ? `${formatter.format(current.sentences || 0)} frases preparadas; aguardando Postgres` : outcome];
    return `<article class="source-record">
      <div class="source-record-head"><span>Arquivo ${index + 1}</span><strong class="source-status ${persisted ? "acquired" : error ? "failed" : ""}">${outcome}</strong></div>
      <h3>${escapeHtml(name)}</h3>
      <div class="source-steps" aria-label="Etapas do arquivo">${phases.map(([label, done]) => `<span class="source-step ${done ? "done" : ""}">${done ? "●" : "○"} ${label}</span>`).join("")}</div>
      ${pageProgress ? `<p class="source-meta">${escapeHtml(pageProgress)}</p>` : ""}
      <p class="source-meta">${steps.map(escapeHtml).join(" · ")}</p>
      ${error ? `<p class="source-error">${escapeHtml(error.error_class || "Falha")}: ${escapeHtml(error.error_message || "Sem detalhes")}</p>` : ""}
    </article>`;
  }).join("");
}

function renderAssetManifest(input) {
  const assets = Array.isArray(input?.assets) ? input.assets : [];
  const panel = $("#assets-panel");
  panel.hidden = assets.length === 0;
  if (!assets.length) { $("#asset-manifest").innerHTML = ""; return; }
  const names = { table: "Tabela", figure: "Figura", chart: "Gráfico", unknown_picture: "Imagem" };
  $("#asset-manifest").innerHTML = assets.map((asset, index) => `<article class="source-record">
    <div class="source-record-head"><span>${escapeHtml(names[asset.kind] || "Ativo")} ${index + 1}</span><strong>${asset.page_no ? `Página ${asset.page_no}` : "Página não identificada"}</strong></div>
    <h3>${escapeHtml(asset.caption || `${names[asset.kind] || "Ativo"} sem legenda`)}</h3>
    <p class="source-meta">${asset.has_image ? "Imagem preservada" : "Sem recorte visual"}${asset.has_structure ? " · Estrutura preservada" : ""} · ${formatter.format(asset.linked_paragraphs || 0)} vínculo${asset.linked_paragraphs === 1 ? "" : "s"} textual${asset.linked_paragraphs === 1 ? "" : "is"}</p>
  </article>`).join("");
}

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
  renderSourceManifest(known, run.provisional_editorial_project || run.request?.provisional_project);
  renderInputSources(run.input, status);
  renderAssetManifest(run.input);
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
  const canResume = ["failed", "searching_sources"].includes(status);
  $("#resume-plan").hidden = !canResume || state.pendingStatus === status;
  const provisional = status === "provisional_project_proposed" || status === "searching_sources";
  state.currentProjectSignature = project ? JSON.stringify(project) : null;
  $("#project-panel").hidden = !project;
  if (project) renderProject(project, provisional);
  $("#approve-project").hidden = !["project_proposed", "provisional_project_proposed"].includes(status) || state.pendingStatus === status;
  $("#approve-project").textContent = status === "provisional_project_proposed" ? "Aprovar pré-projeto" : "Aprovar Projeto Editorial";
  $("#project-chat").hidden = !["provisional_project_proposed", "project_proposed"].includes(status);
  $("#produce-text").hidden = status !== "project_approved" || Boolean(run.final_text) || state.pendingStatus === "project_approved";
  $("#project-approval-copy").textContent =
    status === "provisional_project_proposed"
      ? "Revise o pré-projeto ou aprove para o Hefesto buscar fontes automaticamente."
      : status === "searching_sources"
        ? "Pré-projeto aprovado. O Hefesto está buscando fontes e preparará o Projeto Editorial definitivo."
        : ["project_approved", "producing_text", "text_ready"].includes(status)
          ? "Projeto Editorial aprovado."
          : "Revise o Projeto Editorial antes de gerar o texto final.";
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
  const model = provisional ? [] : (project.model_knowledge_suggestions || []);
  const sources = provisional ? [] : (project.source_enrichment_opportunities || []);
  $("#project-suggestions").hidden = !model.length && !sources.length;
  $("#project-suggestions").innerHTML = [
    model.length ? '<h3>Contribuições do LLM</h3><p>Pequenos acréscimos para assuntos existentes.</p>' + model.map((item, index) => `<article class="subject"><p>${escapeHtml(item)}</p><button class="secondary-button" data-suggestion="model" data-index="${index}" type="button">Incluir contribuição</button></article>`).join("") : "",
    sources.length ? '<h3>Enriquecimento com fontes</h3><p>O Hefesto buscará uma fonte para sustentar o assunto escolhido e apresentará um novo projeto.</p>' + sources.map((item, index) => `<article class="subject"><p>${escapeHtml(item)}</p><button class="secondary-button" data-suggestion="source" data-index="${index}" type="button">Buscar fonte</button></article>`).join("") : ""
  ].join("");
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

async function reviseProject() {
  const message = $("#project-message").value.trim();
  if (!message) return showError($("#run-error"), "Escreva a alteração desejada.");
  clearError($("#run-error"));
  const provisional = state.currentStatus === "provisional_project_proposed";
  const button = $("#revise-project"); button.disabled = true; button.textContent = "Revisando…";
  try {
    const route = provisional ? "revise-provisional-project" : "revise-project";
    await api(`/v1/plans/${state.current}/${route}`, { method: "POST", body: JSON.stringify({ message }) });
    state.pendingSignature = state.currentProjectSignature; state.pendingStatus = state.currentStatus; $("#project-message").value = ""; await poll();
  } catch (error) { state.pendingStatus = null; state.pendingSignature = null; showError($("#run-error"), error); }
  finally { button.disabled = false; button.textContent = "Enviar alterações"; }
}

async function chooseSuggestion(kind, index, button) {
  button.disabled = true;
  clearError($("#run-error"));
  try {
    const route = kind === "model" ? `model-suggestions/${index}/accept` : `source-suggestions/${index}/accept`;
    await api(`/v1/plans/${state.current}/${route}`, { method: "POST" });
    state.pendingSignature = state.currentProjectSignature; state.pendingStatus = state.currentStatus; await poll();
  } catch (error) { state.pendingStatus = null; state.pendingSignature = null; showError($("#run-error"), error); }
  finally { button.disabled = false; }
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
  $("#resume-plan").addEventListener("click", () => runAction("resume", $("#resume-plan"), "Retomando…", state.currentStatus));
  $("#approve-project").addEventListener("click", () => { const provisional = state.currentStatus === "provisional_project_proposed"; runAction(provisional ? "approve-provisional-project" : "approve-project", $("#approve-project"), provisional ? "Aprovando pré-projeto…" : "Aprovando…", state.currentStatus); });
  $("#revise-project").addEventListener("click", reviseProject);
  $("#project-suggestions").addEventListener("click", (event) => { const button = event.target.closest("button[data-suggestion]"); if (button) chooseSuggestion(button.dataset.suggestion, Number(button.dataset.index), button); });
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
