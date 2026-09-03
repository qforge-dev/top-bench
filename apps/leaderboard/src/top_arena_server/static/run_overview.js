(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const CASE_PAGE_SIZE = 20;
  const REFRESH_MS = 5_000;
  const TERMINAL = new Set(["completed", "failed", "error"]);
  const METRICS = [
    { key: "esr", label: "ESR", digits: 4, lower: true },
    { key: "human_weighted_esr", label: "Human-weighted ESR", digits: 4, lower: true },
    { key: "mrstft", label: "MRSTFT", digits: 4, lower: true },
    { key: "level_db", label: "Level delta", digits: 2, suffix: " dB", lower: true },
    { key: "peak_db", label: "Peak delta", digits: 2, suffix: " dB", lower: true },
    { key: "correlation", label: "Correlation", digits: 4, lower: false },
    { key: "realtime_x", label: "Realtime", digits: 2, suffix: "×", lower: false },
  ];

  const root = document.querySelector("#run-overview");
  if (!root) return;

  const elements = {
    loading: document.querySelector("#report-loading"),
    error: document.querySelector("#report-error"),
    errorMessage: document.querySelector("#report-error-message"),
    retry: document.querySelector("#retry-report"),
    content: document.querySelector("#run-report-content"),
    started: document.querySelector("#report-started"),
    status: document.querySelector("#report-status"),
    name: document.querySelector("#report-name"),
    creator: document.querySelector("#report-creator"),
    ampLink: document.querySelector("#report-amp-link"),
    description: document.querySelector("#report-description"),
    progress: document.querySelector("#report-progress"),
    progressFill: document.querySelector("#report-progress-fill"),
    progressCopy: document.querySelector("#report-progress-copy"),
    facts: document.querySelector("#report-facts"),
    headline: document.querySelector("#headline-metrics"),
    distributionBody: document.querySelector("#distribution-body"),
    coverageCorrelation: document.querySelector("#coverage-correlation"),
    coverageSummary: document.querySelector("#coverage-summary"),
    coverageFacts: document.querySelector("#coverage-facts"),
    coverageChart: document.querySelector("#coverage-chart"),
    coverageTooltip: document.querySelector("#coverage-tooltip"),
    coverageWorstLink: document.querySelector("#coverage-worst-link"),
    coverageEmpty: document.querySelector("#coverage-empty"),
    findingsList: document.querySelector("#findings-list"),
    findingsEmpty: document.querySelector("#findings-empty"),
    positionSort: document.querySelector("#position-sort"),
    positionBody: document.querySelector("#position-body"),
    positionsEmpty: document.querySelector("#positions-empty"),
    casePositionFilter: document.querySelector("#case-position-filter"),
    caseSort: document.querySelector("#case-sort"),
    caseSearch: document.querySelector("#case-search"),
    caseBody: document.querySelector("#case-body"),
    casesEmpty: document.querySelector("#cases-empty"),
    casePrevious: document.querySelector("#case-previous"),
    caseNext: document.querySelector("#case-next"),
    casePage: document.querySelector("#case-page"),
    provenanceSummary: document.querySelector("#provenance-summary"),
    provenanceHead: document.querySelector("#provenance-position-head"),
    provenanceBody: document.querySelector("#provenance-position-body"),
    provenancePositionsEmpty: document.querySelector("#provenance-positions-empty"),
    provenanceFiles: document.querySelector("#provenance-files"),
    provenanceFilesEmpty: document.querySelector("#provenance-files-empty"),
  };

  const state = {
    casePage: 1,
    coverageSignature: "",
    payload: null,
    refreshTimer: null,
    requestSerial: 0,
    runId: root.dataset.runId || "",
  };

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function text(value, fallback = "") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }

  function titleCase(value) {
    return text(value, "queued").replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function element(tag, className = "", content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }

  function svg(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
    return node;
  }

  function formatNumber(value, digits = 4) {
    const number = finite(value);
    if (number === null) return "—";
    if (Math.abs(number) > 0 && Math.abs(number) < 0.0001) return number.toExponential(2);
    return number.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function formatInteger(value) {
    const number = finite(value);
    return number === null ? "—" : Math.round(number).toLocaleString();
  }

  function formatDuration(seconds) {
    const value = finite(seconds);
    if (value === null) return "—";
    if (value < 60) return `${formatNumber(value, 1)} s`;
    if (value < 3_600) return `${formatNumber(value / 60, 1)} min`;
    return `${formatNumber(value / 3_600, 1)} h`;
  }

  function formatStarted(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "—";
    const pad = (part) => String(part).padStart(2, "0");
    return `${pad(date.getUTCDate())}.${pad(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  }

  function metric(payload, key, nam = false) {
    return payload?.[nam ? "nam_metric_distributions" : "metric_distributions"]?.[key] || {};
  }

  function appendFact(list, label, value, hint = "") {
    if (!list) return;
    const wrapper = element("div");
    wrapper.append(element("dt", "", label));
    const description = element("dd", "", value);
    if (hint) description.append(element("small", "", hint));
    wrapper.append(description);
    list.append(wrapper);
  }

  function show(kind, message = "") {
    if (elements.loading) elements.loading.hidden = kind !== "loading";
    if (elements.error) elements.error.hidden = kind !== "error";
    if (elements.content) elements.content.hidden = kind !== "content";
    if (elements.errorMessage && message) elements.errorMessage.textContent = message;
    root.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
  }

  function setStatus(status) {
    if (!elements.status) return;
    const normalized = text(status, "queued").toLowerCase();
    elements.status.textContent = titleCase(normalized);
    elements.status.className = `report-status is-${normalized.replace(/[^a-z0-9-]/g, "-")}`;
  }

  function comparison(modelValue, baselineValue, lowerIsBetter = true) {
    const model = finite(modelValue);
    const baseline = finite(baselineValue);
    if (model === null || baseline === null || baseline === 0) return { copy: "NAM comparison unavailable", className: "" };
    const delta = ((model / baseline) - 1) * 100;
    const modelBetter = lowerIsBetter ? delta < 0 : delta > 0;
    const direction = delta < 0 ? "lower" : "higher";
    return {
      copy: `${Math.abs(delta).toFixed(1)}% ${direction} than NAM-A2-FULL`,
      className: modelBetter ? "is-better" : delta === 0 ? "" : "is-worse",
    };
  }

  function renderIdentity(payload) {
    const run = payload.run || {};
    if (elements.name) elements.name.textContent = text(run.name, "Untitled run");
    if (elements.creator) elements.creator.textContent = text(run.creator, "Anonymous");
    if (elements.description) elements.description.textContent = text(run.description, "No run description supplied.");
    if (elements.ampLink) {
      elements.ampLink.textContent = text(run.amp_name || run.amp_id, "Unknown amp");
      elements.ampLink.href = `/amps/${encodeURIComponent(text(run.amp_id))}`;
    }
    if (elements.started) {
      elements.started.textContent = formatStarted(run.created_at);
      if (run.created_at) elements.started.dateTime = run.created_at;
    }
    setStatus(run.status);
    document.title = `${text(run.name, "Run")} · Run report · Top Arena`;

    const completed = finite(run.completed_cases) || 0;
    const total = finite(run.total_cases) || 0;
    const fraction = total > 0 ? Math.min(1, completed / total) : 0;
    if (elements.progress) elements.progress.textContent = `${formatInteger(completed)} / ${formatInteger(total)}`;
    if (elements.progressFill) elements.progressFill.style.width = `${fraction * 100}%`;
    if (elements.progressCopy) elements.progressCopy.textContent = `${formatNumber(fraction * 100, 1)}% scored · ${titleCase(run.status)}`;

    elements.facts?.replaceChildren();
    const controlCount = finite(run.amp_control_count);
    const trainingCount = finite(run.unique_positions_used);
    appendFact(elements.facts, "Amp controls", formatInteger(controlCount), "knobs and switches");
    appendFact(elements.facts, "Training positions", formatInteger(trainingCount), controlCount && trainingCount !== null ? `${formatNumber(trainingCount / controlCount, 2)} per control` : "coverage unknown");
    appendFact(elements.facts, "Model parameters", formatInteger(run.parameter_count), "trainable metadata");
    appendFact(elements.facts, "Training dry files", formatInteger(run.training_dry_files?.length), "declared building blocks");
    appendFact(elements.facts, "Training audio", formatDuration(run.audio_duration_sum), `${formatInteger(run.turns)} turn${run.turns === 1 ? "" : "s"}`);
    appendFact(elements.facts, "Training time", formatDuration(run.training_time), "reported duration");
  }

  function renderHeadline(payload) {
    elements.headline?.replaceChildren();
    const definitions = [
      { key: "esr", label: "Mean ESR", digits: 4, lower: true },
      { key: "human_weighted_esr", label: "Human-weighted ESR", digits: 4, lower: true },
      { key: "mrstft", label: "Mean MRSTFT", digits: 4, lower: true },
      { key: "realtime_x", label: "Mean realtime", digits: 2, suffix: "×", lower: false },
    ];
    for (const definition of definitions) {
      const model = metric(payload, definition.key).mean;
      const card = element("article", "headline-card");
      card.append(element("p", "", definition.label));
      card.append(element("strong", "", `${formatNumber(model, definition.digits)}${finite(model) === null ? "" : definition.suffix || ""}`));
      let result;
      if (definition.key === "realtime_x") {
        const ratio = finite(payload.run?.metrics?.nam_a2_speed_ratio?.mean);
        result = ratio === null
          ? { copy: "Local NAM-A2 speed comparison unavailable", className: "" }
          : { copy: `${formatNumber(ratio * 100, 1)}% of local NAM-A2 speed`, className: ratio >= 1 ? "is-better" : "is-worse" };
      } else {
        result = comparison(model, metric(payload, definition.key, true).mean, definition.lower);
      }
      card.append(element("span", result.className, result.copy));
      elements.headline?.append(card);
    }
  }

  function renderDistribution(payload) {
    elements.distributionBody?.replaceChildren();
    for (const definition of METRICS) {
      const model = metric(payload, definition.key);
      const baseline = metric(payload, definition.key, true);
      const row = element("tr");
      const heading = element("th", "", definition.label);
      heading.scope = "row";
      heading.append(element("small", "", definition.lower ? "lower is better" : "higher is better"));
      row.append(heading);
      for (const key of ["mean", "median", "p90", "best", "worst"]) {
        const value = finite(model[key]);
        row.append(element("td", value === null ? "is-muted" : "", `${formatNumber(value, definition.digits)}${value === null ? "" : definition.suffix || ""}`));
      }
      const nam = finite(baseline.mean);
      row.append(element("td", nam === null ? "is-muted" : "", `${formatNumber(nam, definition.digits)}${nam === null ? "" : definition.suffix || ""}`));
      elements.distributionBody?.append(row);
    }
  }

  function renderCoverage(payload) {
    const coverage = payload.training_coverage || {};
    const correlation = coverage.esr_distance_correlation || {};
    const rho = finite(correlation.spearman_rho);
    const pearson = finite(correlation.pearson_r);
    if (elements.coverageCorrelation) {
      elements.coverageCorrelation.textContent = rho === null
        ? "Correlation unavailable"
        : `Spearman ${rho >= 0 ? "+" : ""}${rho.toFixed(3)}${pearson === null ? "" : ` · Pearson ${pearson >= 0 ? "+" : ""}${pearson.toFixed(3)}`}`;
    }
    if (elements.coverageSummary) {
      elements.coverageSummary.textContent = text(
        correlation.reading,
        text(coverage.reason, "No training-distance relationship can be estimated."),
      );
    }
    elements.coverageFacts?.replaceChildren();
    appendFact(elements.coverageFacts, "Training points", formatInteger(coverage.training_position_count));
    appendFact(elements.coverageFacts, "Compared controls", formatInteger(coverage.training_control_count));
    appendFact(elements.coverageFacts, "Measured settings", formatInteger(coverage.analyzed_settings));
    appendFact(elements.coverageFacts, "Distance range", "0–1", "0 is an exact match");

    const worst = [...(payload.positions || [])].sort((left, right) => (left.esr_error_rank || Infinity) - (right.esr_error_rank || Infinity))[0];
    if (elements.coverageWorstLink) {
      elements.coverageWorstLink.hidden = !worst;
      if (worst) elements.coverageWorstLink.href = worst.url;
    }
    if (elements.coverageEmpty) elements.coverageEmpty.hidden = Boolean(coverage.available);
    drawCoverageChart(payload);
  }

  function chartTooltip(event, copy) {
    const tooltip = elements.coverageTooltip;
    if (!tooltip) return;
    tooltip.textContent = copy;
    tooltip.hidden = false;
    const shell = tooltip.parentElement?.getBoundingClientRect() || { left: 0, top: 0, width: 960 };
    const x = Math.min(Math.max((event.clientX || shell.left) - shell.left + 12, 8), Math.max(8, shell.width - 320));
    const y = Math.max((event.clientY || shell.top) - shell.top - 16, 8);
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
  }

  function drawCoverageChart(payload) {
    const points = (payload.positions || []).map((position) => ({
      id: position.position_id,
      url: position.url,
      distance: finite(position.training_coverage?.nearest_training_distance),
      esr: finite(position.metrics?.esr?.mean),
      controls: controlsText(position),
      rank: position.esr_error_rank,
    })).filter((point) => point.distance !== null && point.esr !== null && point.esr > 0);
    const signature = JSON.stringify(points.map((point) => [point.id, point.distance, point.esr]));
    if (signature === state.coverageSignature) return;
    state.coverageSignature = signature;
    elements.coverageChart?.replaceChildren();
    if (!elements.coverageChart || points.length === 0) return;

    const width = 960;
    const height = 470;
    const margin = { top: 34, right: 50, bottom: 82, left: 100 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const maxDistance = Math.max(0.05, ...points.map((point) => point.distance));
    const minLog = Math.log10(Math.min(...points.map((point) => point.esr)));
    const maxLog = Math.log10(Math.max(...points.map((point) => point.esr)));
    const logPadding = Math.max(0.08, (maxLog - minLog) * 0.12);
    const lowLog = minLog - logPadding;
    const highLog = maxLog + logPadding;
    const x = (value) => margin.left + (value / (maxDistance * 1.08)) * plotWidth;
    const y = (value) => margin.top + ((highLog - Math.log10(value)) / (highLog - lowLog || 1)) * plotHeight;

    for (let index = 0; index <= 4; index += 1) {
      const xValue = (maxDistance * 1.08 * index) / 4;
      const xPosition = x(xValue);
      elements.coverageChart.append(svg("line", { x1: xPosition, y1: margin.top, x2: xPosition, y2: margin.top + plotHeight, class: "coverage-grid-line" }));
      const tick = svg("text", { x: xPosition, y: margin.top + plotHeight + 30, class: "coverage-tick", "text-anchor": "middle" });
      tick.textContent = formatNumber(xValue, 3);
      elements.coverageChart.append(tick);
      const yLog = lowLog + ((highLog - lowLog) * index) / 4;
      const yValue = 10 ** yLog;
      const yPosition = y(yValue);
      elements.coverageChart.append(svg("line", { x1: margin.left, y1: yPosition, x2: margin.left + plotWidth, y2: yPosition, class: "coverage-grid-line" }));
      const yTick = svg("text", { x: margin.left - 16, y: yPosition + 6, class: "coverage-tick", "text-anchor": "end" });
      yTick.textContent = formatNumber(yValue, 4);
      elements.coverageChart.append(yTick);
    }
    elements.coverageChart.append(svg("line", { x1: margin.left, y1: margin.top + plotHeight, x2: margin.left + plotWidth, y2: margin.top + plotHeight, class: "coverage-axis" }));
    elements.coverageChart.append(svg("line", { x1: margin.left, y1: margin.top, x2: margin.left, y2: margin.top + plotHeight, class: "coverage-axis" }));
    const xTitle = svg("text", { x: margin.left + plotWidth / 2, y: height - 20, class: "coverage-axis-title", "text-anchor": "middle" });
    xTitle.textContent = "DISTANCE TO NEAREST TRAINING POINT →";
    elements.coverageChart.append(xTitle);
    const yTitle = svg("text", { x: 25, y: margin.top + plotHeight / 2, class: "coverage-axis-title", "text-anchor": "middle", transform: `rotate(-90 25 ${margin.top + plotHeight / 2})` });
    yTitle.textContent = "MEAN ESR · LOG SCALE";
    elements.coverageChart.append(yTitle);

    if (points.length >= 2 && new Set(points.map((point) => point.distance)).size > 1) {
      const xMean = points.reduce((sum, point) => sum + point.distance, 0) / points.length;
      const yMean = points.reduce((sum, point) => sum + Math.log10(point.esr), 0) / points.length;
      const denominator = points.reduce((sum, point) => sum + (point.distance - xMean) ** 2, 0);
      const slope = denominator === 0 ? 0 : points.reduce((sum, point) => sum + (point.distance - xMean) * (Math.log10(point.esr) - yMean), 0) / denominator;
      const intercept = yMean - slope * xMean;
      const x1 = 0;
      const x2 = maxDistance * 1.08;
      elements.coverageChart.append(svg("line", { x1: x(x1), y1: y(10 ** (intercept + slope * x1)), x2: x(x2), y2: y(10 ** (intercept + slope * x2)), class: "coverage-trend" }));
    }

    for (const point of points) {
      const circle = svg("circle", {
        cx: x(point.distance), cy: y(point.esr), r: point.rank === 1 ? 10 : 8,
        class: `coverage-point${point.rank === 1 ? " is-highest" : ""}`,
        tabindex: 0, role: "link", "aria-label": `Position ${point.id}, mean ESR ${formatNumber(point.esr)}, training distance ${formatNumber(point.distance)}`,
      });
      const copy = `Position ${String(point.id).padStart(2, "0")} · mean ESR ${formatNumber(point.esr)} · distance ${formatNumber(point.distance)} · ${point.controls}`;
      circle.addEventListener("mouseenter", (event) => chartTooltip(event, copy));
      circle.addEventListener("mousemove", (event) => chartTooltip(event, copy));
      circle.addEventListener("mouseleave", () => { if (elements.coverageTooltip) elements.coverageTooltip.hidden = true; });
      circle.addEventListener("focus", (event) => chartTooltip(event, copy));
      circle.addEventListener("blur", () => { if (elements.coverageTooltip) elements.coverageTooltip.hidden = true; });
      circle.addEventListener("click", () => window.location.assign(point.url));
      circle.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") window.location.assign(point.url); });
      elements.coverageChart.append(circle);
      if (point.rank !== null && point.rank <= 5) {
        const label = svg("text", { x: x(point.distance) + 13, y: y(point.esr) - 10, class: "coverage-point-label" });
        label.textContent = `P${String(point.id).padStart(2, "0")}`;
        elements.coverageChart.append(label);
      }
    }
  }

  function renderFindings(payload) {
    elements.findingsList?.replaceChildren();
    const findings = payload.run?.metrics?.diagnostics?.findings || {};
    const items = [
      ...(Array.isArray(findings.strengths) ? findings.strengths.map((item) => ({ ...item, strength: true })) : []),
      ...(Array.isArray(findings.significant) ? findings.significant : []),
    ].slice(0, 10);
    if (elements.findingsEmpty) elements.findingsEmpty.hidden = items.length > 0;
    for (const item of items) {
      const card = element("article", `finding-card${item.strength ? " is-strength" : ""}`);
      const heading = element("header");
      heading.append(element("h3", "", text(item.title, "Measured finding")));
      const signal = finite(item.signal_strength);
      heading.append(element("b", "", item.strength ? "STRENGTH" : signal === null ? "MEASURED" : `${signal.toFixed(2)}× SIGNAL`));
      card.append(heading);
      card.append(element("p", "", text(item.evidence, text(item.interpretation, "Derived from completed cases."))));
      const scope = [item.scope, item.basis || item.confidence].filter(Boolean).join(" · ");
      if (scope) card.append(element("p", "", scope));
      elements.findingsList?.append(card);
    }
  }

  function positionRows(payload) {
    const rows = [...(payload.positions || [])];
    const sort = elements.positionSort?.value || "error-desc";
    const value = (position, key) => finite(position?.[key]) ?? -Infinity;
    if (sort === "error-asc") rows.sort((a, b) => value(a.metrics?.esr, "mean") - value(b.metrics?.esr, "mean"));
    else if (sort === "distance-desc") rows.sort((a, b) => value(b.training_coverage, "nearest_training_distance") - value(a.training_coverage, "nearest_training_distance"));
    else if (sort === "id-asc") rows.sort((a, b) => a.position_id - b.position_id);
    else rows.sort((a, b) => value(b.metrics?.esr, "mean") - value(a.metrics?.esr, "mean"));
    return rows;
  }

  function controlsText(position) {
    const names = Array.isArray(position.control_names) ? position.control_names : [];
    const matrix = Array.isArray(position.positions) ? position.positions : [];
    if (matrix.length === 0) return "No control metadata";
    return matrix.map((step, stepIndex) => {
      const values = Array.isArray(step) ? step : [];
      const prefix = matrix.length > 1 ? `Step ${stepIndex + 1}: ` : "";
      return prefix + values.map((value, index) => `${names[index] || `Control ${index + 1}`} ${formatNumber(value, 2)}`).join(" · ");
    }).join(" / ");
  }

  function renderPositions(payload) {
    elements.positionBody?.replaceChildren();
    const rows = positionRows(payload);
    if (elements.positionsEmpty) elements.positionsEmpty.hidden = rows.length > 0;
    for (const position of rows) {
      const row = element("tr");
      const heading = element("th");
      heading.scope = "row";
      const link = element("a", "report-table-link", `Position ${String(position.position_id).padStart(2, "0")}`);
      link.href = position.url;
      heading.append(link);
      if (position.esr_error_rank) heading.append(element("small", "", `#${position.esr_error_rank} highest error`));
      row.append(heading);
      const controls = element("td", "", controlsText(position));
      controls.title = controls.textContent;
      row.append(controls);
      row.append(element("td", "", `${formatInteger(position.completed_cases)} / ${formatInteger(position.total_cases)}`));
      row.append(element("td", finite(position.training_coverage?.nearest_training_distance) === null ? "is-muted" : "", formatNumber(position.training_coverage?.nearest_training_distance, 4)));
      for (const key of ["mean", "median", "p90", "worst"]) row.append(element("td", "", formatNumber(position.metrics?.esr?.[key], 4)));
      const action = element("td");
      const open = element("a", "report-inline-link", "Open →");
      open.href = position.url;
      action.append(open);
      row.append(action);
      elements.positionBody?.append(row);
    }
  }

  function caseRows(payload) {
    const position = elements.casePositionFilter?.value || "all";
    const query = text(elements.caseSearch?.value).trim().toLowerCase();
    const rows = (payload.cases || []).filter((item) => {
      if (position !== "all" && String(item.position_id) !== position) return false;
      if (!query) return true;
      return `${item.case_id} ${item.dry_file}`.toLowerCase().includes(query);
    });
    const sort = elements.caseSort?.value || "index";
    const value = (item, key) => finite(item.metrics?.[key]);
    if (sort === "esr-desc") rows.sort((a, b) => (value(b, "esr") ?? -Infinity) - (value(a, "esr") ?? -Infinity));
    else if (sort === "esr-asc") rows.sort((a, b) => (value(a, "esr") ?? Infinity) - (value(b, "esr") ?? Infinity));
    else if (sort === "speed-asc") rows.sort((a, b) => (value(a, "realtime_x") ?? Infinity) - (value(b, "realtime_x") ?? Infinity));
    else rows.sort((a, b) => a.index - b.index);
    return rows;
  }

  function basename(value) {
    const parts = text(value).split("/");
    return parts[parts.length - 1] || text(value, "Unknown input");
  }

  function renderCases(payload) {
    const rows = caseRows(payload);
    const pages = Math.max(1, Math.ceil(rows.length / CASE_PAGE_SIZE));
    state.casePage = Math.min(Math.max(1, state.casePage), pages);
    const visible = rows.slice((state.casePage - 1) * CASE_PAGE_SIZE, state.casePage * CASE_PAGE_SIZE);
    elements.caseBody?.replaceChildren();
    for (const item of visible) {
      const row = element("tr");
      const heading = element("th");
      heading.scope = "row";
      const link = element("a", "report-table-link", `Case ${String(item.index).padStart(3, "0")}`);
      link.href = item.url;
      heading.append(link, element("small", "", `Chunk ${String(item.chunk_index + 1).padStart(2, "0")}`));
      row.append(heading);
      const dry = element("td", "", basename(item.dry_file));
      dry.title = text(item.dry_file);
      row.append(dry);
      const positionCell = element("td");
      const positionLink = element("a", "report-table-link", `Position ${String(item.position_id).padStart(2, "0")}`);
      positionLink.href = item.position_url;
      positionCell.append(positionLink);
      row.append(positionCell);
      row.append(element("td", "", titleCase(item.status)));
      row.append(element("td", "", formatNumber(item.metrics?.esr, 4)));
      row.append(element("td", "", formatNumber(item.metrics?.human_weighted_esr, 4)));
      row.append(element("td", "", formatNumber(item.metrics?.mrstft, 4)));
      row.append(element("td", "", finite(item.metrics?.realtime_x) === null ? "—" : `${formatNumber(item.metrics.realtime_x, 2)}×`));
      const action = element("td");
      const inspect = element("a", "report-inline-link", "Inspect →");
      inspect.href = item.url;
      action.append(inspect);
      row.append(action);
      elements.caseBody?.append(row);
    }
    if (elements.casesEmpty) elements.casesEmpty.hidden = rows.length > 0;
    if (elements.casePage) elements.casePage.textContent = rows.length ? `Page ${state.casePage} / ${pages} · ${rows.length} cases` : "0 cases";
    if (elements.casePrevious) elements.casePrevious.disabled = state.casePage <= 1;
    if (elements.caseNext) elements.caseNext.disabled = state.casePage >= pages;
  }

  function populatePositionFilter(payload) {
    if (!elements.casePositionFilter) return;
    const selected = elements.casePositionFilter.value;
    elements.casePositionFilter.replaceChildren();
    const all = element("option", "", "All positions");
    all.value = "all";
    elements.casePositionFilter.append(all);
    for (const position of payload.positions || []) {
      const option = element("option", "", `Position ${String(position.position_id).padStart(2, "0")}`);
      option.value = String(position.position_id);
      elements.casePositionFilter.append(option);
    }
    elements.casePositionFilter.value = [...elements.casePositionFilter.options].some((option) => option.value === selected) ? selected : "all";
  }

  function renderProvenance(payload) {
    const run = payload.run || {};
    const positions = Array.isArray(run.training_positions) ? run.training_positions : [];
    const files = Array.isArray(run.training_dry_files) ? run.training_dry_files : [];
    if (elements.provenanceSummary) elements.provenanceSummary.textContent = `${positions.length} positions · ${files.length} dry files`;
    elements.provenanceHead?.replaceChildren();
    elements.provenanceBody?.replaceChildren();
    if (positions.length) {
      const names = Array.isArray(run.amp_control_names) ? run.amp_control_names : [];
      const width = Math.max(...positions.map((position) => Array.isArray(position) ? position.length : 0));
      const header = element("tr");
      const positionHeader = element("th", "", "Point");
      positionHeader.scope = "col";
      header.append(positionHeader);
      for (let index = 0; index < width; index += 1) {
        const cell = element("th", "", names[index] || `Control ${index + 1}`);
        cell.scope = "col";
        header.append(cell);
      }
      elements.provenanceHead?.append(header);
      positions.forEach((position, positionIndex) => {
        const row = element("tr");
        const heading = element("th", "", String(positionIndex + 1).padStart(2, "0"));
        heading.scope = "row";
        row.append(heading);
        for (let index = 0; index < width; index += 1) row.append(element("td", "", formatNumber(position[index], 6)));
        elements.provenanceBody?.append(row);
      });
    }
    if (elements.provenancePositionsEmpty) elements.provenancePositionsEmpty.hidden = positions.length > 0;
    elements.provenanceFiles?.replaceChildren();
    for (const file of files) elements.provenanceFiles?.append(element("li", "", text(file)));
    if (elements.provenanceFilesEmpty) elements.provenanceFilesEmpty.hidden = files.length > 0;
  }

  function render(payload) {
    state.payload = payload;
    renderIdentity(payload);
    renderHeadline(payload);
    renderDistribution(payload);
    renderCoverage(payload);
    renderFindings(payload);
    populatePositionFilter(payload);
    renderPositions(payload);
    renderCases(payload);
    renderProvenance(payload);
    show("content");
  }

  function scheduleRefresh(payload) {
    if (state.refreshTimer !== null) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
    if (!TERMINAL.has(text(payload?.run?.status).toLowerCase())) {
      state.refreshTimer = window.setTimeout(() => load(false), REFRESH_MS);
    }
  }

  async function load(initial = true) {
    const serial = ++state.requestSerial;
    if (initial) show("loading");
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(state.runId)}/overview`, { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
      const payload = await response.json();
      if (serial !== state.requestSerial) return;
      render(payload);
      scheduleRefresh(payload);
    } catch (error) {
      if (serial !== state.requestSerial) return;
      if (initial || !state.payload) show("error", error instanceof Error ? error.message : "The report could not be loaded.");
      else scheduleRefresh(state.payload);
    }
  }

  elements.retry?.addEventListener("click", () => load(true));
  elements.positionSort?.addEventListener("change", () => { if (state.payload) renderPositions(state.payload); });
  elements.casePositionFilter?.addEventListener("change", () => { state.casePage = 1; if (state.payload) renderCases(state.payload); });
  elements.caseSort?.addEventListener("change", () => { state.casePage = 1; if (state.payload) renderCases(state.payload); });
  elements.caseSearch?.addEventListener("input", () => { state.casePage = 1; if (state.payload) renderCases(state.payload); });
  elements.casePrevious?.addEventListener("click", () => { state.casePage -= 1; if (state.payload) renderCases(state.payload); });
  elements.caseNext?.addEventListener("click", () => { state.casePage += 1; if (state.payload) renderCases(state.payload); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && state.refreshTimer !== null) {
      window.clearTimeout(state.refreshTimer);
      state.refreshTimer = null;
    } else if (!document.hidden && state.payload && !TERMINAL.has(text(state.payload.run?.status).toLowerCase())) {
      load(false);
    }
  });

  load(true);
})();
