(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const POLL_INTERVAL_MS = 2_000;

  const elements = {
    ampFilter: document.querySelector("#amp-filter"),
    body: document.querySelector("#leaderboard-body"),
    chart: document.querySelector("#pareto-chart"),
    clearFilters: document.querySelector("#clear-filters"),
    connection: document.querySelector(".live-indicator"),
    connectionLabel: document.querySelector("#connection-label"),
    creatorFilter: document.querySelector("#creator-filter"),
    initialData: document.querySelector("#leaderboard-initial-data"),
    modelFilter: document.querySelector("#model-filter"),
    refreshStatus: document.querySelector("#refresh-status"),
    resultCount: document.querySelector("#result-count"),
    summaryCompleted: document.querySelector("#summary-completed-count"),
    summaryRuns: document.querySelector("#summary-run-count"),
    tooltip: document.querySelector("#chart-tooltip"),
  };

  if (!elements.body || !elements.chart) {
    return;
  }

  const state = {
    amps: [],
    runs: [],
    sortKey: "esr",
    sortDirection: "ascending",
    requestInFlight: false,
  };

  function firstValue(...values) {
    return values.find((value) => value !== undefined && value !== null && value !== "");
  }

  function finite(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function text(value, fallback = "") {
    const selected = firstValue(value, fallback);
    return selected === undefined || selected === null ? "" : String(selected);
  }

  function metric(raw, snakeName, camelName = snakeName) {
    const metrics = raw && typeof raw.metrics === "object" && raw.metrics ? raw.metrics : {};
    const candidate = firstValue(metrics[snakeName], metrics[camelName], raw[snakeName], raw[camelName]);

    if (typeof candidate === "number" || typeof candidate === "string") {
      return { mean: finite(candidate), p90: null, worst: null, best: null };
    }

    const values = candidate && typeof candidate === "object" ? candidate : {};
    return {
      mean: finite(firstValue(values.mean, values.average, raw[`${snakeName}_mean`])),
      p90: finite(firstValue(values.p90, values.percentile90, raw[`${snakeName}_p90`])),
      worst: finite(firstValue(values.worst, values.max, raw[`${snakeName}_worst`])),
      best: finite(firstValue(values.best, values.min, raw[`${snakeName}_best`])),
    };
  }

  function normalizeRun(raw, index) {
    const source = raw && typeof raw === "object" ? raw : {};
    const esr = metric(source, "esr");
    return {
      id: text(firstValue(source.id, source.run_id, source.runId), `run-${index}`),
      name: text(firstValue(source.name, source.model_name, source.modelName), "Untitled model"),
      creator: text(source.creator, "Anonymous"),
      ampId: text(firstValue(source.amp_id, source.ampId)),
      ampName: text(firstValue(source.amp_name, source.ampName, source.amp_id, source.ampId), "Unknown amp"),
      ampType: text(firstValue(source.amp_type, source.ampType), "Unspecified"),
      positions: finite(firstValue(source.unique_positions_used, source.uniquePositionsUsed)),
      audioDuration: finite(firstValue(source.audio_duration_sum, source.audioDurationSum)),
      turns: finite(source.turns),
      trainingTime: finite(firstValue(source.training_time, source.trainingTime)),
      description: text(source.description),
      parameterCount: finite(firstValue(source.parameter_count, source.parameterCount, source.params_count)),
      status: text(source.status, "queued").toLowerCase(),
      totalCases: finite(firstValue(source.total_cases, source.totalCases, source.case_count)) ?? 0,
      completedCases: finite(firstValue(source.completed_cases, source.completedCases)) ?? 0,
      esr,
      humanWeightedEsr: metric(source, "human_weighted_esr", "humanWeightedEsr"),
      mrstft: metric(source, "mrstft"),
      realtime: metric(source, "realtime_x", "realtimeX"),
      createdAt: text(firstValue(source.created_at, source.createdAt)),
    };
  }

  function runsFromPayload(payload) {
    const values = Array.isArray(payload)
      ? payload
      : firstValue(payload?.runs, payload?.items, payload?.leaderboard, payload?.data);
    return Array.isArray(values) ? values.map(normalizeRun) : [];
  }

  function ampsFromPayload(payload, runs) {
    const supplied = Array.isArray(payload?.amps) ? payload.amps : [];
    const values = supplied.length
      ? supplied.map((amp) => ({
        id: text(amp?.id),
        name: text(firstValue(amp?.name, amp?.id), "Unknown amp"),
      }))
      : runs.map((run) => ({ id: run.ampId, name: run.ampName }));
    const unique = new Map();
    for (const amp of values) {
      if (amp.id) unique.set(amp.id, amp);
    }
    return [...unique.values()].sort((left, right) => (
      left.name.localeCompare(right.name, undefined, { sensitivity: "base" })
      || left.id.localeCompare(right.id)
    ));
  }

  function parseInitialData() {
    if (!elements.initialData) {
      return { amps: [], runs: [] };
    }
    try {
      const payload = JSON.parse(elements.initialData.textContent || "{}");
      const runs = runsFromPayload(payload);
      return { amps: ampsFromPayload(payload, runs), runs };
    } catch (error) {
      console.warn("Could not read the server-rendered leaderboard snapshot.", error);
      return { amps: [], runs: [] };
    }
  }

  function createElement(tag, className, content) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (content !== undefined && content !== null) {
      node.textContent = String(content);
    }
    return node;
  }

  function createSvg(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) {
      node.setAttribute(name, String(value));
    }
    return node;
  }

  function formatScore(value) {
    if (value === null) {
      return "—";
    }
    const absolute = Math.abs(value);
    if (absolute !== 0 && (absolute < 0.001 || absolute >= 10_000)) {
      return value.toExponential(2);
    }
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

  function formatCompact(value) {
    if (value === null) {
      return "—";
    }
    return Intl.NumberFormat(undefined, { maximumFractionDigits: 1, notation: "compact" }).format(value);
  }

  function formatDuration(seconds) {
    if (seconds === null) {
      return "—";
    }
    if (seconds < 60) {
      return `${formatScore(seconds)}s`;
    }
    if (seconds < 3_600) {
      return `${formatScore(seconds / 60)}m`;
    }
    return `${formatScore(seconds / 3_600)}h`;
  }

  function titleCase(value) {
    return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function appendMetadata(container, label, value) {
    if (value === null || value === "") {
      return;
    }
    const wrapper = createElement("div");
    wrapper.append(createElement("dt", "", label), createElement("dd", "", value));
    container.append(wrapper);
  }

  function modelCell(run) {
    const cell = createElement("td", "model-cell");
    cell.dataset.label = "Model";
    const heading = createElement("strong");
    const link = createElement("a", "model-link", run.name);
    link.href = `/runs/${encodeURIComponent(run.id)}`;
    heading.append(link);
    cell.append(heading, createElement("span", "", `by ${run.creator}`));
    if (run.description) {
      cell.append(createElement("p", "", run.description));
    }

    const metadata = createElement("dl", "model-metadata");
    appendMetadata(metadata, "Params", formatCompact(run.parameterCount));
    appendMetadata(metadata, "Audio", formatDuration(run.audioDuration));
    appendMetadata(metadata, "Turns", run.turns === null ? "—" : formatScore(run.turns));
    appendMetadata(metadata, "Training", formatDuration(run.trainingTime));
    cell.append(metadata);
    return cell;
  }

  function ampCell(run) {
    const cell = createElement("td");
    cell.dataset.label = "Amp";
    cell.append(createElement("strong", "", run.ampName), createElement("span", "cell-secondary", run.ampType));
    return cell;
  }

  function progressCell(run) {
    const cell = createElement("td");
    cell.dataset.label = "Progress";
    const statusClass = run.status.replace(/[^a-z0-9-]+/g, "-");
    cell.append(createElement("span", `status status-${statusClass}`, titleCase(run.status)));

    const progress = createElement("progress");
    progress.max = Math.max(1, run.totalCases);
    progress.value = Math.min(run.completedCases, progress.max);
    progress.setAttribute("aria-label", `${run.completedCases} of ${run.totalCases} cases processed`);
    cell.append(progress, createElement("span", "cell-secondary", `${run.completedCases} / ${run.totalCases}`));
    return cell;
  }

  function simpleCell(label, value, className = "") {
    const cell = createElement("td", className, value);
    cell.dataset.label = label;
    return cell;
  }

  function metricCell(label, summary) {
    const cell = createElement("td");
    cell.dataset.label = label;
    const wrapper = createElement("div", "metric-summary");
    wrapper.append(createElement("strong", `metric-primary${summary.mean === null ? " metric-empty" : ""}`, formatScore(summary.mean)));

    const details = createElement("dl", "metric-details");
    for (const [name, value] of [["P90", summary.p90], ["Worst", summary.worst], ["Best", summary.best]]) {
      const item = createElement("div");
      item.append(createElement("dt", "", name), createElement("dd", value === null ? "metric-empty" : "", formatScore(value)));
      details.append(item);
    }
    wrapper.append(details);
    cell.append(wrapper);
    return cell;
  }

  function rankMap(runs) {
    const ranked = runs
      .filter((run) => run.esr.mean !== null)
      .sort((left, right) => compareNullable(left.esr.mean, right.esr.mean, 1));
    return new Map(ranked.map((run, index) => [run.id, index + 1]));
  }

  function sortValue(run, key, ranks) {
    const values = {
      amp: `${run.ampName}\u0000${run.ampId}`,
      esr: run.esr.mean,
      humanWeightedEsr: run.humanWeightedEsr.mean,
      mrstft: run.mrstft.mean,
      name: run.name,
      positions: run.positions,
      rank: ranks.get(run.id) ?? null,
      realtime: run.realtime.mean,
      status: run.totalCases > 0 ? run.completedCases / run.totalCases : 0,
    };
    return values[key];
  }

  function compareNullable(left, right, direction) {
    const leftMissing = left === null || left === undefined || left === "";
    const rightMissing = right === null || right === undefined || right === "";
    if (leftMissing && rightMissing) return 0;
    if (leftMissing) return 1;
    if (rightMissing) return -1;
    if (typeof left === "string" || typeof right === "string") {
      return text(left).localeCompare(text(right), undefined, { numeric: true, sensitivity: "base" }) * direction;
    }
    return (Number(left) - Number(right)) * direction;
  }

  function selectedRuns() {
    const ampId = elements.ampFilter?.value || "";
    const creator = elements.creatorFilter?.value || "";
    const search = (elements.modelFilter?.value || "").trim().toLocaleLowerCase();
    return state.runs.filter((run) => {
      const searchable = `${run.name} ${run.description} ${run.creator}`.toLocaleLowerCase();
      return (!ampId || run.ampId === ampId)
        && (!creator || run.creator === creator)
        && (!search || searchable.includes(search));
    });
  }

  function sortedRuns(runs, ranks) {
    const direction = state.sortDirection === "ascending" ? 1 : -1;
    return [...runs].sort((left, right) => {
      const comparison = compareNullable(
        sortValue(left, state.sortKey, ranks),
        sortValue(right, state.sortKey, ranks),
        direction,
      );
      return comparison || left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
    });
  }

  function renderTable(runs) {
    elements.body.replaceChildren();
    if (runs.length === 0) {
      const row = createElement("tr", "empty-row");
      const cell = createElement("td");
      cell.colSpan = 9;
      cell.append(
        createElement("strong", "", state.runs.length ? "No runs match these filters" : "No benchmark runs yet"),
        createElement("span", "", state.runs.length
          ? "Try a different amp, creator, or model name."
          : "Start a local benchmark and its progress will appear here."),
      );
      row.append(cell);
      elements.body.append(row);
      return;
    }

    const ranks = rankMap(state.runs);
    for (const run of sortedRuns(runs, ranks)) {
      const row = createElement("tr");
      row.dataset.runId = run.id;
      row.append(
        simpleCell("Rank", ranks.get(run.id) ?? "—", "rank-cell"),
        modelCell(run),
        ampCell(run),
        progressCell(run),
        simpleCell("Positions", run.positions === null ? "—" : formatScore(run.positions), "numeric-cell"),
        simpleCell("Realtime", run.realtime.mean === null ? "—" : `${formatScore(run.realtime.mean)}×`, "numeric-cell"),
        metricCell("ESR", run.esr),
        metricCell("Human-weighted ESR", run.humanWeightedEsr),
        metricCell("MRSTFT", run.mrstft),
      );
      elements.body.append(row);
    }
  }

  function uniqueSorted(values) {
    return [...new Set(values.filter(Boolean))].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
  }

  function updateSelect(select, values, allLabel) {
    if (!select) return;
    const previous = select.value;
    const expected = ["", ...values];
    const current = [...select.options].map((option) => option.value);
    if (expected.length === current.length && expected.every((value, index) => value === current[index])) {
      return;
    }
    select.replaceChildren();
    const all = createElement("option", "", allLabel);
    all.value = "";
    select.append(all);
    for (const value of values) {
      const option = createElement("option", "", value);
      option.value = value;
      select.append(option);
    }
    select.value = values.includes(previous) ? previous : "";
  }

  function updateAmpSelect() {
    const select = elements.ampFilter;
    if (!select) return;
    const previous = select.value;
    const expected = [{ id: "", name: "All amps" }, ...state.amps];
    const current = [...select.options].map((option) => ({ id: option.value, name: option.textContent }));
    if (expected.length === current.length
      && expected.every((amp, index) => amp.id === current[index].id && amp.name === current[index].name)) {
      return;
    }
    select.replaceChildren();
    for (const amp of expected) {
      const option = createElement("option", "", amp.name);
      option.value = amp.id;
      select.append(option);
    }
    select.value = state.amps.some((amp) => amp.id === previous) ? previous : "";
  }

  function updateFilterOptions() {
    updateAmpSelect();
    updateSelect(elements.creatorFilter, uniqueSorted(state.runs.map((run) => run.creator)), "All creators");
  }

  function paretoFrontier(points) {
    const sorted = [...points].sort((left, right) => left.positions - right.positions || left.esr.mean - right.esr.mean);
    const frontier = [];
    let bestScore = Number.POSITIVE_INFINITY;
    for (const point of sorted) {
      if (point.esr.mean < bestScore) {
        frontier.push(point);
        bestScore = point.esr.mean;
      }
    }
    return frontier;
  }

  function shorten(value, length = 19) {
    return value.length > length ? `${value.slice(0, length - 1)}…` : value;
  }

  function positionTickStep(maximum) {
    const rough = Math.max(1, maximum) / 5;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const residual = rough / magnitude;
    const multiplier = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
    return Math.max(1, multiplier * magnitude);
  }

  function showTooltip(run, point) {
    if (!elements.tooltip) return;
    const chartBounds = elements.chart.getBoundingClientRect();
    const shellBounds = elements.chart.parentElement.getBoundingClientRect();
    const scaleX = chartBounds.width / 960;
    const scaleY = chartBounds.height / 430;
    elements.tooltip.replaceChildren(
      createElement("strong", "", run.name),
      document.createTextNode(`ESR ${formatScore(run.esr.mean)} \u00b7 ${formatScore(run.positions)} positions`),
    );
    elements.tooltip.style.left = `${chartBounds.left - shellBounds.left + point.x * scaleX}px`;
    elements.tooltip.style.top = `${chartBounds.top - shellBounds.top + point.y * scaleY}px`;
    elements.tooltip.hidden = false;
  }

  function hideTooltip() {
    if (elements.tooltip) elements.tooltip.hidden = true;
  }

  function renderChart(runs) {
    const chart = elements.chart;
    chart.replaceChildren();
    hideTooltip();
    const points = runs.filter((run) => run.positions !== null && run.esr.mean !== null);

    if (points.length === 0) {
      const placeholder = createSvg("text", { class: "chart-placeholder", x: 480, y: 215, "text-anchor": "middle" });
      placeholder.textContent = "Completed runs with scores will appear here";
      chart.append(placeholder);
      return;
    }

    const width = 960;
    const height = 430;
    const margin = { top: 35, right: 44, bottom: 62, left: 102 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const maxX = Math.max(...points.map((run) => run.positions));
    const maxY = Math.max(...points.map((run) => run.esr.mean));
    const xStep = positionTickStep(maxX);
    const xDomainMax = Math.max(1, Math.ceil((maxX + xStep * 0.3) / xStep) * xStep);
    const yDomainMax = Math.max(0.0001, maxY * 1.14);
    const xScale = (value) => margin.left + (value / xDomainMax) * innerWidth;
    const yScale = (value) => margin.top + innerHeight - (value / yDomainMax) * innerHeight;

    const defs = createSvg("defs");
    const gradient = createSvg("linearGradient", { id: "frontier-fill", x1: 0, x2: 0, y1: 0, y2: 1 });
    gradient.append(
      createSvg("stop", { offset: "0%", "stop-color": "#b7f34a", "stop-opacity": 0.16 }),
      createSvg("stop", { offset: "100%", "stop-color": "#b7f34a", "stop-opacity": 0 }),
    );
    defs.append(gradient);
    chart.append(defs);

    const grid = createSvg("g", { "aria-hidden": "true" });
    const xTickCount = Math.round(xDomainMax / xStep);
    for (let index = 0; index <= xTickCount; index += 1) {
      const ratio = index / xTickCount;
      const x = margin.left + ratio * innerWidth;
      grid.append(createSvg("line", { class: "grid-line", x1: x, x2: x, y1: margin.top, y2: margin.top + innerHeight }));

      const xLabel = createSvg("text", { class: "axis-text", x, y: margin.top + innerHeight + 25, "text-anchor": "middle" });
      xLabel.textContent = formatScore(xStep * index);
      grid.append(xLabel);
    }

    const yTickCount = 5;
    for (let index = 0; index <= yTickCount; index += 1) {
      const ratio = index / yTickCount;
      const y = margin.top + ratio * innerHeight;
      grid.append(createSvg("line", { class: "grid-line", x1: margin.left, x2: margin.left + innerWidth, y1: y, y2: y }));
      const yLabel = createSvg("text", { class: "axis-text", x: margin.left - 15, y: margin.top + innerHeight - ratio * innerHeight + 4, "text-anchor": "end" });
      yLabel.textContent = formatScore(yDomainMax * ratio);
      grid.append(yLabel);
    }
    grid.append(
      createSvg("line", { class: "axis-line", x1: margin.left, x2: margin.left + innerWidth, y1: margin.top + innerHeight, y2: margin.top + innerHeight }),
      createSvg("line", { class: "axis-line", x1: margin.left, x2: margin.left, y1: margin.top, y2: margin.top + innerHeight }),
    );
    chart.append(grid);

    const xTitle = createSvg("text", { class: "axis-title", x: margin.left + innerWidth / 2, y: height - 14, "text-anchor": "middle" });
    xTitle.textContent = "Unique positions used (lower is leaner)";
    const yTitle = createSvg("text", { class: "axis-title", transform: `translate(21 ${margin.top + innerHeight / 2}) rotate(-90)`, "text-anchor": "middle" });
    yTitle.textContent = "Mean ESR (lower is better)";
    chart.append(xTitle, yTitle);

    const frontier = paretoFrontier(points);
    const frontierCoordinates = frontier.map((run) => ({ x: xScale(run.positions), y: yScale(run.esr.mean) }));
    const frontierPath = frontierCoordinates.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
    const areaPath = `${frontierPath} L ${frontierCoordinates.at(-1).x} ${margin.top + innerHeight} L ${frontierCoordinates[0].x} ${margin.top + innerHeight} Z`;
    chart.append(
      createSvg("path", { class: "frontier-area", d: areaPath, "aria-hidden": "true" }),
      createSvg("path", { class: "frontier-line", d: frontierPath, "aria-hidden": "true" }),
    );

    const frontierValues = new Set(frontier.map((run) => `${run.positions}:${run.esr.mean}`));
    const pointLayer = createSvg("g");
    const shouldLabelAll = points.length <= 12;
    for (const run of points) {
      const position = { x: xScale(run.positions), y: yScale(run.esr.mean) };
      const onFrontier = frontierValues.has(`${run.positions}:${run.esr.mean}`);
      const circle = createSvg("circle", {
        class: `run-point${onFrontier ? " on-frontier" : ""}`,
        cx: position.x,
        cy: position.y,
        r: 6,
        tabindex: 0,
        role: "img",
        "aria-label": `${run.name}: mean ESR ${formatScore(run.esr.mean)}, ${formatScore(run.positions)} unique positions${onFrontier ? ", on the Pareto frontier" : ""}`,
      });
      const nativeTitle = createSvg("title");
      nativeTitle.textContent = `${run.name} — ESR ${formatScore(run.esr.mean)}, ${formatScore(run.positions)} positions`;
      circle.append(nativeTitle);
      circle.addEventListener("mouseenter", () => showTooltip(run, position));
      circle.addEventListener("focus", () => showTooltip(run, position));
      circle.addEventListener("mouseleave", hideTooltip);
      circle.addEventListener("blur", hideTooltip);
      pointLayer.append(circle);

      if (shouldLabelAll || onFrontier) {
        const label = createSvg("text", { class: "point-label", x: position.x + 10, y: position.y - 10 });
        label.textContent = shorten(run.name);
        pointLayer.append(label);
      }
    }
    chart.append(pointLayer);
  }

  function updateSortHeaders() {
    for (const button of document.querySelectorAll(".sort-button")) {
      const header = button.closest("th");
      const active = button.dataset.sort === state.sortKey;
      header.setAttribute("aria-sort", active ? state.sortDirection : "none");
      const indicator = button.querySelector("span");
      if (indicator) indicator.textContent = active ? (state.sortDirection === "ascending" ? "↑" : "↓") : "↕";
    }
  }

  function render() {
    const filtered = selectedRuns();
    renderTable(filtered);
    renderChart(filtered);
    updateSortHeaders();
    if (elements.resultCount) {
      elements.resultCount.textContent = `${filtered.length} ${filtered.length === 1 ? "run" : "runs"}${filtered.length !== state.runs.length ? ` of ${state.runs.length}` : ""}`;
    }
    if (elements.summaryRuns) elements.summaryRuns.textContent = String(state.runs.length);
    if (elements.summaryCompleted) {
      elements.summaryCompleted.textContent = String(state.runs.filter((run) => ["completed", "finished"].includes(run.status)).length);
    }
    if (elements.clearFilters) {
      elements.clearFilters.disabled = !elements.ampFilter?.value && !elements.creatorFilter?.value && !elements.modelFilter?.value;
    }
  }

  function setConnection(online) {
    elements.connection?.classList.toggle("is-offline", !online);
    if (elements.connectionLabel) elements.connectionLabel.textContent = online ? "Live" : "Reconnecting";
  }

  async function pollLeaderboard() {
    if (state.requestInFlight || document.hidden) {
      return;
    }
    state.requestInFlight = true;
    try {
      const response = await fetch("/api/v1/leaderboard", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`Leaderboard request failed with ${response.status}`);
      }
      const payload = await response.json();
      state.runs = runsFromPayload(payload);
      state.amps = ampsFromPayload(payload, state.runs);
      updateFilterOptions();
      render();
      setConnection(true);
      if (elements.refreshStatus) {
        elements.refreshStatus.textContent = `Updated ${new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`;
      }
    } catch (error) {
      setConnection(false);
      if (elements.refreshStatus) elements.refreshStatus.textContent = "Live update unavailable · retrying";
      console.warn("Leaderboard refresh failed.", error);
    } finally {
      state.requestInFlight = false;
    }
  }

  for (const button of document.querySelectorAll(".sort-button")) {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (!key) return;
      if (key === state.sortKey) {
        state.sortDirection = state.sortDirection === "ascending" ? "descending" : "ascending";
      } else {
        state.sortKey = key;
        state.sortDirection = key === "realtime" ? "descending" : "ascending";
      }
      render();
    });
  }

  for (const input of [elements.ampFilter, elements.creatorFilter, elements.modelFilter]) {
    input?.addEventListener("input", render);
    input?.addEventListener("change", render);
  }

  elements.clearFilters?.addEventListener("click", () => {
    if (elements.ampFilter) elements.ampFilter.value = "";
    if (elements.creatorFilter) elements.creatorFilter.value = "";
    if (elements.modelFilter) elements.modelFilter.value = "";
    elements.modelFilter?.focus();
    render();
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void pollLeaderboard();
  });

  const initial = parseInitialData();
  state.runs = initial.runs;
  state.amps = initial.amps;
  updateFilterOptions();
  render();
  window.setInterval(() => void pollLeaderboard(), POLL_INTERVAL_MS);
})();
