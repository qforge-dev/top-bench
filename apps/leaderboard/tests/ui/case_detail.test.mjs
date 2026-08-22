import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

const SCRIPT_URL = new URL("../../src/top_arena_server/static/case_detail.js", import.meta.url);

const CASES = [
  { case_id: "case-a", index: 1, chunk_index: 0, position_index: 0, status: "completed", url: "/runs/run-1/cases/case-a" },
  { case_id: "case-b", index: 2, chunk_index: 0, position_index: 1, status: "completed", url: "/runs/run-1/cases/case-b" },
];

function detail(caseId, index) {
  const offset = index / 10;
  return {
    run: {
      id: "run-1",
      name: "Velvet Drive",
      creator: "Studio North",
      description: "A compact recurrent amp model.",
      status: "completed",
      amp_name: "Blackface 63",
      unique_positions_used: 5,
      total_cases: 2,
      completed_cases: 2,
      metrics: {
        esr: { mean: 0.031, p90: 0.044, worst: 0.051, best: 0.019 },
        human_weighted_esr: { mean: 0.027 },
        level_db: { mean: 0.54 },
        peak_db: { mean: 0.37 },
        correlation: { mean: 0.94 },
        mrstft: { mean: 0.112 },
        realtime_x: { mean: 18.4 },
      },
    },
    case_id: caseId,
    index,
    total: 2,
    chunk_index: 0,
    position_index: index - 1,
    status: "completed",
    positions: [[0.66, 0.33]],
    control_names: ["bass", "master"],
    duration_seconds: 5,
    sample_rate: 48_000,
    metrics: {
      esr: 0.02 + offset,
      human_weighted_esr: 0.018 + offset,
      mrstft: 0.1 + offset,
      realtime_x: 17 + index,
      level_db: 0.5 + offset,
      peak_db: 0.3 + offset,
      correlation: 0.98 - offset,
      nam_esr: 0.03 + offset,
      nam_human_weighted_esr: 0.028 + offset,
      nam_mrstft: 0.12 + offset,
      nam_level_db: 0.6 + offset,
      nam_peak_db: 0.4 + offset,
      nam_correlation: 0.96 - offset,
    },
    analysis: {
      version: "top-arena-case-analysis-v1",
      window_seconds: 0.1,
      hop_seconds: 0.1,
      points: [
        {
          time_seconds: 0,
          esr: 0.01 + offset,
          reference_level_db: -15,
          candidate_level_db: -14,
          reference_peak_db: -2,
          candidate_peak_db: -1.8,
          correlation: -0.25,
        },
        {
          time_seconds: 5,
          esr: 0.02 + offset,
          reference_level_db: -13,
          candidate_level_db: -12.5,
          reference_peak_db: -1.5,
          candidate_peak_db: -1.2,
          correlation: 0.95 - offset,
        },
      ],
      nam_points: [
        {
          time_seconds: 0,
          esr: 0.02 + offset,
          reference_level_db: -15.5,
          candidate_level_db: -14,
          reference_peak_db: -2.4,
          candidate_peak_db: -1.8,
          correlation: -0.3,
        },
        {
          time_seconds: 5,
          esr: 0.03 + offset,
          reference_level_db: -13.4,
          candidate_level_db: -12.5,
          reference_peak_db: -1.9,
          candidate_peak_db: -1.2,
          correlation: 0.9 - offset,
        },
      ],
    },
    audio: {
      dry: `/audio/${caseId}/dry.wav`,
      reference: `/audio/${caseId}/reference.wav`,
      candidate: `/audio/${caseId}/candidate.wav`,
      nam: `/audio/${caseId}/nam.flac`,
    },
    waveform_url: `/waveform/${caseId}`,
    url: `/runs/run-1/cases/${caseId}`,
    previous_url: index === 1 ? null : "/runs/run-1/cases/case-a",
    next_url: index === 2 ? null : "/runs/run-1/cases/case-b",
  };
}

function markup() {
  return `<!doctype html><html><body>
    <main id="run-detail" data-run-id="run-1" data-case-id="case-a">
      <div id="detail-loading"></div><div id="detail-error" hidden></div><div id="detail-empty" hidden></div>
      <div id="detail-content" hidden>
        <span id="run-status"></span><h1 id="run-name"></h1><p id="run-description"></p><span id="run-creator"></span><span id="run-amp"></span>
        <button id="delete-run" type="button">Delete result</button>
        <div id="delete-run-dialog" role="dialog" aria-modal="true" hidden>
          <p id="delete-run-message"></p><p id="delete-run-status"></p>
          <button id="cancel-delete-run" type="button">Cancel</button>
          <button id="confirm-delete-run" type="button">Delete result</button>
        </div>
        <div id="run-summary"></div>
        <button id="previous-case" type="button">Previous</button>
        <select id="case-select"></select><span id="case-position"></span>
        <button id="next-case" type="button">Next</button>
        <p id="case-label"></p><p id="case-meta"></p><div id="position-chips"></div>
        <div id="case-metrics"></div>
        <audio id="dry-audio" preload="none"></audio><a id="dry-download"></a>
        <audio id="reference-audio" preload="none"></audio><a id="reference-download"></a>
        <audio id="candidate-audio" preload="none"></audio><a id="candidate-download"></a><p id="candidate-missing" hidden></p>
        <audio id="nam-audio" preload="none"></audio><a id="nam-download"></a><p id="nam-missing" hidden></p>
        <button id="play-sequence" type="button">Play sequence</button>
        <span id="sequence-status" role="status">Ready</span>
        <div class="audition-sequence">
          <button class="sequence-part" data-sequence-index="0" type="button">01 BIAS-X</button>
          <button class="sequence-part" data-sequence-index="1" type="button">02 Model</button>
          <button class="sequence-part" data-sequence-index="2" type="button">03 BIAS-X</button>
          <button class="sequence-part" data-sequence-index="3" type="button">04 Model</button>
        </div>
        <p id="waveform-status"></p><div id="waveform-legend"></div>
        <svg id="waveform-chart" viewBox="0 0 1000 360"></svg>
        <div role="tablist">
          <button id="tab-esr" role="tab" data-metric="esr" aria-selected="true" tabindex="0">ESR</button>
          <button id="tab-level" role="tab" data-metric="level_db" aria-selected="false" tabindex="-1">Level dB</button>
          <button id="tab-peak" role="tab" data-metric="peak_db" aria-selected="false" tabindex="-1">Peak dB</button>
          <button id="tab-correlation" role="tab" data-metric="correlation" aria-selected="false" tabindex="-1">Correlation</button>
        </div>
        <p id="chart-summary"></p><div id="case-chart-legend"></div>
        <svg id="case-chart" viewBox="0 0 1000 420"></svg>
      </div>
    </main>
  </body></html>`;
}

async function waitFor(assertion, timeoutMs = 1_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      assertion();
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  }
  assertion();
}

async function setup({
  captureTimers = false,
  esrValues = null,
  failAfterDetailRequests = Infinity,
  runStatus = "completed",
} = {}) {
  const dom = new JSDOM(markup(), {
    runScripts: "outside-only",
    url: "https://arena.test/runs/run-1/cases/case-a",
  });
  const { window } = dom;
  const requests = [];
  const mediaLoads = [];
  const mediaPlays = [];
  const timers = [];
  Object.defineProperty(window.HTMLMediaElement.prototype, "paused", {
    configurable: true,
    get() { return this.dataset.playing !== "true"; },
  });
  window.HTMLMediaElement.prototype.play = function play() {
    this.dataset.playing = "true";
    mediaPlays.push({ id: this.id, currentTime: this.currentTime });
    return Promise.resolve();
  };
  window.HTMLMediaElement.prototype.pause = function pause() {
    this.dataset.playing = "false";
  };
  window.HTMLMediaElement.prototype.load = function load() {
    mediaLoads.push(this.id);
    this.dispatchEvent(new window.Event("canplaythrough"));
  };
  if (captureTimers) {
    window.setTimeout = (callback, delay) => {
      timers.push({ callback, cancelled: false, delay });
      return timers.length;
    };
    window.clearTimeout = (timerId) => {
      if (timers[timerId - 1]) timers[timerId - 1].cancelled = true;
    };
  }
  let detailRequests = 0;
  window.fetch = async (input, options = {}) => {
    const url = String(input);
    requests.push(options.method ? `${options.method} ${url}` : url);
    if (options.method === "DELETE") {
      return { ok: true, status: 204 };
    }
    if (url.endsWith("/detail")) {
      detailRequests += 1;
      if (detailRequests > failAfterDetailRequests) {
        return { ok: false, status: 503 };
      }
    }
    const detailCase = url.includes("case-b") ? "case-b" : "case-a";
    const payload = url.endsWith("/case-index")
      ? { run: detail("case-a", 1).run, cases: CASES }
      : url.startsWith("/waveform/")
        ? {
            duration_seconds: 5,
            series: [
              { key: "dry", label: "Dry", values: [0, 0.2, 0.5, 0.1, 0] },
              { key: "nam", label: "NAM A2", values: [0, 0.3, 0.45, 0.12, 0] },
              { key: "model", label: "Model", values: [0, 0.25, 0.48, 0.11, 0] },
            ],
          }
        : detail(detailCase, detailCase === "case-b" ? 2 : 1);
    if (url.endsWith("/detail") && esrValues) {
      payload.analysis.points.forEach((point, index) => {
        point.esr = esrValues[index];
      });
    }
    if (payload.run) payload.run.status = runStatus;
    if (url.endsWith("/detail")) payload.status = runStatus;
    return { ok: true, status: 200, json: async () => payload };
  };
  const script = await readFile(SCRIPT_URL, "utf8");
  window.eval(script);
  await waitFor(() => assert.equal(window.document.querySelector("#detail-content").hidden, false));
  return { mediaLoads, mediaPlays, requests, timers, window };
}

test("deep link loads one case lazily and renders its summary, graph, and audio", async () => {
  const { requests, window } = await setup();
  const document = window.document;

  assert.deepEqual(requests, [
    "/api/v1/runs/run-1/case-index",
    "/api/v1/runs/run-1/cases/case-a/detail",
    "/waveform/case-a",
  ]);
  assert.equal(document.querySelector("#run-name").textContent, "Velvet Drive");
  assert.equal(document.title, "Velvet Drive · Case detail · Top Arena");
  assert.match(document.querySelector("#run-summary").textContent, /Mean level Δ/);
  assert.match(document.querySelector("#run-summary").textContent, /Mean peak Δ/);
  assert.match(document.querySelector("#run-summary").textContent, /Mean correlation/);
  assert.equal(document.querySelector("#case-position").textContent, "1 / 2");
  assert.match(document.querySelector("#position-chips").textContent, /bass\s+0\.66/);
  assert.match(document.querySelector("#case-metrics").textContent, /Correlation/);
  assert.match(document.querySelector("#case-metrics").textContent, /0\.8800/);
  assert.equal(document.querySelector("#dry-audio").getAttribute("preload"), "none");
  assert.equal(document.querySelector("#dry-audio").getAttribute("src"), "/audio/case-a/dry.wav");
  assert.equal(document.querySelector("#nam-audio").getAttribute("src"), "/audio/case-a/nam.flac");
  assert.equal(document.querySelector("#case-chart").dataset.metric, "esr");
  assert.equal(document.querySelectorAll("#case-chart .chart-series").length, 2);
  assert.match(document.querySelector("#case-chart-legend").textContent, /Reference vs Model/);
  assert.match(document.querySelector("#case-chart-legend").textContent, /Reference vs NAM A2/);
  await waitFor(() => assert.equal(document.querySelectorAll("#waveform-chart .waveform-series").length, 3));
  assert.match(document.querySelector("#waveform-legend").textContent, /Dry/);
  assert.match(document.querySelector("#waveform-legend").textContent, /NAM A2/);
  assert.match(document.querySelector("#waveform-legend").textContent, /Model/);

  document.querySelector("#tab-correlation").click();
  assert.equal(document.querySelector("#case-chart").dataset.metric, "correlation");
  assert.equal(document.querySelectorAll("#case-chart .chart-series").length, 2);
  assert.match(document.querySelector("#case-chart").textContent, /-1\.00/);
  assert.match(document.querySelector("#case-chart").textContent, /1\.00/);
});

test("large ESR ranges use a readable logarithmic scale", async () => {
  const { window } = await setup({ esrValues: [0, 6_830_000] });
  const chart = window.document.querySelector("#case-chart");
  const labels = [...window.document.querySelectorAll('#case-chart .chart-label[text-anchor="end"]')]
    .map((label) => label.textContent);

  assert.equal(chart.dataset.scale, "log");
  assert.equal(window.document.querySelector("#case-chart .chart-title").textContent, "ESR (log scale)");
  assert.equal(labels[0], "0.0000");
  assert.match(labels.at(-1), /M$/);
});

test("play sequence alternates synchronized BIAS-X and model sections", async () => {
  const { mediaLoads, mediaPlays, timers, window } = await setup({ captureTimers: true });
  const document = window.document;

  document.querySelector("#play-sequence").click();
  await waitFor(() => assert.equal(mediaPlays.length, 1));
  assert.deepEqual(mediaLoads, ["reference-audio"]);
  assert.deepEqual(mediaPlays[0], { id: "reference-audio", currentTime: 0 });
  assert.equal(document.querySelector('[data-sequence-index="0"]').getAttribute("aria-current"), "true");
  assert.match(document.querySelector("#sequence-status").textContent, /1 \/ 4.*BIAS-X/);
  assert.equal(document.querySelector("#play-sequence").textContent, "Stop sequence");

  assert.equal(timers[0].delay, 1_250);
  timers[0].callback();
  await waitFor(() => assert.equal(mediaPlays.length, 2));
  assert.deepEqual(mediaLoads, ["reference-audio", "candidate-audio"]);
  assert.deepEqual(mediaPlays[1], { id: "candidate-audio", currentTime: 1.25 });
  assert.equal(document.querySelector('[data-sequence-index="1"]').getAttribute("aria-current"), "true");

  document.querySelector("#next-case").click();
  await waitFor(() => assert.equal(document.querySelector("#case-position").textContent, "2 / 2"));
  assert.equal(document.querySelector("#play-sequence").textContent, "Play sequence");
  assert.equal(document.querySelector("#sequence-status").textContent, "Ready");
});

test("arrows and select update the canonical URL and replace all case media", async () => {
  const { requests, window } = await setup();
  const document = window.document;

  document.querySelector("#next-case").click();
  await waitFor(() => assert.equal(document.querySelector("#case-position").textContent, "2 / 2"));

  assert.equal(window.location.pathname, "/runs/run-1/cases/case-b");
  assert.equal(document.querySelector("#candidate-audio").getAttribute("src"), "/audio/case-b/candidate.wav");
  assert.equal(document.querySelector("#next-case").disabled, true);
  assert.equal(requests.filter((url) => url.endsWith("/detail")).length, 2);
  await waitFor(() => assert.ok(requests.includes("/waveform/case-b")));

  const select = document.querySelector("#case-select");
  select.value = "case-a";
  select.dispatchEvent(new window.Event("change", { bubbles: true }));
  await waitFor(() => assert.equal(document.querySelector("#case-position").textContent, "1 / 2"));
  assert.equal(window.location.pathname, "/runs/run-1/cases/case-a");
  assert.equal(document.querySelector("#reference-audio").getAttribute("src"), "/audio/case-a/reference.wav");
  assert.equal(document.querySelector("#nam-audio").getAttribute("src"), "/audio/case-a/nam.flac");
});

test("history and keyboard-operated metric tabs restore the selected state", async () => {
  const { window } = await setup();
  const document = window.document;

  document.querySelector("#next-case").click();
  await waitFor(() => assert.equal(document.querySelector("#case-position").textContent, "2 / 2"));

  window.history.replaceState({}, "", "/runs/run-1/cases/case-a");
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await waitFor(() => assert.equal(document.querySelector("#case-position").textContent, "1 / 2"));

  const esrTab = document.querySelector("#tab-esr");
  esrTab.dispatchEvent(new window.KeyboardEvent("keydown", { bubbles: true, key: "ArrowRight" }));
  assert.equal(document.activeElement.id, "tab-level");
  assert.equal(document.querySelector("#tab-level").getAttribute("aria-selected"), "true");
  assert.equal(document.querySelector("#case-chart").dataset.metric, "level_db");
  assert.equal(document.querySelectorAll("#case-chart .chart-series").length, 3);
  assert.match(document.querySelector("#case-chart-legend").textContent, /BIAS-X/);
  assert.match(document.querySelector("#case-chart-legend").textContent, /Model/);
  assert.match(document.querySelector("#case-chart-legend").textContent, /NAM A2/);
});

test("a transient live-refresh failure keeps the populated inspector visible and retries", async () => {
  const { requests, timers, window } = await setup({
    captureTimers: true,
    failAfterDetailRequests: 1,
    runStatus: "running",
  });
  const document = window.document;

  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 2_000);
  timers[0].callback();
  await waitFor(() => assert.equal(requests.filter((url) => url.endsWith("/detail")).length, 2));
  await waitFor(() => assert.equal(timers.length, 2));

  assert.equal(document.querySelector("#detail-content").hidden, false);
  assert.equal(document.querySelector("#detail-error").hidden, true);
});

test("deleting a result requires explicit confirmation", async () => {
  const { requests, timers, window } = await setup({ captureTimers: true });
  const document = window.document;
  const dialog = document.querySelector("#delete-run-dialog");

  document.querySelector("#delete-run").click();
  assert.equal(dialog.hidden, false);
  assert.match(document.querySelector("#delete-run-message").textContent, /Velvet Drive/);

  document.querySelector("#cancel-delete-run").click();
  assert.equal(dialog.hidden, true);
  assert.equal(requests.some((request) => request.startsWith("DELETE ")), false);

  document.querySelector("#delete-run").click();
  document.querySelector("#confirm-delete-run").click();
  await waitFor(() => assert.ok(requests.includes("DELETE /api/v1/runs/run-1")));
  await waitFor(() => assert.match(document.querySelector("#delete-run-status").textContent, /deleted/i));
  assert.equal(document.querySelector("#confirm-delete-run").disabled, true);
  assert.equal(timers.at(-1).delay, 250);
});
