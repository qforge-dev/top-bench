(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
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

  const root = document.querySelector("#position-detail");
  if (!root) return;

  const elements = {
    loading: document.querySelector("#position-loading"),
    error: document.querySelector("#position-error"),
    errorMessage: document.querySelector("#position-error-message"),
    retry: document.querySelector("#retry-position"),
    content: document.querySelector("#position-report-content"),
    started: document.querySelector("#position-started"),
    runLink: document.querySelector("#position-run-link"),
    status: document.querySelector("#position-status"),
    runName: document.querySelector("#position-run-name"),
    number: document.querySelector("#position-number"),
    summary: document.querySelector("#position-summary"),
    rank: document.querySelector("#position-rank"),
    rankCopy: document.querySelector("#position-rank-copy"),
    controls: document.querySelector("#position-controls"),
    insights: document.querySelector("#position-insights"),
    distributionBody: document.querySelector("#position-distribution-body"),
    distanceLabel: document.querySelector("#position-distance-label"),
    neighborhoodReading: document.querySelector("#neighborhood-reading"),
    neighborhoodFacts: document.querySelector("#neighborhood-facts"),
    nearestControls: document.querySelector("#nearest-training-controls"),
    neighborhoodEmpty: document.querySelector("#neighborhood-empty"),
    chart: document.querySelector("#position-case-chart"),
    chartSummary: document.querySelector("#position-chart-summary"),
    tooltip: document.querySelector("#position-chart-tooltip"),
    caseBody: document.querySelector("#position-case-body"),
    casesEmpty: document.querySelector("#position-cases-empty"),
  };

  const state = {
    chartSignature: "",
    payload: null,
    refreshTimer: null,
    requestSerial: 0,
    runId: root.dataset.runId || "",
    positionId: root.dataset.positionId || "",
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

  function formatStarted(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "—";
    const pad = (part) => String(part).padStart(2, "0");
    return `${pad(date.getUTCDate())}.${pad(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  }

  function basename(value) {
    const parts = text(value).split("/");
    return parts[parts.length - 1] || text(value, "Unknown input");
  }

  function show(kind, message = "") {
    if (elements.loading) elements.loading.hidden = kind !== "loading";
    if (elements.error) elements.error.hidden = kind !== "error";
    if (elements.content) elements.content.hidden = kind !== "content";
    if (elements.errorMessage && message) elements.errorMessage.textContent = message;
    root.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
  }

  function appendChip(target, label, value, stepIndex = null) {
    if (!target) return;
    const chip = element("span", "position-control-chip");
    const name = stepIndex === null ? label : `Step ${stepIndex + 1} · ${label}`;
    chip.append(element("span", "", name), element("b", "", formatNumber(value, 4)));
    target.append(chip);
  }

  function renderControlMatrix(target, matrix, names) {
    target?.replaceChildren();
    if (!target || !Array.isArray(matrix) || matrix.length === 0) return;
    matrix.forEach((step, stepIndex) => {
      if (!Array.isArray(step)) return;
      step.forEach((value, index) => appendChip(target, names?.[index] || `Control ${index + 1}`, value, matrix.length > 1 ? stepIndex : null));
    });
  }

  function setStatus(status) {
    if (!elements.status) return;
    const normalized = text(status, "queued").toLowerCase();
    elements.status.textContent = titleCase(normalized);
    elements.status.className = `report-status is-${normalized.replace(/[^a-z0-9-]/g, "-")}`;
  }

  function renderIdentity(payload) {
    const run = payload.run || {};
    const position = payload.position || {};
    const positionNumber = String(position.position_id || state.positionId).padStart(2, "0");
    if (elements.runName) elements.runName.textContent = text(run.name, "Untitled run");
    if (elements.number) elements.number.textContent = positionNumber;
    if (elements.runLink) {
      elements.runLink.textContent = text(run.name, "Run report");
      elements.runLink.href = `/runs/${encodeURIComponent(state.runId)}`;
    }
    if (elements.started) {
      elements.started.textContent = formatStarted(run.created_at);
      if (run.created_at) elements.started.dateTime = run.created_at;
    }
    setStatus(run.status);
    document.title = `${text(run.name, "Run")} · Position ${positionNumber} · Top Arena`;
    if (elements.summary) {
      elements.summary.textContent = `This exact ${text(run.amp_name || run.amp_id, "amp")} setting was evaluated against ${position.total_cases || 0} dry input${position.total_cases === 1 ? "" : "s"}. Open any case below for waveform and listening evidence.`;
    }
    const totalPositions = finite(payload.training_coverage?.analyzed_settings);
    if (elements.rank) elements.rank.textContent = position.esr_error_rank ? `#${position.esr_error_rank}` : "—";
    if (elements.rankCopy) {
      elements.rankCopy.textContent = position.esr_error_rank
        ? `of ${totalPositions || "all"} measured settings · rank 1 has the highest mean ESR`
        : "Waiting for completed ESR scores";
    }
    renderControlMatrix(elements.controls, position.positions, position.control_names);
  }

  function addInsight(label, value, copy, className = "") {
    const card = element("article", `position-insight${className ? ` ${className}` : ""}`);
    card.append(element("p", "", label), element("strong", "", value), element("span", "", copy));
    elements.insights?.append(card);
  }

  function renderInsights(payload) {
    elements.insights?.replaceChildren();
    const position = payload.position || {};
    const local = position.metrics?.esr || {};
    const run = payload.run_metric_distributions?.esr || {};
    const localMean = finite(local.mean);
    const runMean = finite(run.mean);
    let relativeCopy = "Run comparison unavailable.";
    let relativeClass = "";
    if (localMean !== null && runMean !== null && runMean !== 0) {
      const delta = ((localMean / runMean) - 1) * 100;
      relativeCopy = `${Math.abs(delta).toFixed(1)}% ${delta >= 0 ? "higher" : "lower"} than the whole-run mean of ${formatNumber(runMean)}.`;
      relativeClass = delta > 0 ? "is-warning" : "is-good";
    }
    addInsight("Mean ESR", formatNumber(localMean), relativeCopy, relativeClass);

    const median = finite(local.median);
    const worst = finite(local.worst);
    const tailRatio = median !== null && worst !== null && median !== 0 ? worst / median : null;
    addInsight(
      "Worst dry input",
      formatNumber(worst),
      tailRatio === null ? "Tail variation is not available yet." : `${formatNumber(tailRatio, 2)}× the median ESR of ${formatNumber(median)}.`,
      tailRatio !== null && tailRatio >= 2 ? "is-warning" : "",
    );

    const distance = finite(position.training_coverage?.nearest_training_distance);
    const pointId = finite(position.training_coverage?.nearest_training_points?.[0]?.training_position_id);
    addInsight(
      "Nearest training point",
      formatNumber(distance, 4),
      distance === null ? "Exact training-position metadata was not declared." : `${distance === 0 ? "Exact match" : "Normalized RMS distance"}${pointId === null ? "." : ` to declared point ${Math.round(pointId)}.`}`,
      distance === 0 ? "is-good" : "",
    );
  }

  function renderDistribution(payload) {
    elements.distributionBody?.replaceChildren();
    for (const definition of METRICS) {
      const local = payload.position?.metrics?.[definition.key] || {};
      const run = payload.run_metric_distributions?.[definition.key] || {};
      const row = element("tr");
      const heading = element("th", "", definition.label);
      heading.scope = "row";
      heading.append(element("small", "", definition.lower ? "lower is better" : "higher is better"));
      row.append(heading);
      for (const key of ["mean", "median", "p90", "best", "worst"]) {
        const value = finite(local[key]);
        row.append(element("td", value === null ? "is-muted" : "", `${formatNumber(value, definition.digits)}${value === null ? "" : definition.suffix || ""}`));
      }
      const runMean = finite(run.mean);
      row.append(element("td", runMean === null ? "is-muted" : "", `${formatNumber(runMean, definition.digits)}${runMean === null ? "" : definition.suffix || ""}`));
      elements.distributionBody?.append(row);
    }
  }

  function appendFact(label, value, hint = "") {
    if (!elements.neighborhoodFacts) return;
    const wrapper = element("div");
    wrapper.append(element("dt", "", label));
    const description = element("dd", "", value);
    if (hint) description.append(element("small", "", hint));
    wrapper.append(description);
    elements.neighborhoodFacts.append(wrapper);
  }

  function renderNeighborhood(payload) {
    const coverage = payload.position?.training_coverage || {};
    const distance = finite(coverage.nearest_training_distance);
    const nearestPoints = Array.isArray(coverage.nearest_training_points) ? coverage.nearest_training_points : [];
    const pointId = finite(nearestPoints[0]?.training_position_id);
    const nearest = nearestPoints
      .map((point) => point?.training_position)
      .filter((point) => Array.isArray(point));
    const overall = payload.training_coverage?.esr_distance_correlation || {};
    const rho = finite(overall.spearman_rho);
    if (elements.distanceLabel) elements.distanceLabel.textContent = distance === null ? "Distance unavailable" : `Distance ${formatNumber(distance, 4)}`;
    if (elements.neighborhoodReading) {
      const localReading = distance === 0
        ? "This measured setting exactly matches a declared training point."
        : distance === null
          ? "This run does not include comparable exact training-position metadata."
          : "This is the normalized RMS distance to the closest declared training setting: 0 is identical and 1 spans the full normalized control range.";
      const correlationReading = text(overall.reading);
      elements.neighborhoodReading.textContent = correlationReading ? `${localReading} Across this run, ${correlationReading.charAt(0).toLowerCase()}${correlationReading.slice(1)}` : localReading;
    }
    elements.neighborhoodFacts?.replaceChildren();
    appendFact("Nearest point", pointId === null ? "—" : String(Math.round(pointId)).padStart(2, "0"));
    appendFact("Normalized distance", formatNumber(distance, 6), "0 exact · 1 opposite");
    appendFact("Run Spearman", rho === null ? "—" : `${rho >= 0 ? "+" : ""}${rho.toFixed(3)}`, "distance ↔ ESR");
    appendFact("Compared controls", String(payload.training_coverage?.training_control_count ?? "—"));
    renderControlMatrix(elements.nearestControls, nearest, payload.position?.control_names || []);
    if (elements.neighborhoodEmpty) elements.neighborhoodEmpty.hidden = distance !== null;
  }

  function chartTooltip(event, copy) {
    if (!elements.tooltip) return;
    elements.tooltip.textContent = copy;
    elements.tooltip.hidden = false;
    const shell = elements.tooltip.parentElement?.getBoundingClientRect() || { left: 0, top: 0, width: 1060 };
    elements.tooltip.style.left = `${Math.min(Math.max((event.clientX || shell.left) - shell.left + 12, 8), Math.max(8, shell.width - 320))}px`;
    elements.tooltip.style.top = `${Math.max((event.clientY || shell.top) - shell.top - 16, 8)}px`;
  }

  function drawCaseChart(payload) {
    const cases = (payload.cases || []).map((item) => ({
      index: item.index,
      dry: basename(item.dry_file),
      esr: finite(item.metrics?.esr),
      nam: finite(item.metrics?.nam_esr),
      url: item.url,
    })).filter((item) => item.esr !== null || item.nam !== null);
    const signature = JSON.stringify(cases.map((item) => [item.index, item.esr, item.nam]));
    if (signature === state.chartSignature) return;
    state.chartSignature = signature;
    elements.chart?.replaceChildren();
    if (elements.chartSummary) elements.chartSummary.textContent = cases.length ? `${cases.length} dry-input ESR comparisons.` : "No scored cases are available.";
    if (!elements.chart || cases.length === 0) return;

    const width = 1060;
    const height = 470;
    const margin = { top: 34, right: 42, bottom: 88, left: 90 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const maximum = Math.max(0.001, ...cases.flatMap((item) => [item.esr, item.nam].filter((value) => value !== null)));
    const y = (value) => margin.top + plotHeight - (value / (maximum * 1.12)) * plotHeight;
    const slot = plotWidth / cases.length;
    const barWidth = Math.max(4, Math.min(34, slot * 0.58));

    for (let index = 0; index <= 4; index += 1) {
      const value = (maximum * 1.12 * index) / 4;
      const yPosition = y(value);
      elements.chart.append(svg("line", { x1: margin.left, y1: yPosition, x2: margin.left + plotWidth, y2: yPosition, class: "position-grid-line" }));
      const tick = svg("text", { x: margin.left - 14, y: yPosition + 6, class: "position-tick", "text-anchor": "end" });
      tick.textContent = formatNumber(value, 4);
      elements.chart.append(tick);
    }
    elements.chart.append(svg("line", { x1: margin.left, y1: margin.top + plotHeight, x2: margin.left + plotWidth, y2: margin.top + plotHeight, class: "position-axis" }));
    elements.chart.append(svg("line", { x1: margin.left, y1: margin.top, x2: margin.left, y2: margin.top + plotHeight, class: "position-axis" }));
    const xTitle = svg("text", { x: margin.left + plotWidth / 2, y: height - 20, class: "position-axis-title", "text-anchor": "middle" });
    xTitle.textContent = "DRY INPUTS → OPEN A BAR FOR CASE DETAIL";
    elements.chart.append(xTitle);
    const yTitle = svg("text", { x: 24, y: margin.top + plotHeight / 2, class: "position-axis-title", "text-anchor": "middle", transform: `rotate(-90 24 ${margin.top + plotHeight / 2})` });
    yTitle.textContent = "ESR · LOWER IS BETTER";
    elements.chart.append(yTitle);

    cases.forEach((item, index) => {
      const center = margin.left + slot * (index + 0.5);
      if (item.esr !== null) {
        const bar = svg("rect", {
          x: center - barWidth / 2,
          y: y(item.esr),
          width: barWidth,
          height: Math.max(1, margin.top + plotHeight - y(item.esr)),
          rx: 3,
          class: "position-bar",
          tabindex: 0,
          role: "link",
          "aria-label": `${item.dry}, model ESR ${formatNumber(item.esr)}, NAM ESR ${formatNumber(item.nam)}`,
        });
        const copy = `${item.dry} · model ESR ${formatNumber(item.esr)} · NAM-A2-FULL ${formatNumber(item.nam)}`;
        bar.addEventListener("mouseenter", (event) => chartTooltip(event, copy));
        bar.addEventListener("mousemove", (event) => chartTooltip(event, copy));
        bar.addEventListener("mouseleave", () => { if (elements.tooltip) elements.tooltip.hidden = true; });
        bar.addEventListener("focus", (event) => chartTooltip(event, copy));
        bar.addEventListener("blur", () => { if (elements.tooltip) elements.tooltip.hidden = true; });
        bar.addEventListener("click", () => window.location.assign(item.url));
        bar.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") window.location.assign(item.url); });
        elements.chart.append(bar);
      }
      if (item.nam !== null) elements.chart.append(svg("circle", { cx: center, cy: y(item.nam), r: 6, class: "position-nam-marker" }));
      const labelEvery = Math.max(1, Math.ceil(cases.length / 14));
      if (index % labelEvery === 0) {
        const label = svg("text", { x: center, y: margin.top + plotHeight + 28, class: "position-tick", "text-anchor": "middle" });
        label.textContent = String(item.index).padStart(3, "0");
        elements.chart.append(label);
      }
    });
  }

  function renderCases(payload) {
    elements.caseBody?.replaceChildren();
    const cases = [...(payload.cases || [])].sort((left, right) => left.index - right.index);
    if (elements.casesEmpty) elements.casesEmpty.hidden = cases.length > 0;
    for (const item of cases) {
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
      row.append(element("td", "", titleCase(item.status)));
      row.append(element("td", "", formatNumber(item.metrics?.esr)));
      row.append(element("td", "", formatNumber(item.metrics?.human_weighted_esr)));
      row.append(element("td", "", formatNumber(item.metrics?.mrstft)));
      const realtime = finite(item.metrics?.realtime_x);
      row.append(element("td", "", realtime === null ? "—" : `${formatNumber(realtime, 2)}×`));
      const action = element("td");
      const inspect = element("a", "report-inline-link", "Inspect →");
      inspect.href = item.url;
      action.append(inspect);
      row.append(action);
      elements.caseBody?.append(row);
    }
  }

  function render(payload) {
    state.payload = payload;
    renderIdentity(payload);
    renderInsights(payload);
    renderDistribution(payload);
    renderNeighborhood(payload);
    drawCaseChart(payload);
    renderCases(payload);
    show("content");
  }

  function scheduleRefresh(payload) {
    if (state.refreshTimer !== null) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
    if (!TERMINAL.has(text(payload?.run?.status).toLowerCase())) state.refreshTimer = window.setTimeout(() => load(false), REFRESH_MS);
  }

  async function load(initial = true) {
    const serial = ++state.requestSerial;
    if (initial) show("loading");
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(state.runId)}/positions/${encodeURIComponent(state.positionId)}`, { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
      const payload = await response.json();
      if (serial !== state.requestSerial) return;
      render(payload);
      scheduleRefresh(payload);
    } catch (error) {
      if (serial !== state.requestSerial) return;
      if (initial || !state.payload) show("error", error instanceof Error ? error.message : "The position report could not be loaded.");
      else scheduleRefresh(state.payload);
    }
  }

  elements.retry?.addEventListener("click", () => load(true));
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
