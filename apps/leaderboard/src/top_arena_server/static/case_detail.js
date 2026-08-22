(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const REFRESH_INTERVAL_MS = 2_000;
  const TERMINAL_STATUSES = new Set(["completed", "finished", "failed", "error"]);

  const root = document.querySelector("#run-detail");
  if (!root) return;

  const elements = {
    candidateAudio: document.querySelector("#candidate-audio"),
    candidateDownload: document.querySelector("#candidate-download"),
    candidateMissing: document.querySelector("#candidate-missing"),
    caseChart: document.querySelector("#case-chart"),
    caseLabel: document.querySelector("#case-label"),
    caseLegend: document.querySelector("#case-chart-legend"),
    caseMeta: document.querySelector("#case-meta"),
    caseMetrics: document.querySelector("#case-metrics"),
    casePosition: document.querySelector("#case-position"),
    caseSelect: document.querySelector("#case-select"),
    chartPanel: document.querySelector("#chart-panel"),
    chartSummary: document.querySelector("#chart-summary"),
    content: document.querySelector("#detail-content"),
    comparisonModel: document.querySelector("#comparison-model"),
    dryAudio: document.querySelector("#dry-audio"),
    dryDownload: document.querySelector("#dry-download"),
    empty: document.querySelector("#detail-empty"),
    error: document.querySelector("#detail-error"),
    errorMessage: document.querySelector("#detail-error-message"),
    loading: document.querySelector("#detail-loading"),
    namAudio: document.querySelector("#nam-audio"),
    namDownload: document.querySelector("#nam-download"),
    namMissing: document.querySelector("#nam-missing"),
    next: document.querySelector("#next-case"),
    positions: document.querySelector("#position-chips"),
    previous: document.querySelector("#previous-case"),
    playSequence: document.querySelector("#play-sequence"),
    referenceAudio: document.querySelector("#reference-audio"),
    referenceDownload: document.querySelector("#reference-download"),
    retry: document.querySelector("#retry-detail"),
    runAmp: document.querySelector("#run-amp"),
    runCreator: document.querySelector("#run-creator"),
    runDescription: document.querySelector("#run-description"),
    runName: document.querySelector("#run-name"),
    runStatus: document.querySelector("#run-status"),
    runSummary: document.querySelector("#run-summary"),
    sequenceParts: [...document.querySelectorAll(".sequence-part[data-sequence-index]")],
    sequenceStatus: document.querySelector("#sequence-status"),
    tabs: [...document.querySelectorAll('[role="tab"][data-metric]')],
  };

  const state = {
    cases: [],
    currentCaseId: root.dataset.caseId || "",
    detail: null,
    metric: "esr",
    refreshTimer: null,
    requestSerial: 0,
    runId: root.dataset.runId || "",
    sequenceActive: false,
    sequenceIndex: -1,
    sequenceTimer: null,
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
    return text(value, "queued")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function createElement(tag, className = "", content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }

  function createSvg(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) {
      node.setAttribute(name, String(value));
    }
    return node;
  }

  function setText(element, value) {
    if (element) element.textContent = text(value);
  }

  function formatNumber(value, digits = 4) {
    const number = finite(value);
    if (number === null) return "—";
    const absolute = Math.abs(number);
    if (absolute > 0 && absolute < 0.0001) return number.toExponential(2);
    return number.toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    });
  }

  function formatCompact(value) {
    const number = finite(value);
    if (number === null) return "—";
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, notation: "compact" }).format(number);
  }

  function metricMean(run, name) {
    const metric = run?.metrics?.[name];
    if (metric && typeof metric === "object") return finite(metric.mean);
    return finite(metric);
  }

  function addDefinitionListItem(list, label, value, hint = "", valueClass = "") {
    if (!list) return;
    const wrapper = createElement("div");
    const term = createElement("dt", "", label);
    const description = createElement("dd", valueClass, value);
    if (hint) description.append(createElement("small", "", hint));
    wrapper.append(term, description);
    list.append(wrapper);
  }

  function renderRun(run) {
    if (!run || typeof run !== "object") return;
    setText(elements.runName, run.name || "Untitled model");
    setText(elements.comparisonModel, run.name || "model");
    setText(elements.runCreator, run.creator || "Anonymous");
    setText(elements.runDescription, run.description || "No model description was supplied.");
    setText(elements.runAmp, run.amp_name || run.amp_id || "Unknown amp");

    const status = text(run.status, "queued").toLowerCase();
    if (elements.runStatus) {
      elements.runStatus.textContent = titleCase(status);
      elements.runStatus.className = `detail-status is-${status.replace(/[^a-z0-9-]/g, "-")}`;
    }

    if (run.name) document.title = `${run.name} · Case detail · Top Arena`;
    if (!elements.runSummary) return;
    elements.runSummary.replaceChildren();
    addDefinitionListItem(elements.runSummary, "Mean ESR", formatNumber(metricMean(run, "esr")), "lower is better");
    const levelDelta = metricMean(run, "level_db");
    addDefinitionListItem(
      elements.runSummary,
      "Mean level Δ",
      levelDelta === null ? "—" : `${formatNumber(levelDelta, 2)} dB`,
      "absolute difference",
    );
    const peakDelta = metricMean(run, "peak_db");
    addDefinitionListItem(
      elements.runSummary,
      "Mean peak Δ",
      peakDelta === null ? "—" : `${formatNumber(peakDelta, 2)} dB`,
      "absolute difference",
    );
    addDefinitionListItem(elements.runSummary, "Mean correlation", formatNumber(metricMean(run, "correlation")), "higher is better");
    addDefinitionListItem(elements.runSummary, "MRSTFT", formatNumber(metricMean(run, "mrstft")), "multi-resolution");
    const realtime = metricMean(run, "realtime_x");
    addDefinitionListItem(elements.runSummary, "Realtime", realtime === null ? "—" : `${formatNumber(realtime, 2)}×`, "higher is faster");
    addDefinitionListItem(elements.runSummary, "Positions", formatCompact(run.unique_positions_used), "unique settings");
    const completed = finite(run.completed_cases) ?? 0;
    const total = finite(run.total_cases) ?? 0;
    addDefinitionListItem(elements.runSummary, "Cases", `${formatCompact(completed)} / ${formatCompact(total)}`, titleCase(status));
  }

  function showState(kind, message = "") {
    const showingContent = kind === "content";
    if (elements.loading) elements.loading.hidden = kind !== "loading";
    if (elements.error) elements.error.hidden = kind !== "error";
    if (elements.empty) elements.empty.hidden = kind !== "empty";
    if (elements.content) elements.content.hidden = !showingContent;
    if (elements.errorMessage && message) elements.errorMessage.textContent = message;
    root.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
  }

  function setNavigationBusy(busy) {
    root.classList.toggle("is-case-loading", busy);
    if (elements.caseSelect) elements.caseSelect.disabled = busy;
    if (busy) {
      if (elements.previous) elements.previous.disabled = true;
      if (elements.next) elements.next.disabled = true;
    }
    root.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function requestJson(url) {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    return response.json();
  }

  function normalizeCases(payload) {
    if (!Array.isArray(payload)) return [];
    return payload
      .filter((item) => item && item.case_id)
      .map((item, offset) => ({
        ...item,
        case_id: String(item.case_id),
        index: finite(item.index) ?? offset + 1,
      }))
      .sort((left, right) => left.index - right.index);
  }

  function populateCaseSelect() {
    if (!elements.caseSelect) return;
    elements.caseSelect.replaceChildren();
    for (const item of state.cases) {
      const option = createElement(
        "option",
        "",
        `Test ${String(item.index).padStart(2, "0")} · Chunk ${String((finite(item.chunk_index) ?? 0) + 1).padStart(2, "0")} · Position ${String((finite(item.position_index) ?? 0) + 1).padStart(2, "0")}`,
      );
      option.value = item.case_id;
      elements.caseSelect.append(option);
    }
    elements.caseSelect.value = state.currentCaseId;
  }

  function positionValues(detail) {
    const matrix = Array.isArray(detail?.positions) ? detail.positions : [];
    return Array.isArray(matrix[0]) ? matrix[0] : matrix;
  }

  function renderPositions(detail) {
    if (!elements.positions) return;
    elements.positions.replaceChildren();
    const controls = Array.isArray(detail.control_names) ? detail.control_names : [];
    const values = positionValues(detail);
    if (values.length === 0) {
      elements.positions.append(createElement("span", "position-chip", "No position metadata"));
      return;
    }
    values.forEach((value, index) => {
      const chip = createElement("span", "position-chip");
      chip.append(
        document.createTextNode(`${text(controls[index], `control ${index + 1}`)} `),
        createElement("b", "", formatNumber(value, 2)),
      );
      elements.positions.append(chip);
    });
  }

  const CASE_METRICS = [
    { key: "esr", label: "ESR", hint: "lower is better", digits: 4 },
    { key: "human_weighted_esr", label: "Weighted ESR", hint: "human-weighted", digits: 4 },
    { key: "mrstft", label: "MRSTFT", hint: "lower is better", digits: 4 },
    { key: "level_db", label: "Level Δ", hint: "absolute difference", digits: 2, suffix: " dB" },
    { key: "peak_db", label: "Peak Δ", hint: "absolute difference", digits: 2, suffix: " dB" },
    { key: "correlation", label: "Correlation", hint: "higher is better", digits: 4 },
    { key: "realtime_x", label: "Realtime", hint: "higher is faster", digits: 2, suffix: "×" },
  ];

  function renderCaseMetrics(detail) {
    if (!elements.caseMetrics) return;
    elements.caseMetrics.replaceChildren();
    for (const definition of CASE_METRICS) {
      const value = finite(detail.metrics?.[definition.key]);
      const formatted = value === null
        ? "—"
        : `${formatNumber(value, definition.digits)}${definition.suffix || ""}`;
      const isGood = definition.key === "correlation" && value !== null && value >= 0.95;
      addDefinitionListItem(
        elements.caseMetrics,
        definition.label,
        formatted,
        definition.hint,
        value === null ? "metric-unavailable" : isGood ? "metric-good" : "",
      );
    }
  }

  function setAudio(audio, download, url, optional = false) {
    const source = typeof url === "string" && url ? url : null;
    if (audio) {
      const previous = audio.getAttribute("src");
      if (previous !== source) {
        if (!audio.paused && typeof audio.pause === "function") audio.pause();
        if (source) audio.setAttribute("src", source);
        else audio.removeAttribute("src");
      }
      audio.hidden = optional && source === null;
    }
    if (download) {
      if (source) download.setAttribute("href", source);
      else download.removeAttribute("href");
      download.hidden = source === null;
    }
  }

  function renderAudio(detail) {
    const audio = detail.audio && typeof detail.audio === "object" ? detail.audio : {};
    setAudio(elements.dryAudio, elements.dryDownload, audio.dry);
    setAudio(elements.referenceAudio, elements.referenceDownload, audio.reference);
    setAudio(elements.candidateAudio, elements.candidateDownload, audio.candidate, true);
    setAudio(elements.namAudio, elements.namDownload, audio.nam, true);
    if (elements.candidateMissing) elements.candidateMissing.hidden = Boolean(audio.candidate);
    if (elements.namMissing) elements.namMissing.hidden = Boolean(audio.nam);
  }

  function clearSequenceTimer() {
    if (state.sequenceTimer !== null) {
      window.clearTimeout(state.sequenceTimer);
      state.sequenceTimer = null;
    }
  }

  function setSequenceButton(playing) {
    if (!elements.playSequence) return;
    elements.playSequence.textContent = playing ? "Stop sequence" : "Play sequence";
    elements.playSequence.setAttribute("aria-pressed", playing ? "true" : "false");
  }

  function stopSequence(status = "Ready") {
    clearSequenceTimer();
    state.sequenceActive = false;
    state.sequenceIndex = -1;
    for (const audio of [elements.referenceAudio, elements.candidateAudio]) {
      if (audio && !audio.paused && typeof audio.pause === "function") audio.pause();
    }
    for (const part of elements.sequenceParts) {
      part.classList.remove("is-active");
      part.removeAttribute("aria-current");
    }
    setSequenceButton(false);
    setText(elements.sequenceStatus, status);
  }

  function playSequenceStep(index) {
    if (!state.sequenceActive || index < 0 || index >= elements.sequenceParts.length) {
      stopSequence(index >= elements.sequenceParts.length ? "Sequence complete" : "Ready");
      return;
    }
    clearSequenceTimer();
    const referenceStep = index % 2 === 0;
    const audio = referenceStep ? elements.referenceAudio : elements.candidateAudio;
    const label = referenceStep ? "BIAS-X" : "Model";
    const duration = Math.max((finite(state.detail?.duration_seconds) ?? 4) / elements.sequenceParts.length, 0.1);
    if (!audio?.getAttribute("src")) {
      stopSequence(`${label} audio is unavailable`);
      return;
    }
    for (const candidate of [elements.referenceAudio, elements.candidateAudio]) {
      if (candidate && candidate !== audio && !candidate.paused && typeof candidate.pause === "function") candidate.pause();
    }
    state.sequenceIndex = index;
    elements.sequenceParts.forEach((part, partIndex) => {
      const active = partIndex === index;
      part.classList.toggle("is-active", active);
      if (active) part.setAttribute("aria-current", "true");
      else part.removeAttribute("aria-current");
    });
    try {
      audio.currentTime = duration * index;
    } catch {
      // Some browsers only allow seeking once media metadata is available.
    }
    setText(elements.sequenceStatus, `${index + 1} / ${elements.sequenceParts.length} · ${label}`);
    const playback = audio.play();
    if (playback && typeof playback.catch === "function") {
      playback.catch(() => stopSequence("Playback could not start"));
    }
    state.sequenceTimer = window.setTimeout(() => {
      if (!state.sequenceActive) return;
      playSequenceStep(index + 1);
    }, duration * 1_000);
  }

  function startSequence(index = 0) {
    stopSequence();
    state.sequenceActive = true;
    setSequenceButton(true);
    playSequenceStep(index);
  }

  function analysisPoints(detail, source = "bias") {
    const analysis = detail?.analysis;
    if (source === "nam") return Array.isArray(analysis?.nam_points) ? analysis.nam_points : [];
    if (Array.isArray(analysis)) return analysis;
    return Array.isArray(analysis?.points) ? analysis.points : [];
  }

  const CHART_METRICS = {
    correlation: {
      axis: "Correlation",
      description: "Correlation over time · higher is better",
      fields: [
        { key: "correlation", label: "Model vs BIAS-X", tone: "model" },
        { key: "correlation", label: "Model vs NAM A2", source: "nam", tone: "nam" },
      ],
      fixedDomain: [-1, 1],
    },
    esr: {
      axis: "ESR (log scale)",
      description: "Error-to-signal ratio over time · logarithmic scale · lower is better",
      fields: [
        { key: "esr", label: "Model vs BIAS-X", tone: "model" },
        { key: "esr", label: "Model vs NAM A2", source: "nam", tone: "nam" },
      ],
      scale: "log",
      zeroBaseline: true,
    },
    level_db: {
      axis: "Level (dBFS)",
      description: "Reference and candidate RMS level over time",
      fields: [
        { key: "reference_level_db", label: "BIAS-X", tone: "reference" },
        { key: "candidate_level_db", label: "Model", tone: "model" },
        { key: "reference_level_db", label: "NAM A2", source: "nam", tone: "nam" },
      ],
    },
    peak_db: {
      axis: "Peak (dBFS)",
      description: "Reference and candidate peak level over time",
      fields: [
        { key: "reference_peak_db", label: "BIAS-X", tone: "reference" },
        { key: "candidate_peak_db", label: "Model", tone: "model" },
        { key: "reference_peak_db", label: "NAM A2", source: "nam", tone: "nam" },
      ],
    },
  };

  function seriesFor(detail, field) {
    return analysisPoints(detail, field.source)
      .map((point) => ({ time: finite(point?.time_seconds), value: finite(point?.[field.key]) }))
      .filter((point) => point.time !== null && point.value !== null);
  }

  function chartDomain(config, series) {
    if (config.fixedDomain) return config.fixedDomain;
    const values = series.flatMap((item) => item.values.map((point) => point.value));
    if (values.length === 0) return [0, 1];
    let minimum = Math.min(...values);
    let maximum = Math.max(...values);
    if (config.zeroBaseline) minimum = 0;
    const span = maximum - minimum;
    const padding = span > 0 ? span * 0.12 : Math.max(Math.abs(maximum) * 0.12, 0.1);
    if (!config.zeroBaseline) minimum -= padding;
    maximum += padding;
    if (minimum === maximum) maximum = minimum + 1;
    return [minimum, maximum];
  }

  function chartTick(value, metric) {
    if (metric === "correlation") return Number(value).toFixed(2);
    if (metric === "esr") {
      const absolute = Math.abs(value);
      const compactScales = [
        { divisor: 1_000_000_000_000, suffix: "T" },
        { divisor: 1_000_000_000, suffix: "B" },
        { divisor: 1_000_000, suffix: "M" },
        { divisor: 1_000, suffix: "K" },
      ];
      const scale = compactScales.find((candidate) => absolute >= candidate.divisor);
      if (scale) {
        const rounded = (value / scale.divisor).toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
        return `${rounded}${scale.suffix}`;
      }
      if (absolute >= 100) return Number(value).toFixed(0);
      if (absolute >= 10) return Number(value).toFixed(1);
      return Number(value).toFixed(absolute < 0.01 ? 4 : 3);
    }
    return Number(value).toFixed(1);
  }

  function renderLegend(config, renderedSeries) {
    if (!elements.caseLegend) return;
    elements.caseLegend.replaceChildren();
    for (const item of renderedSeries) {
      const label = createElement("span");
      label.append(
        createElement("i", `tone-${item.definition.tone || "model"}`),
        document.createTextNode(item.definition.label),
      );
      elements.caseLegend.append(label);
    }
    if (renderedSeries.length === 0) {
      elements.caseLegend.append(createElement("span", "", "No windowed samples"));
    }
    if (elements.chartSummary) {
      const count = renderedSeries[0]?.values.length ?? 0;
      const duration = finite(state.detail?.duration_seconds);
      const durationLabel = duration === null ? "this case" : `${formatNumber(duration, 2)}s`;
      elements.chartSummary.textContent = `${config.description} · ${count} ${count === 1 ? "sample" : "samples"} across ${durationLabel}`;
    }
  }

  function renderChart() {
    const chart = elements.caseChart;
    const detail = state.detail;
    if (!chart || !detail) return;
    const config = CHART_METRICS[state.metric] || CHART_METRICS.esr;
    const renderedSeries = config.fields
      .map((definition) => ({ definition, values: seriesFor(detail, definition) }))
      .filter((series) => series.values.length > 0);
    chart.replaceChildren();
    chart.dataset.metric = state.metric;
    chart.dataset.scale = config.scale || "linear";
    renderLegend(config, renderedSeries);

    const width = 1_000;
    const height = 420;
    const margin = { top: 26, right: 32, bottom: 58, left: 82 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const title = createSvg("title");
    title.textContent = config.description;
    const description = createSvg("desc");
    description.textContent = renderedSeries.length
      ? `Time series for ${renderedSeries.map((item) => item.definition.label).join(" and ")}.`
      : "No windowed analysis is available for this metric.";
    chart.append(title, description);

    if (renderedSeries.length === 0) {
      const empty = createSvg("text", { class: "chart-empty", x: width / 2, y: height / 2, "text-anchor": "middle" });
      empty.textContent = "Windowed analysis will appear when scoring finishes";
      chart.append(empty);
      return;
    }

    const allTimes = renderedSeries.flatMap((item) => item.values.map((point) => point.time));
    const minimumTime = Math.min(0, ...allTimes);
    const maximumTime = Math.max(finite(detail.duration_seconds) ?? 0, ...allTimes, 1);
    const [minimumValue, maximumValue] = chartDomain(config, renderedSeries);
    const xScale = (value) => margin.left + ((value - minimumTime) / (maximumTime - minimumTime)) * innerWidth;
    const positiveValues = renderedSeries
      .flatMap((item) => item.values.map((point) => point.value))
      .filter((value) => value > 0);
    const logarithmicFloor = config.scale === "log"
      ? Math.min(0.000001, (Math.min(...positiveValues) || 0.00001) / 10)
      : 0;
    const scaledMinimum = config.scale === "log" ? Math.log10(logarithmicFloor) : minimumValue;
    const scaledMaximum = config.scale === "log"
      ? Math.max(scaledMinimum + 1, Math.log10(Math.max(maximumValue, logarithmicFloor * 10)))
      : maximumValue;
    const scaleValue = (value) => config.scale === "log"
      ? Math.log10(Math.max(value, logarithmicFloor))
      : value;
    const yScale = (value) => margin.top + innerHeight
      - ((scaleValue(value) - scaledMinimum) / (scaledMaximum - scaledMinimum)) * innerHeight;

    const defs = createSvg("defs");
    const gradient = createSvg("linearGradient", { id: "case-area-fill", x1: 0, x2: 0, y1: 0, y2: 1 });
    gradient.append(
      createSvg("stop", { offset: "0%", "stop-color": "#63b3ff", "stop-opacity": 0.16 }),
      createSvg("stop", { offset: "100%", "stop-color": "#63b3ff", "stop-opacity": 0 }),
    );
    defs.append(gradient);
    chart.append(defs);

    const grid = createSvg("g", { "aria-hidden": "true" });
    const tickCount = 5;
    for (let index = 0; index <= tickCount; index += 1) {
      const ratio = index / tickCount;
      const x = margin.left + ratio * innerWidth;
      const y = margin.top + ratio * innerHeight;
      grid.append(
        createSvg("line", { class: "chart-grid", x1: x, x2: x, y1: margin.top, y2: margin.top + innerHeight }),
        createSvg("line", { class: "chart-grid", x1: margin.left, x2: margin.left + innerWidth, y1: y, y2: y }),
      );

      const xLabel = createSvg("text", { class: "chart-label", x, y: margin.top + innerHeight + 27, "text-anchor": "middle" });
      xLabel.textContent = `${formatNumber(minimumTime + ratio * (maximumTime - minimumTime), 1)}s`;
      const yLabel = createSvg("text", { class: "chart-label", x: margin.left - 13, y: margin.top + innerHeight - ratio * innerHeight + 4, "text-anchor": "end" });
      const tickValue = config.scale === "log"
        ? (ratio === 0 ? 0 : 10 ** (scaledMinimum + ratio * (scaledMaximum - scaledMinimum)))
        : minimumValue + ratio * (maximumValue - minimumValue);
      yLabel.textContent = chartTick(tickValue, state.metric);
      grid.append(xLabel, yLabel);
    }
    grid.append(
      createSvg("line", { class: "chart-axis", x1: margin.left, x2: margin.left + innerWidth, y1: margin.top + innerHeight, y2: margin.top + innerHeight }),
      createSvg("line", { class: "chart-axis", x1: margin.left, x2: margin.left, y1: margin.top, y2: margin.top + innerHeight }),
    );
    chart.append(grid);

    const yTitle = createSvg("text", {
      class: "chart-title",
      transform: `translate(21 ${margin.top + innerHeight / 2}) rotate(-90)`,
      "text-anchor": "middle",
    });
    yTitle.textContent = config.axis;
    const xTitle = createSvg("text", {
      class: "chart-title",
      x: margin.left + innerWidth / 2,
      y: height - 10,
      "text-anchor": "middle",
    });
    xTitle.textContent = "Time (seconds)";
    chart.append(yTitle, xTitle);

    renderedSeries.forEach((series, index) => {
      const coordinates = series.values.map((point) => ({ x: xScale(point.time), y: yScale(point.value) }));
      const pathData = coordinates.map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
      if (renderedSeries.length === 1 && coordinates.length > 1) {
        const baseline = margin.top + innerHeight;
        const areaData = `${pathData} L ${coordinates[coordinates.length - 1].x} ${baseline} L ${coordinates[0].x} ${baseline} Z`;
        chart.append(createSvg("path", { class: "chart-area", d: areaData, "aria-hidden": "true" }));
      }
      const tone = series.definition.tone || (index > 0 ? "nam" : "model");
      const className = `chart-series tone-${tone}`;
      chart.append(createSvg("path", { class: className, d: pathData, "aria-hidden": "true" }));
      const endpoint = coordinates[coordinates.length - 1];
      chart.append(createSvg("circle", {
        class: `chart-endpoint tone-${tone}`,
        cx: endpoint.x,
        cy: endpoint.y,
        r: 4,
        "aria-hidden": "true",
      }));
    });
  }

  function activateMetric(tab) {
    if (!tab?.dataset.metric || !CHART_METRICS[tab.dataset.metric]) return;
    state.metric = tab.dataset.metric;
    for (const candidate of elements.tabs) {
      const active = candidate === tab;
      candidate.setAttribute("aria-selected", active ? "true" : "false");
      candidate.tabIndex = active ? 0 : -1;
    }
    if (elements.chartPanel) elements.chartPanel.setAttribute("aria-labelledby", tab.id);
    renderChart();
  }

  function renderCaseIdentity(detail) {
    const index = finite(detail.index) ?? state.cases.findIndex((item) => item.case_id === detail.case_id) + 1;
    const total = finite(detail.total) ?? state.cases.length;
    setText(elements.caseLabel, `TEST–${String(index).padStart(4, "0")}`);
    setText(elements.casePosition, `${index} / ${total}`);
    const duration = finite(detail.duration_seconds);
    const sampleRate = finite(detail.sample_rate);
    const parts = [
      duration === null ? null : `${formatNumber(duration, 2)}s`,
      sampleRate === null ? null : `${formatNumber(sampleRate / 1_000, sampleRate % 1_000 === 0 ? 0 : 1)} kHz`,
      `Chunk ${(finite(detail.chunk_index) ?? 0) + 1}`,
      `Position ${(finite(detail.position_index) ?? 0) + 1}`,
      titleCase(detail.status),
    ].filter(Boolean);
    setText(elements.caseMeta, parts.join(" · "));
  }

  function updateNavigation(detail) {
    const selectedIndex = state.cases.findIndex((item) => item.case_id === detail.case_id);
    if (elements.caseSelect) {
      elements.caseSelect.value = detail.case_id;
      elements.caseSelect.disabled = false;
    }
    if (elements.previous) {
      elements.previous.disabled = selectedIndex <= 0 && !detail.previous_url;
      elements.previous.dataset.caseId = selectedIndex > 0 ? state.cases[selectedIndex - 1].case_id : "";
    }
    if (elements.next) {
      elements.next.disabled = (selectedIndex < 0 || selectedIndex >= state.cases.length - 1) && !detail.next_url;
      elements.next.dataset.caseId = selectedIndex >= 0 && selectedIndex < state.cases.length - 1
        ? state.cases[selectedIndex + 1].case_id
        : "";
    }
  }

  function renderDetail(detail) {
    stopSequence();
    state.detail = detail;
    state.currentCaseId = String(detail.case_id);
    root.dataset.caseId = state.currentCaseId;
    renderRun(detail.run);
    renderCaseIdentity(detail);
    renderPositions(detail);
    renderCaseMetrics(detail);
    renderAudio(detail);
    updateNavigation(detail);
    renderChart();
    showState("content");
  }

  function canonicalUrl(detail) {
    if (typeof detail.url === "string" && detail.url) return detail.url;
    const item = state.cases.find((candidate) => candidate.case_id === detail.case_id);
    return item?.url || `/runs/${encodeURIComponent(state.runId)}/cases/${encodeURIComponent(detail.case_id)}`;
  }

  function updateHistory(detail, mode) {
    if (mode === "none") return;
    const url = canonicalUrl(detail);
    const target = new URL(url, window.location.origin);
    if (target.pathname === window.location.pathname && target.search === window.location.search) return;
    const method = mode === "replace" ? "replaceState" : "pushState";
    window.history[method]({ runId: state.runId, caseId: detail.case_id }, "", url);
  }

  function clearRefresh() {
    if (state.refreshTimer !== null) {
      window.clearTimeout(state.refreshTimer);
      state.refreshTimer = null;
    }
  }

  function isTerminal(status) {
    return TERMINAL_STATUSES.has(text(status).toLowerCase());
  }

  function scheduleRefresh(detail) {
    clearRefresh();
    if (isTerminal(detail.status) && isTerminal(detail.run?.status)) return;
    state.refreshTimer = window.setTimeout(() => {
      void loadCase(state.currentCaseId, { historyMode: "none", quiet: true });
    }, REFRESH_INTERVAL_MS);
  }

  async function loadCase(caseId, { historyMode = "push", quiet = false } = {}) {
    if (!caseId) return;
    clearRefresh();
    const serial = ++state.requestSerial;
    if (!quiet) {
      if (!state.detail) showState("loading");
      else setNavigationBusy(true);
    }
    try {
      const detail = await requestJson(
        `/api/v1/runs/${encodeURIComponent(state.runId)}/cases/${encodeURIComponent(caseId)}/detail`,
      );
      if (serial !== state.requestSerial) return;
      renderDetail(detail);
      updateHistory(detail, historyMode);
      scheduleRefresh(detail);
    } catch (error) {
      if (serial !== state.requestSerial) return;
      if (quiet && state.detail) {
        scheduleRefresh(state.detail);
        return;
      }
      const message = error instanceof Error ? error.message : "The benchmark details could not be loaded.";
      showState("error", `${message}. Please try again.`);
    } finally {
      if (serial === state.requestSerial) setNavigationBusy(false);
    }
  }

  async function boot() {
    if (!state.runId) {
      showState("error", "This link does not contain a benchmark run ID.");
      return;
    }
    showState("loading");
    try {
      const payload = await requestJson(`/api/v1/runs/${encodeURIComponent(state.runId)}/case-index`);
      state.cases = normalizeCases(payload?.cases);
      renderRun(payload?.run);
      if (state.cases.length === 0) {
        showState("empty");
        return;
      }
      const requested = state.cases.find((item) => item.case_id === state.currentCaseId);
      const selected = requested || state.cases[0];
      state.currentCaseId = selected.case_id;
      populateCaseSelect();
      await loadCase(selected.case_id, { historyMode: requested ? "none" : "replace" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "The benchmark index could not be loaded.";
      showState("error", `${message}. Please try again.`);
    }
  }

  function navigateTo(caseId) {
    if (!caseId || caseId === state.currentCaseId) return;
    stopSequence();
    void loadCase(caseId, { historyMode: "push" });
  }

  elements.previous?.addEventListener("click", () => navigateTo(elements.previous?.dataset.caseId));
  elements.next?.addEventListener("click", () => navigateTo(elements.next?.dataset.caseId));
  elements.caseSelect?.addEventListener("change", () => navigateTo(elements.caseSelect?.value));
  elements.playSequence?.addEventListener("click", () => {
    if (state.sequenceActive) stopSequence();
    else startSequence();
  });
  for (const part of elements.sequenceParts) {
    part.addEventListener("click", () => startSequence(Number(part.dataset.sequenceIndex) || 0));
  }
  elements.retry?.addEventListener("click", () => {
    if (state.cases.length > 0 && state.currentCaseId) void loadCase(state.currentCaseId, { historyMode: "none" });
    else void boot();
  });

  for (const tab of elements.tabs) {
    tab.addEventListener("click", () => activateMetric(tab));
    tab.addEventListener("keydown", (event) => {
      const current = elements.tabs.indexOf(tab);
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % elements.tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + elements.tabs.length) % elements.tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = elements.tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      elements.tabs[next].focus();
      activateMetric(elements.tabs[next]);
    });
  }

  window.addEventListener("popstate", () => {
    const path = window.location.pathname;
    const selected = state.cases.find((item) => {
      if (!item.url) return false;
      return new URL(item.url, window.location.origin).pathname === path;
    });
    if (selected && selected.case_id !== state.currentCaseId) {
      void loadCase(selected.case_id, { historyMode: "none" });
    }
  });

  window.addEventListener("pagehide", () => {
    clearRefresh();
    stopSequence();
  });
  void boot();
})();
