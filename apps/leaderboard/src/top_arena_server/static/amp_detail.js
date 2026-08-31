(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const POLL_INTERVAL_MS = 5_000;
  const STARTED_AT_FORMATTER = new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    timeZoneName: "short",
    year: "numeric",
  });
  const elements = {
    main: document.querySelector(".amp-main"),
    initialData: document.querySelector("#amp-initial-data"),
    runCount: document.querySelector("#amp-run-count"),
    caseCount: document.querySelector("#amp-case-count"),
    standings: document.querySelector("#amp-standings"),
    standingsEmpty: document.querySelector("#standings-empty"),
    chart: document.querySelector("#amp-comparison-chart"),
    tooltip: document.querySelector("#amp-chart-tooltip"),
    chartTitle: document.querySelector("#comparison-title"),
    chartGuidance: document.querySelector("#comparison-guidance"),
    chartDescription: document.querySelector("#comparison-description"),
    filter: document.querySelector("#amp-model-filter"),
    tableBody: document.querySelector("#amp-model-body"),
    filterEmpty: document.querySelector("#model-filter-empty"),
    selectedSection: document.querySelector("#selected-model"),
    selectedTitle: document.querySelector("#selected-model-title"),
    selectedLink: document.querySelector("#selected-model-link"),
    selectedSummary: document.querySelector("#selected-summary"),
    selectedProfile: document.querySelector("#selected-profile"),
    pageEmpty: document.querySelector("#amp-empty-state"),
  };

  if (!elements.main || !elements.initialData || !elements.chart || !elements.tableBody) return;

  const state = {
    ampId: elements.main.dataset.ampId || "",
    runs: [],
    selectedId: null,
    chartMode: "speed",
    requestInFlight: false,
    runsSignature: null,
    chartSignature: null,
  };

  const chartModes = {
    speed: {
      title: "Quality vs speed",
      guidance: "Further right is faster · higher is lower error",
      axis: "Realtime (×)",
      value: (run) => run.realtime,
      format: (value) => `${formatNumber(value, 2)}×`,
    },
    positions: {
      title: "Quality vs positions",
      guidance: "Further left is leaner · higher is lower error",
      axis: "Unique amp positions",
      value: (run) => run.positions,
      format: (value) => formatInteger(value),
    },
    budget: {
      title: "Quality vs recording budget",
      guidance: "Further left uses less audio · higher is lower error",
      axis: "Recording budget (seconds)",
      value: (run) => run.audioDuration,
      format: (value) => `${formatNumber(value, 0)}s`,
    },
  };

  function firstValue(...values) {
    return values.find((value) => value !== undefined && value !== null && value !== "");
  }

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function text(value, fallback = "") {
    const selected = firstValue(value, fallback);
    return selected === undefined || selected === null ? "" : String(selected);
  }

  function metric(source, key, camelKey = key) {
    const metrics = source?.metrics && typeof source.metrics === "object" ? source.metrics : {};
    const candidate = firstValue(metrics[key], metrics[camelKey], source?.[key], source?.[camelKey]);
    if (typeof candidate === "number" || typeof candidate === "string") return finite(candidate);
    return finite(candidate?.mean);
  }

  function normalizeRun(raw, index) {
    const source = raw && typeof raw === "object" ? raw : {};
    const baseline = source.metrics?.nam_a2_full;
    const baselineSource = { metrics: baseline && typeof baseline === "object" ? baseline : {} };
    const ampParameterCount = finite(firstValue(source.amp_control_count, source.ampControlCount));
    const positions = finite(firstValue(source.unique_positions_used, source.uniquePositionsUsed));
    return {
      id: text(firstValue(source.id, source.run_id, source.runId), `run-${index}`),
      name: text(firstValue(source.name, source.model_name, source.modelName), "Untitled model"),
      creator: text(source.creator, "Anonymous"),
      status: text(source.status, "queued").toLowerCase(),
      completedCases: finite(firstValue(source.completed_cases, source.completedCases)) ?? 0,
      totalCases: finite(firstValue(source.total_cases, source.totalCases, source.case_count)) ?? 0,
      ampParameterCount,
      positions,
      positionsPerControl: positions !== null && ampParameterCount !== null && ampParameterCount > 0
        ? positions / ampParameterCount
        : null,
      createdAt: text(firstValue(source.created_at, source.createdAt)),
      audioDuration: finite(firstValue(source.audio_duration_sum, source.audioDurationSum)),
      esr: metric(source, "esr"),
      weighted: metric(source, "human_weighted_esr", "humanWeightedEsr"),
      mrstft: metric(source, "mrstft"),
      realtime: metric(source, "realtime_x", "realtimeX"),
      baseline: {
        esr: metric(baselineSource, "esr"),
        weighted: metric(baselineSource, "human_weighted_esr", "humanWeightedEsr"),
        mrstft: metric(baselineSource, "mrstft"),
      },
    };
  }

  function parsePayload(payload) {
    const rawRuns = Array.isArray(payload) ? payload : payload?.runs;
    return Array.isArray(rawRuns) ? rawRuns.map(normalizeRun) : [];
  }

  function parseInitialData() {
    try {
      return JSON.parse(elements.initialData.textContent || "{}");
    } catch (error) {
      console.warn("Could not read the amp results snapshot.", error);
      return { runs: [] };
    }
  }

  function createElement(tag, className = "", content = null) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== null && content !== undefined) node.textContent = String(content);
    return node;
  }

  function createSvg(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }

  function formatNumber(value, digits = 4) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
  }

  function formatInteger(value) {
    return value === null || value === undefined
      ? "—"
      : Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  function formatStartedAt(value) {
    const date = new Date(value);
    return value && !Number.isNaN(date.getTime()) ? STARTED_AT_FORMATTER.format(date) : "—";
  }

  function rankedRuns() {
    return state.runs
      .filter((run) => run.esr !== null)
      .sort((left, right) => left.esr - right.esr || left.name.localeCompare(right.name));
  }

  function rankFor(runId) {
    const index = rankedRuns().findIndex((run) => run.id === runId);
    return index < 0 ? null : index + 1;
  }

  function selectedRun() {
    return state.runs.find((run) => run.id === state.selectedId) || rankedRuns()[0] || state.runs[0] || null;
  }

  function setSelected(runId, { scroll = false } = {}) {
    if (!state.runs.some((run) => run.id === runId)) return;
    state.selectedId = runId;
    renderTable();
    renderSelection();
    renderChart();
    if (scroll) elements.selectedSection?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderStandings() {
    const ranked = rankedRuns().slice(0, 5);
    elements.standings.replaceChildren();
    elements.standingsEmpty.hidden = ranked.length > 0;
    if (!ranked.length) return;
    const best = ranked[0].esr;
    for (const [index, run] of ranked.entries()) {
      const item = createElement("li", "amp-standing");
      const rank = createElement("span", "amp-standing-rank", `#${index + 1}`);
      const name = createElement("a", "amp-standing-model amp-link", run.name);
      name.href = `/runs/${encodeURIComponent(run.id)}`;
      const track = createElement("span", "amp-standing-track");
      const bar = createElement("span", "amp-standing-bar");
      bar.style.width = `${Math.max(12, Math.min(100, (best / run.esr) * 100))}%`;
      track.append(bar);
      const score = createElement("span", "amp-standing-score", formatNumber(run.esr));
      score.append(createElement("small", "", "mean ESR"));
      item.append(rank, name, track, score);
      elements.standings.append(item);
    }
  }

  function filteredRuns() {
    const query = (elements.filter?.value || "").trim().toLocaleLowerCase();
    return state.runs.filter((run) => !query || `${run.name} ${run.creator}`.toLocaleLowerCase().includes(query));
  }

  function metricCell(label, value, suffix = "") {
    const cell = createElement("td", "numeric-cell", value === null ? "—" : `${formatNumber(value)}${suffix}`);
    cell.dataset.label = label;
    return cell;
  }

  function startedCell(value) {
    const cell = createElement("td", "timestamp-cell");
    cell.dataset.label = "Started";
    const timestamp = createElement("time", "", formatStartedAt(value));
    if (value) timestamp.dateTime = value;
    cell.append(timestamp);
    return cell;
  }

  function renderTable() {
    const runs = filteredRuns().sort((left, right) => {
      if (left.esr === null && right.esr === null) return left.name.localeCompare(right.name);
      if (left.esr === null) return 1;
      if (right.esr === null) return -1;
      return left.esr - right.esr || left.name.localeCompare(right.name);
    });
    elements.tableBody.replaceChildren();
    elements.filterEmpty.hidden = runs.length > 0 || state.runs.length === 0;
    for (const run of runs) {
      const row = createElement("tr", run.id === state.selectedId ? "is-selected" : "");
      row.dataset.runId = run.id;
      const modelCell = createElement("td", "amp-model-cell");
      modelCell.dataset.label = "Model";
      const select = createElement("button", "amp-run-select", run.name);
      select.type = "button";
      select.dataset.runId = run.id;
      select.setAttribute("aria-pressed", String(run.id === state.selectedId));
      select.addEventListener("click", () => setSelected(run.id));
      const byline = createElement("span", "amp-model-byline", `by ${run.creator} · `);
      const open = createElement("a", "amp-link", "open run ↗");
      open.href = `/runs/${encodeURIComponent(run.id)}`;
      byline.append(open);
      modelCell.append(select, byline);
      row.append(
        modelCell,
        metricCell("ESR", run.esr),
        metricCell("Weighted ESR", run.weighted),
        metricCell("MRSTFT", run.mrstft),
        metricCell("Realtime", run.realtime, run.realtime === null ? "" : "×"),
        metricCell("Positions", run.positions),
        metricCell("Amp parameters", run.ampParameterCount),
        metricCell("Positions per amp parameter", run.positionsPerControl),
        startedCell(run.createdAt),
        metricCell("Budget", run.audioDuration, run.audioDuration === null ? "" : "s"),
      );
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, button")) return;
        setSelected(run.id);
      });
      elements.tableBody.append(row);
    }
  }

  function summaryStat(label, value, context) {
    const item = createElement("dl", "selected-stat");
    item.append(createElement("dt", "", label));
    const detail = createElement("dd", "", value);
    detail.append(createElement("small", "", context));
    item.append(detail);
    return item;
  }

  function comparison(modelValue, baselineValue) {
    if (modelValue === null || baselineValue === null || baselineValue === 0) return null;
    return (1 - modelValue / baselineValue) * 100;
  }

  function renderProfile(run) {
    elements.selectedProfile.replaceChildren();
    const metrics = [
      ["ESR", run.esr, run.baseline.esr],
      ["Weighted ESR", run.weighted, run.baseline.weighted],
      ["MRSTFT", run.mrstft, run.baseline.mrstft],
    ];
    for (const [label, modelValue, baselineValue] of metrics) {
      const row = createElement("div", "profile-row");
      row.append(createElement("span", "profile-name", label));
      const track = createElement("span", "profile-track");
      if (modelValue !== null || baselineValue !== null) {
        const maximum = Math.max(modelValue ?? 0, baselineValue ?? 0, 0.0001) * 1.2;
        const bar = createElement("span", "profile-bar");
        bar.style.width = `${Math.min(100, ((modelValue ?? 0) / maximum) * 100)}%`;
        const marker = createElement("span", "profile-baseline");
        marker.style.left = `${Math.min(100, ((baselineValue ?? 0) / maximum) * 100)}%`;
        track.append(bar, marker);
      }
      const value = createElement("span", "profile-value", formatNumber(modelValue));
      const delta = comparison(modelValue, baselineValue);
      if (delta !== null) {
        value.append(createElement("small", delta < 0 ? "is-worse" : "", `${Math.abs(delta).toFixed(1)}% ${delta >= 0 ? "lower" : "higher"} than NAM`));
      }
      row.append(track, value);
      elements.selectedProfile.append(row);
    }
  }

  function renderSelection() {
    const run = selectedRun();
    elements.selectedSection.hidden = !run;
    if (!run) return;
    state.selectedId = run.id;
    const esrDelta = comparison(run.esr, run.baseline.esr);
    const rank = rankFor(run.id);
    elements.selectedTitle.textContent = run.name;
    elements.selectedLink.href = `/runs/${encodeURIComponent(run.id)}`;
    elements.selectedSummary.replaceChildren(
      summaryStat("Amp rank", rank === null ? "—" : `#${rank} of ${rankedRuns().length}`, "by mean ESR"),
      summaryStat("Mean ESR", formatNumber(run.esr), esrDelta === null ? "comparison unavailable" : `${Math.abs(esrDelta).toFixed(1)}% vs NAM-A2-FULL`),
      summaryStat("Realtime", run.realtime === null ? "—" : `${formatNumber(run.realtime, 2)}×`, "higher is faster"),
      summaryStat("Cases", `${formatInteger(run.completedCases)} / ${formatInteger(run.totalCases)}`, run.status.replace(/[_-]+/g, " ")),
    );
    renderProfile(run);
  }

  function chartDomain(values) {
    const maximum = Math.max(...values, 1);
    return [0, maximum * 1.08];
  }

  function showTooltip(run, point) {
    if (!elements.tooltip) return;
    const chartBounds = elements.chart.getBoundingClientRect();
    const shellBounds = elements.chart.parentElement.getBoundingClientRect();
    elements.tooltip.replaceChildren(
      createElement("strong", "", run.name),
      document.createTextNode(`ESR ${formatNumber(run.esr)} · ${chartModes[state.chartMode].format(chartModes[state.chartMode].value(run))}`),
    );
    elements.tooltip.style.left = `${chartBounds.left - shellBounds.left + (point.x / 1120) * chartBounds.width}px`;
    elements.tooltip.style.top = `${chartBounds.top - shellBounds.top + (point.y / 430) * chartBounds.height}px`;
    elements.tooltip.hidden = false;
  }

  function hideTooltip() {
    if (elements.tooltip) elements.tooltip.hidden = true;
  }

  function renderChart() {
    const mode = chartModes[state.chartMode];
    elements.chartTitle.textContent = mode.title;
    elements.chartGuidance.textContent = mode.guidance;
    elements.chartDescription.textContent = `${mode.title} for this amp. Mean ESR is lower when a point is higher on the chart.`;
    const runs = state.runs.filter((run) => run.esr !== null && mode.value(run) !== null);
    const signature = JSON.stringify({
      mode: state.chartMode,
      selectedId: state.selectedId,
      runs: runs.map((run) => [run.id, run.name, run.esr, mode.value(run)]),
    });
    if (signature === state.chartSignature) return;
    state.chartSignature = signature;
    elements.chart.replaceChildren();
    hideTooltip();
    if (!runs.length) {
      const empty = createSvg("text", { x: 560, y: 215, "text-anchor": "middle" });
      empty.textContent = "Completed runs with scores will appear here";
      elements.chart.append(empty);
      return;
    }

    const width = 1120;
    const height = 430;
    const margin = { top: 35, right: 45, bottom: 62, left: 86 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const xValues = runs.map(mode.value);
    const [xMin, xMax] = chartDomain(xValues);
    const rawYMin = Math.min(...runs.map((run) => run.esr));
    const rawYMax = Math.max(...runs.map((run) => run.esr));
    const yPadding = Math.max((rawYMax - rawYMin) * 0.18, rawYMax * 0.08, 0.001);
    const yMin = Math.max(0, rawYMin - yPadding);
    const yMax = rawYMax + yPadding;
    const xScale = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * innerWidth;
    const yScale = (value) => margin.top + ((value - yMin) / (yMax - yMin)) * innerHeight;
    const grid = createSvg("g", { "aria-hidden": "true" });
    for (let index = 0; index <= 5; index += 1) {
      const ratio = index / 5;
      const x = margin.left + ratio * innerWidth;
      const value = xMin + ratio * (xMax - xMin);
      grid.append(createSvg("line", { class: "amp-grid-line", x1: x, x2: x, y1: margin.top, y2: margin.top + innerHeight }));
      const label = createSvg("text", { x, y: height - 28, "text-anchor": "middle" });
      label.textContent = mode.format(value);
      grid.append(label);
    }
    for (let index = 0; index <= 4; index += 1) {
      const ratio = index / 4;
      const y = margin.top + ratio * innerHeight;
      const value = yMin + ratio * (yMax - yMin);
      grid.append(createSvg("line", { class: "amp-grid-line", x1: margin.left, x2: margin.left + innerWidth, y1: y, y2: y }));
      const label = createSvg("text", { x: margin.left - 12, y: y + 4, "text-anchor": "end" });
      label.textContent = formatNumber(value);
      grid.append(label);
    }
    grid.append(
      createSvg("line", { class: "amp-axis-line", x1: margin.left, x2: margin.left + innerWidth, y1: margin.top + innerHeight, y2: margin.top + innerHeight }),
      createSvg("line", { class: "amp-axis-line", x1: margin.left, x2: margin.left, y1: margin.top, y2: margin.top + innerHeight }),
    );
    elements.chart.append(grid);
    const xTitle = createSvg("text", { class: "amp-axis-title", x: margin.left + innerWidth / 2, y: height - 6, "text-anchor": "middle" });
    xTitle.textContent = mode.axis;
    const yTitle = createSvg("text", { class: "amp-axis-title", transform: `translate(18 ${margin.top + innerHeight / 2}) rotate(-90)`, "text-anchor": "middle" });
    yTitle.textContent = "Mean ESR (lower is better)";
    elements.chart.append(xTitle, yTitle);

    for (const [index, run] of runs.entries()) {
      const point = { x: xScale(mode.value(run)), y: yScale(run.esr) };
      const circle = createSvg("circle", {
        class: `amp-run-point${run.id === state.selectedId ? " is-selected" : ""}`,
        cx: point.x,
        cy: point.y,
        r: run.id === state.selectedId ? 8 : 6,
        tabindex: 0,
        role: "button",
        "aria-label": `${run.name}: mean ESR ${formatNumber(run.esr)}, ${mode.format(mode.value(run))}. Select this model.`,
      });
      circle.addEventListener("mouseenter", () => showTooltip(run, point));
      circle.addEventListener("focus", () => showTooltip(run, point));
      circle.addEventListener("mouseleave", hideTooltip);
      circle.addEventListener("blur", hideTooltip);
      circle.addEventListener("click", () => setSelected(run.id));
      circle.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setSelected(run.id);
        }
      });
      elements.chart.append(circle);
      const labelOnLeft = point.x > width - margin.right - 250;
      const label = createSvg("text", {
        class: `amp-point-label${run.id === state.selectedId ? " is-selected" : ""}`,
        x: point.x + (labelOnLeft ? -11 : 11),
        y: point.y + (index % 2 === 0 ? -11 : 19),
        "text-anchor": labelOnLeft ? "end" : "start",
      });
      label.textContent = run.name.length > 32 ? `${run.name.slice(0, 31)}…` : run.name;
      elements.chart.append(label);
    }
  }

  function render() {
    if (state.runs.length && !state.runs.some((run) => run.id === state.selectedId)) {
      state.selectedId = rankedRuns()[0]?.id || state.runs[0].id;
    }
    elements.runCount.textContent = `${state.runs.length} ${state.runs.length === 1 ? "model run" : "model runs"}`;
    const caseCounts = state.runs.map((run) => run.totalCases).filter((value) => value > 0);
    elements.caseCount.textContent = caseCounts.length ? `${Math.max(...caseCounts)} cases` : "— cases";
    elements.pageEmpty.hidden = state.runs.length > 0;
    elements.selectedSection.hidden = state.runs.length === 0;
    renderStandings();
    renderTable();
    renderSelection();
    renderChart();
  }

  async function refresh() {
    if (state.requestInFlight || document.hidden) return;
    state.requestInFlight = true;
    try {
      const response = await fetch(`/api/v1/leaderboard?amp_id=${encodeURIComponent(state.ampId)}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`Amp results request failed with ${response.status}`);
      const runs = parsePayload(await response.json());
      const signature = JSON.stringify(runs);
      if (signature !== state.runsSignature) {
        state.runs = runs;
        state.runsSignature = signature;
        render();
      }
    } catch (error) {
      console.warn("Could not refresh amp results.", error);
    } finally {
      state.requestInFlight = false;
    }
  }

  elements.filter?.addEventListener("input", renderTable);
  document.querySelectorAll(".comparison-tabs [role='tab']").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.chartMode = tab.dataset.chartMode;
      document.querySelectorAll(".comparison-tabs [role='tab']").forEach((button) => {
        const active = button === tab;
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
      });
      renderChart();
    });
    tab.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      const tabs = [...document.querySelectorAll(".comparison-tabs [role='tab']")];
      const offset = event.key === "ArrowRight" ? 1 : -1;
      tabs[(tabs.indexOf(tab) + offset + tabs.length) % tabs.length].click();
      tabs[(tabs.indexOf(tab) + offset + tabs.length) % tabs.length].focus();
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refresh();
  });

  state.runs = parsePayload(parseInitialData());
  state.runsSignature = JSON.stringify(state.runs);
  render();
  window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
})();
