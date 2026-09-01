(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const POLL_INTERVAL_MS = 2_000;
  const SEARCH_DEBOUNCE_MS = 250;
  const PAGE_SIZE = 25;
  const DEFAULT_AMP_SCOPE = "normal";
  const SIMPLE_AMP_IDS = new Set(["blackface63-simple"]);
  const SORT_KEYS = new Set([
    "rank",
    "name",
    "amp",
    "status",
    "positions",
    "ampParameters",
    "positionsPerControl",
    "started",
    "realtime",
    "esr",
    "humanWeightedEsr",
    "mrstft",
  ]);

  const elements = {
    ampFilter: document.querySelector("#amp-filter"),
    ampScopeFilter: document.querySelector("#amp-scope-filter"),
    body: document.querySelector("#leaderboard-body"),
    chart: document.querySelector("#pareto-chart"),
    clearFilters: document.querySelector("#clear-filters"),
    connection: document.querySelector(".live-indicator"),
    connectionLabel: document.querySelector("#connection-label"),
    creatorFilter: document.querySelector("#creator-filter"),
    initialData: document.querySelector("#leaderboard-initial-data"),
    modelFilter: document.querySelector("#model-filter"),
    pageCurrent: document.querySelector("#current-page"),
    pageNext: document.querySelector("#page-next"),
    pagePrevious: document.querySelector("#page-previous"),
    pageTotal: document.querySelector("#total-pages"),
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
    chartRuns: [],
    creators: [],
    runs: [],
    runRanks: new Map(),
    sortKey: "esr",
    sortDirection: "ascending",
    page: 1,
    pageSize: PAGE_SIZE,
    totalRuns: 0,
    totalPages: 1,
    serverPaginated: false,
    requestInFlight: false,
    reloadPending: false,
    dataSignature: null,
    chartSignature: null,
  };
  let searchTimer = null;

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
    const namMetrics = source.metrics?.nam_a2_full;
    const namSource = { metrics: namMetrics && typeof namMetrics === "object" ? namMetrics : {} };
    const ampParameterCount = finite(firstValue(source.amp_control_count, source.ampControlCount));
    const positions = finite(firstValue(source.unique_positions_used, source.uniquePositionsUsed));
    return {
      id: text(firstValue(source.id, source.run_id, source.runId), `run-${index}`),
      name: text(firstValue(source.name, source.model_name, source.modelName), "Untitled model"),
      creator: text(source.creator, "Anonymous"),
      ampId: text(firstValue(source.amp_id, source.ampId)),
      ampName: text(firstValue(source.amp_name, source.ampName, source.amp_id, source.ampId), "Unknown amp"),
      ampType: text(firstValue(source.amp_type, source.ampType), "Unspecified"),
      ampParameterCount,
      positions,
      positionsPerControl: positions === null || ampParameterCount === null || ampParameterCount <= 0
        ? null
        : positions / ampParameterCount,
      audioDuration: finite(firstValue(source.audio_duration_sum, source.audioDurationSum)),
      turns: finite(source.turns),
      trainingTime: finite(firstValue(source.training_time, source.trainingTime)),
      description: text(source.description),
      status: text(source.status, "queued").toLowerCase(),
      totalCases: finite(firstValue(source.total_cases, source.totalCases, source.case_count)) ?? 0,
      completedCases: finite(firstValue(source.completed_cases, source.completedCases)) ?? 0,
      esr,
      humanWeightedEsr: metric(source, "human_weighted_esr", "humanWeightedEsr"),
      mrstft: metric(source, "mrstft"),
      realtime: metric(source, "realtime_x", "realtimeX"),
      namA2Full: {
        esr: metric(namSource, "esr"),
        humanWeightedEsr: metric(namSource, "human_weighted_esr", "humanWeightedEsr"),
        mrstft: metric(namSource, "mrstft"),
      },
      createdAt: text(firstValue(source.created_at, source.createdAt)),
    };
  }

  function runsFromPayload(payload) {
    const values = Array.isArray(payload)
      ? payload
      : firstValue(payload?.runs, payload?.items, payload?.leaderboard, payload?.data);
    return Array.isArray(values) ? values.map(normalizeRun) : [];
  }

  function chartRunsFromPayload(payload, fallbackRuns) {
    if (!Array.isArray(payload?.chart_runs)) return fallbackRuns;
    return payload.chart_runs.map((run, index) => normalizeRun({
      ...run,
      metrics: { esr: { mean: run?.esr } },
    }, index));
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
      return { amps: [], chartRuns: [], creators: [], runs: [] };
    }
    try {
      const payload = JSON.parse(elements.initialData.textContent || "{}");
      const runs = runsFromPayload(payload);
      const totalRuns = finite(payload?.total_runs);
      return {
        amps: ampsFromPayload(payload, runs),
        chartRuns: chartRunsFromPayload(payload, runs),
        creators: Array.isArray(payload?.creators) ? payload.creators.map(String) : [],
        runs,
        runRanks: new Map(Object.entries(payload?.run_ranks || {})),
        page: finite(payload?.page) ?? 1,
        pageSize: finite(payload?.page_size) ?? PAGE_SIZE,
        totalRuns: totalRuns ?? runs.length,
        totalPages: finite(payload?.total_pages) ?? 1,
        serverPaginated: totalRuns !== null,
      };
    } catch (error) {
      console.warn("Could not read the server-rendered leaderboard snapshot.", error);
      return { amps: [], chartRuns: [], creators: [], runs: [] };
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

  function formatInteger(value) {
    return value === null
      ? "—"
      : value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  function formatStartedAt(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "—";
    const pad = (part) => String(part).padStart(2, "0");
    return `${pad(date.getUTCDate())}.${pad(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  }

  function titleCase(value) {
    return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function modelCell(run) {
    const cell = createElement("td", "model-cell");
    cell.dataset.label = "Model";
    const heading = createElement("strong");
    const link = createElement("a", "model-link", run.name);
    link.href = `/runs/${encodeURIComponent(run.id)}`;
    heading.append(link);
    cell.append(heading, createElement("span", "", `by ${run.creator}`));
    return cell;
  }

  function ampCell(run) {
    const cell = createElement("td");
    cell.dataset.label = "Amp";
    const heading = createElement("strong");
    const link = createElement("a", "amp-link", run.ampName);
    link.href = `/amps/${encodeURIComponent(run.ampId)}`;
    heading.append(link);
    cell.append(heading);
    return cell;
  }

  function progressCell(run) {
    const cell = createElement("td");
    cell.dataset.label = "Progress";
    const statusClass = run.status.replace(/[^a-z0-9-]+/g, "-");
    cell.append(createElement("span", `status status-${statusClass}`, titleCase(run.status)));
    cell.append(createElement("span", "progress-count", `${run.completedCases}/${run.totalCases}`));
    return cell;
  }

  function simpleCell(label, value, className = "") {
    const cell = createElement("td", className, value);
    cell.dataset.label = label;
    return cell;
  }

  function startedCell(value) {
    const cell = createElement("td", "timestamp-cell");
    cell.dataset.label = "Started (UTC)";
    const timestamp = createElement("time", "", formatStartedAt(value));
    if (value) timestamp.dateTime = value;
    cell.append(timestamp);
    return cell;
  }

  function comparisonLabel(modelValue, baselineValue, higherIsBetter = false) {
    if (modelValue === null || baselineValue === null) {
      return { label: "—", title: "NAM-A2-FULL comparison unavailable.", className: "" };
    }
    if (modelValue === baselineValue) {
      return {
        label: "0.0% =",
        title: `Equal to NAM-A2-FULL ${formatScore(baselineValue)}.`,
        className: "is-equal",
      };
    }
    const difference = modelValue - baselineValue;
    const modelBetter = higherIsBetter ? difference > 0 : difference < 0;
    const direction = difference < 0 ? "lower" : "higher";
    const arrow = difference < 0 ? "▼" : "▲";
    const percentage = baselineValue === 0
      ? null
      : (Math.abs(difference) / Math.abs(baselineValue)) * 100;
    const label = percentage === null ? arrow : `${percentage.toFixed(1)}% ${arrow}`;
    const comparison = percentage === null ? direction : `${percentage.toFixed(1)}% ${direction}`;
    return {
      label,
      title: `${comparison} than NAM-A2-FULL ${formatScore(baselineValue)}.`,
      className: modelBetter ? "is-model-better" : "is-baseline-better",
    };
  }

  function metricCell(label, summary, baseline, higherIsBetter = false) {
    const cell = createElement("td", "numeric-cell");
    cell.dataset.label = label;
    cell.append(createElement("strong", `metric-primary${summary.mean === null ? " metric-empty" : ""}`, formatScore(summary.mean)));
    const comparison = comparisonLabel(summary.mean, baseline.mean, higherIsBetter);
    const pill = createElement("span", `metric-comparison ${comparison.className}`, comparison.label);
    pill.title = comparison.title;
    pill.setAttribute("aria-label", comparison.title);
    cell.append(pill);
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
      ampParameters: run.ampParameterCount,
      positions: run.positions,
      positionsPerControl: run.positionsPerControl,
      rank: ranks.get(run.id) ?? null,
      realtime: run.realtime.mean,
      started: run.createdAt ? Date.parse(run.createdAt) : null,
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

  function ampScope() {
    const value = elements.ampScopeFilter?.value;
    return ["normal", "simple", "all"].includes(value) ? value : DEFAULT_AMP_SCOPE;
  }

  function matchesAmpScope(ampId) {
    const simple = SIMPLE_AMP_IDS.has(ampId);
    const scope = ampScope();
    return scope === "all" || (scope === "simple" ? simple : !simple);
  }

  function runsInScope() {
    return state.runs.filter((run) => matchesAmpScope(run.ampId));
  }

  function selectedRuns() {
    const ampId = elements.ampFilter?.value || "";
    const creator = elements.creatorFilter?.value || "";
    const search = (elements.modelFilter?.value || "").trim().toLocaleLowerCase();
    return runsInScope().filter((run) => {
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
      const filtered = ampScope() !== DEFAULT_AMP_SCOPE
        || Boolean(elements.ampFilter?.value)
        || Boolean(elements.creatorFilter?.value)
        || Boolean(elements.modelFilter?.value);
      const row = createElement("tr", "empty-row");
      const cell = createElement("td");
      cell.colSpan = 12;
      cell.append(
        createElement("strong", "", filtered || state.runs.length ? "No runs match these filters" : "No benchmark runs yet"),
        createElement("span", "", filtered || state.runs.length
          ? "Try a different amp, creator, or model name."
          : "Start a local benchmark and its progress will appear here."),
      );
      row.append(cell);
      elements.body.append(row);
      return;
    }

    const ranks = state.serverPaginated ? state.runRanks : rankMap(runsInScope());
    const displayedRuns = state.serverPaginated ? runs : sortedRuns(runs, ranks);
    for (const run of displayedRuns) {
      const row = createElement("tr");
      row.dataset.runId = run.id;
      row.append(
        simpleCell("Rank", ranks.get(run.id) ?? "—", "rank-cell"),
        modelCell(run),
        ampCell(run),
        progressCell(run),
        simpleCell("Positions", run.positions === null ? "—" : formatScore(run.positions), "numeric-cell"),
        simpleCell("Amp parameters", formatInteger(run.ampParameterCount), "numeric-cell"),
        simpleCell(
          "Positions per amp parameter",
          run.positionsPerControl === null ? "—" : formatScore(run.positionsPerControl),
          "numeric-cell",
        ),
        startedCell(run.createdAt),
        simpleCell("Realtime", run.realtime.mean === null ? "—" : `${formatScore(run.realtime.mean)}×`, "numeric-cell"),
        metricCell("ESR", run.esr, run.namA2Full.esr),
        metricCell("Human-weighted ESR", run.humanWeightedEsr, run.namA2Full.humanWeightedEsr),
        metricCell("MRSTFT", run.mrstft, run.namA2Full.mrstft),
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
    const expected = [
      { id: "", name: "All amps" },
      ...state.amps.filter((amp) => matchesAmpScope(amp.id)),
    ];
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
    const creators = state.creators.length
      ? state.creators
      : state.runs.map((run) => run.creator);
    updateSelect(elements.creatorFilter, uniqueSorted(creators), "All creators");
  }

  function paretoFrontier(points) {
    const sorted = [...points].sort((left, right) => (
      left.positionsPerControl - right.positionsPerControl
      || left.esr.mean - right.esr.mean
    ));
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

  function keyResults(frontier, maximum = 8) {
    if (frontier.length <= maximum) return frontier;
    const indices = new Set();
    for (let index = 0; index < maximum; index += 1) {
      indices.add(Math.round((index * (frontier.length - 1)) / (maximum - 1)));
    }
    return [...indices].map((index) => frontier[index]);
  }

  function keyLabelPositions(runs, xScale, yScale, bounds) {
    const labels = runs.map((run) => ({
      id: run.id,
      pointX: xScale(run.positionsPerControl),
      pointY: yScale(run.esr.mean),
    })).sort((left, right) => left.pointY - right.pointY);
    const gap = 16;
    for (const [index, label] of labels.entries()) {
      label.labelY = Math.max(label.pointY, index ? labels[index - 1].labelY + gap : bounds.top);
    }
    if (labels.at(-1)?.labelY > bounds.bottom) {
      labels.at(-1).labelY = bounds.bottom;
      for (let index = labels.length - 2; index >= 0; index -= 1) {
        labels[index].labelY = Math.min(labels[index].labelY, labels[index + 1].labelY - gap);
      }
    }
    return new Map(labels.map((label) => {
      const placeLeft = label.pointX > bounds.left + (bounds.right - bounds.left) * 0.72;
      return [label.id, {
        anchor: placeLeft ? "end" : "start",
        labelX: label.pointX + (placeLeft ? -11 : 11),
        labelY: label.labelY,
        pointX: label.pointX,
        pointY: label.pointY,
      }];
    }));
  }

  function shorten(value, length = 19) {
    return value.length > length ? `${value.slice(0, length - 1)}…` : value;
  }

  function positionRatioTickStep(maximum) {
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
      document.createTextNode(
        `ESR ${formatScore(run.esr.mean)} \u00b7 ${formatScore(run.positionsPerControl)} positions per control `
        + `(${formatScore(run.positions)} positions ÷ ${formatInteger(run.ampParameterCount)} knobs/switches)`,
      ),
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
    const points = runs.filter((run) => (
      run.positionsPerControl !== null && run.esr.mean !== null && run.esr.mean > 0
    ));
    const signature = JSON.stringify(points.map((run) => [
      run.id,
      run.name,
      run.positionsPerControl,
      run.positions,
      run.ampParameterCount,
      run.esr.mean,
    ]));
    if (signature === state.chartSignature) return;
    state.chartSignature = signature;
    chart.replaceChildren();
    hideTooltip();

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
    const maxX = Math.max(...points.map((run) => run.positionsPerControl));
    const minY = Math.min(...points.map((run) => run.esr.mean));
    const maxY = Math.max(...points.map((run) => run.esr.mean));
    const xStep = positionRatioTickStep(maxX);
    const xDomainMax = Math.max(1, Math.ceil((maxX + xStep * 0.3) / xStep) * xStep);
    let yLogMin = Math.floor(Math.log10(minY));
    let yLogMax = Math.ceil(Math.log10(maxY));
    if (yLogMin === yLogMax) {
      yLogMin -= 1;
      yLogMax += 1;
    }
    const yLogSpan = yLogMax - yLogMin;
    const xScale = (value) => margin.left + (value / xDomainMax) * innerWidth;
    const yScale = (value) => (
      margin.top + ((yLogMax - Math.log10(value)) / yLogSpan) * innerHeight
    );

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

    const yExponentStep = Math.max(1, Math.ceil(yLogSpan / 6));
    const yExponents = [];
    for (let exponent = yLogMin; exponent <= yLogMax; exponent += yExponentStep) {
      yExponents.push(exponent);
    }
    if (yExponents.at(-1) !== yLogMax) yExponents.push(yLogMax);
    for (const exponent of yExponents) {
      const y = yScale(10 ** exponent);
      grid.append(createSvg("line", { class: "grid-line", x1: margin.left, x2: margin.left + innerWidth, y1: y, y2: y }));
      const yLabel = createSvg("text", { class: "axis-text", x: margin.left - 15, y: y + 4, "text-anchor": "end" });
      yLabel.textContent = formatScore(10 ** exponent);
      grid.append(yLabel);
    }
    grid.append(
      createSvg("line", { class: "axis-line", x1: margin.left, x2: margin.left + innerWidth, y1: margin.top + innerHeight, y2: margin.top + innerHeight }),
      createSvg("line", { class: "axis-line", x1: margin.left, x2: margin.left, y1: margin.top, y2: margin.top + innerHeight }),
    );
    chart.append(grid);

    const xTitle = createSvg("text", { class: "axis-title", x: margin.left + innerWidth / 2, y: height - 14, "text-anchor": "middle" });
    xTitle.textContent = "Training positions per knob/switch (positions ÷ controls · lower is better)";
    const yTitle = createSvg("text", { class: "axis-title", transform: `translate(21 ${margin.top + innerHeight / 2}) rotate(-90)`, "text-anchor": "middle" });
    yTitle.textContent = "Mean ESR (log scale · lower is better)";
    chart.append(xTitle, yTitle);

    const frontier = paretoFrontier(points);
    const frontierCoordinates = frontier.map((run) => ({
      x: xScale(run.positionsPerControl),
      y: yScale(run.esr.mean),
    }));
    const frontierPath = frontierCoordinates.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
    const areaPath = `${frontierPath} L ${frontierCoordinates.at(-1).x} ${margin.top + innerHeight} L ${frontierCoordinates[0].x} ${margin.top + innerHeight} Z`;
    chart.append(
      createSvg("path", { class: "frontier-area", d: areaPath, "aria-hidden": "true" }),
      createSvg("path", { class: "frontier-line", d: frontierPath, "aria-hidden": "true" }),
    );

    const frontierIds = new Set(frontier.map((run) => run.id));
    const labelsByRun = keyLabelPositions(
      keyResults(frontier),
      xScale,
      yScale,
      {
        top: margin.top + 8,
        right: margin.left + innerWidth,
        bottom: margin.top + innerHeight - 8,
        left: margin.left,
      },
    );
    const pointLayer = createSvg("g");
    for (const run of points) {
      const position = { x: xScale(run.positionsPerControl), y: yScale(run.esr.mean) };
      const onFrontier = frontierIds.has(run.id);
      const marker = createSvg("g", { class: "run-marker" });
      const circle = createSvg("circle", {
        class: `run-point${onFrontier ? " on-frontier" : ""}`,
        cx: position.x,
        cy: position.y,
        r: 6,
        tabindex: 0,
        role: "img",
        "aria-label": `${run.name}: mean ESR ${formatScore(run.esr.mean)}, `
          + `${formatScore(run.positionsPerControl)} positions per knob or switch from `
          + `${formatScore(run.positions)} unique positions divided by `
          + `${formatInteger(run.ampParameterCount)} knobs and switches`
          + `${onFrontier ? ", on the Pareto frontier" : ""}`,
      });
      const nativeTitle = createSvg("title");
      nativeTitle.textContent = `${run.name} — ESR ${formatScore(run.esr.mean)}, `
        + `${formatScore(run.positionsPerControl)} positions per control `
        + `(${formatScore(run.positions)} ÷ ${formatInteger(run.ampParameterCount)})`;
      circle.append(nativeTitle);
      circle.addEventListener("mouseenter", () => showTooltip(run, position));
      circle.addEventListener("focus", () => showTooltip(run, position));
      circle.addEventListener("mouseleave", hideTooltip);
      circle.addEventListener("blur", hideTooltip);
      marker.append(circle);
      const labelPosition = labelsByRun.get(run.id);
      if (labelPosition) {
        const connector = createSvg("line", {
          class: "key-label-line",
          x1: labelPosition.pointX,
          x2: labelPosition.labelX,
          y1: labelPosition.pointY,
          y2: labelPosition.labelY,
          "aria-hidden": "true",
        });
        const label = createSvg("text", {
          class: "point-label",
          x: labelPosition.labelX,
          y: labelPosition.labelY,
          "dominant-baseline": "middle",
          "text-anchor": labelPosition.anchor,
          "aria-hidden": "true",
        });
        label.textContent = shorten(run.name);
        marker.append(connector, label);
      }
      pointLayer.append(marker);
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
    const filtered = state.serverPaginated ? state.runs : selectedRuns();
    const chartRuns = state.serverPaginated ? state.chartRuns : filtered;
    renderTable(filtered);
    renderChart(chartRuns);
    updateSortHeaders();
    if (elements.resultCount) {
      if (state.serverPaginated) {
        const first = state.totalRuns ? (state.page - 1) * state.pageSize + 1 : 0;
        const last = state.totalRuns ? first + filtered.length - 1 : 0;
        elements.resultCount.textContent = state.totalRuns
          ? `${first}–${last} of ${state.totalRuns} runs`
          : "0 runs";
      } else {
        elements.resultCount.textContent = `${filtered.length} ${filtered.length === 1 ? "run" : "runs"}${filtered.length !== state.runs.length ? ` of ${state.runs.length}` : ""}`;
      }
    }
    if (elements.summaryRuns) {
      elements.summaryRuns.textContent = String(state.serverPaginated ? state.totalRuns : state.runs.length);
    }
    if (elements.summaryCompleted) {
      elements.summaryCompleted.textContent = String(state.runs.filter((run) => ["completed", "finished"].includes(run.status)).length);
    }
    if (elements.pageCurrent) elements.pageCurrent.textContent = String(state.page);
    if (elements.pageTotal) elements.pageTotal.textContent = String(state.totalPages);
    if (elements.pagePrevious) elements.pagePrevious.disabled = state.page <= 1;
    if (elements.pageNext) elements.pageNext.disabled = state.page >= state.totalPages;
    if (elements.clearFilters) {
      elements.clearFilters.disabled = ampScope() === DEFAULT_AMP_SCOPE
        && !elements.ampFilter?.value
        && !elements.creatorFilter?.value
        && !elements.modelFilter?.value;
    }
  }

  function setConnection(online) {
    elements.connection?.classList.toggle("is-offline", !online);
    if (elements.connectionLabel) elements.connectionLabel.textContent = online ? "Live" : "Reconnecting";
  }

  function leaderboardUrl() {
    const parameters = new URLSearchParams({
      amp_scope: ampScope(),
      direction: state.sortDirection === "ascending" ? "asc" : "desc",
      page: String(state.page),
      page_size: String(state.pageSize),
      sort: state.sortKey,
    });
    const ampId = elements.ampFilter?.value || "";
    const creator = elements.creatorFilter?.value || "";
    const search = (elements.modelFilter?.value || "").trim();
    if (ampId) parameters.set("amp_id", ampId);
    if (creator) parameters.set("creator", creator);
    if (search) parameters.set("search", search);
    return `/api/v1/leaderboard?${parameters}`;
  }

  function readPageUrl() {
    const parameters = new URLSearchParams(window.location.search);
    const scope = parameters.get("amp_scope");
    const sort = parameters.get("sort");
    const direction = parameters.get("direction");
    return {
      ampId: parameters.get("amp_id") || "",
      ampScope: ["normal", "simple", "all"].includes(scope) ? scope : DEFAULT_AMP_SCOPE,
      creator: parameters.get("creator") || "",
      direction: direction === "desc" ? "descending" : "ascending",
      search: parameters.get("search") || "",
      sort: SORT_KEYS.has(sort) ? sort : "esr",
    };
  }

  function syncPageUrl() {
    const url = new URL(window.location.href);
    const parameters = url.searchParams;
    for (const name of [
      "amp_scope",
      "amp_id",
      "creator",
      "search",
      "sort",
      "direction",
      "page",
      "page_size",
    ]) {
      parameters.delete(name);
    }
    parameters.set("amp_scope", ampScope());
    parameters.set("sort", state.sortKey);
    parameters.set("direction", state.sortDirection === "ascending" ? "asc" : "desc");
    parameters.set("page", String(state.page));
    parameters.set("page_size", String(state.pageSize));
    const ampId = elements.ampFilter?.value || "";
    const creator = elements.creatorFilter?.value || "";
    const search = (elements.modelFilter?.value || "").trim();
    if (ampId) parameters.set("amp_id", ampId);
    if (creator) parameters.set("creator", creator);
    if (search) parameters.set("search", search);
    const target = `${url.pathname}${parameters.size ? `?${parameters}` : ""}${url.hash}`;
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (target !== current) window.history.replaceState(window.history.state, "", target);
  }

  function applyPayload(payload) {
    const runs = runsFromPayload(payload);
    const amps = ampsFromPayload(payload, runs);
    const chartRuns = chartRunsFromPayload(payload, runs);
    const totalRuns = finite(payload?.total_runs);
    const signature = JSON.stringify({
      amps,
      chartRuns,
      creators: payload?.creators,
      runs,
      ranks: payload?.run_ranks,
      page: payload?.page,
      pageSize: payload?.page_size,
      totalRuns,
      totalPages: payload?.total_pages,
    });
    if (signature === state.dataSignature) return;
    state.runs = runs;
    state.chartRuns = chartRuns;
    state.amps = amps;
    state.creators = Array.isArray(payload?.creators) ? payload.creators.map(String) : [];
    state.runRanks = new Map(
      Object.entries(payload?.run_ranks || {}).map(([id, rank]) => [id, Number(rank)]),
    );
    state.serverPaginated = totalRuns !== null;
    state.page = finite(payload?.page) ?? state.page;
    state.pageSize = finite(payload?.page_size) ?? state.pageSize;
    state.totalRuns = totalRuns ?? runs.length;
    state.totalPages = finite(payload?.total_pages) ?? 1;
    state.dataSignature = signature;
    updateFilterOptions();
    render();
    syncPageUrl();
  }

  async function pollLeaderboard({ loading = false } = {}) {
    if (state.requestInFlight || document.hidden) {
      state.reloadPending = true;
      return;
    }
    state.requestInFlight = true;
    if (loading && elements.refreshStatus) elements.refreshStatus.textContent = "Loading results…";
    try {
      const requestedUrl = leaderboardUrl();
      const response = await fetch(requestedUrl, {
        cache: "no-cache",
        headers: { Accept: "application/json" },
      });
      if (response.status === 304) {
        setConnection(true);
        if (elements.refreshStatus) {
          elements.refreshStatus.textContent = `Unchanged · checked ${new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`;
        }
        return;
      }
      if (!response.ok) {
        throw new Error(`Leaderboard request failed with ${response.status}`);
      }
      const payload = await response.json();
      if (requestedUrl === leaderboardUrl()) {
        applyPayload(payload);
      } else {
        state.reloadPending = true;
      }
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
      if (state.reloadPending) {
        state.reloadPending = false;
        void pollLeaderboard({ loading: true });
      }
    }
  }

  function refreshFromFirstPage() {
    state.page = 1;
    syncPageUrl();
    if (state.serverPaginated) {
      void pollLeaderboard({ loading: true });
    } else {
      render();
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
        state.sortDirection = ["realtime", "started"].includes(key) ? "descending" : "ascending";
      }
      refreshFromFirstPage();
    });
  }

  for (const input of [elements.ampFilter, elements.creatorFilter]) {
    input?.addEventListener("change", refreshFromFirstPage);
  }

  elements.modelFilter?.addEventListener("input", () => {
    state.page = 1;
    syncPageUrl();
    if (!state.serverPaginated) {
      render();
      return;
    }
    if (searchTimer !== null) window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      searchTimer = null;
      refreshFromFirstPage();
    }, SEARCH_DEBOUNCE_MS);
  });

  elements.ampScopeFilter?.addEventListener("change", () => {
    updateAmpSelect();
    refreshFromFirstPage();
  });

  elements.clearFilters?.addEventListener("click", () => {
    if (searchTimer !== null) {
      window.clearTimeout(searchTimer);
      searchTimer = null;
    }
    if (elements.ampScopeFilter) elements.ampScopeFilter.value = DEFAULT_AMP_SCOPE;
    if (elements.ampFilter) elements.ampFilter.value = "";
    if (elements.creatorFilter) elements.creatorFilter.value = "";
    if (elements.modelFilter) elements.modelFilter.value = "";
    updateAmpSelect();
    elements.modelFilter?.focus();
    refreshFromFirstPage();
  });

  elements.pagePrevious?.addEventListener("click", () => {
    if (state.page <= 1) return;
    state.page -= 1;
    syncPageUrl();
    void pollLeaderboard({ loading: true });
  });

  elements.pageNext?.addEventListener("click", () => {
    if (state.page >= state.totalPages) return;
    state.page += 1;
    syncPageUrl();
    void pollLeaderboard({ loading: true });
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void pollLeaderboard();
  });

  const initial = parseInitialData();
  state.runs = initial.runs;
  state.chartRuns = initial.chartRuns;
  state.amps = initial.amps;
  state.creators = initial.creators;
  state.runRanks = initial.runRanks || new Map();
  state.page = initial.page || 1;
  state.pageSize = initial.pageSize || PAGE_SIZE;
  state.totalRuns = initial.totalRuns ?? state.runs.length;
  state.totalPages = initial.totalPages || 1;
  state.serverPaginated = initial.serverPaginated || false;
  const pageUrl = readPageUrl();
  state.sortKey = pageUrl.sort;
  state.sortDirection = pageUrl.direction;
  if (elements.ampScopeFilter) elements.ampScopeFilter.value = pageUrl.ampScope;
  if (elements.modelFilter) elements.modelFilter.value = pageUrl.search;
  state.dataSignature = JSON.stringify({
    amps: state.amps,
    chartRuns: state.chartRuns,
    creators: state.creators,
    runs: state.runs,
    ranks: Object.fromEntries(state.runRanks),
    page: state.page,
    pageSize: state.pageSize,
    totalRuns: state.totalRuns,
    totalPages: state.totalPages,
  });
  updateFilterOptions();
  if (elements.ampFilter
    && [...elements.ampFilter.options].some((option) => option.value === pageUrl.ampId)) {
    elements.ampFilter.value = pageUrl.ampId;
  }
  if (elements.creatorFilter
    && [...elements.creatorFilter.options].some((option) => option.value === pageUrl.creator)) {
    elements.creatorFilter.value = pageUrl.creator;
  }
  render();
  syncPageUrl();
  window.setInterval(() => void pollLeaderboard(), POLL_INTERVAL_MS);
})();
