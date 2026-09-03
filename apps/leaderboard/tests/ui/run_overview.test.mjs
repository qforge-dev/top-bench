import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

const SCRIPT_URL = new URL("../../src/top_arena_server/static/run_overview.js", import.meta.url);

function distribution(mean, { median = mean, p90 = mean * 1.2, best = mean * 0.7, worst = mean * 1.4, count = 21 } = {}) {
  return { count, mean, median, p90, best, worst };
}

function metrics(mean) {
  return {
    esr: distribution(mean),
    human_weighted_esr: distribution(mean * 0.9),
    mrstft: distribution(mean * 4),
    level_db: distribution(mean * 10),
    peak_db: distribution(mean * 8),
    correlation: distribution(0.95, { best: 0.99, worst: 0.9 }),
    realtime_x: distribution(18, { best: 22, worst: 14 }),
  };
}

function position(id, mean, distance, rank) {
  return {
    position_id: id,
    position_index: id - 1,
    positions: [[id / 10, 1 - id / 10]],
    control_names: ["gain", "tone"],
    total_cases: 7,
    completed_cases: 7,
    esr_error_rank: rank,
    metrics: metrics(mean),
    nam_metrics: metrics(0.04),
    training_coverage: {
      control_setting_id: id,
      nearest_training_distance: distance,
      nearest_training_points: [{ training_position_id: 1, training_position: [0, 1], distance }],
    },
    url: `/runs/run-1/positions/${id}`,
  };
}

function payload() {
  const positions = [position(1, 0.02, 0.1, 3), position(2, 0.03, 0.2, 2), position(3, 0.05, 0.3, 1)];
  const cases = Array.from({ length: 21 }, (_, offset) => {
    const id = offset + 1;
    const positionId = (offset % 3) + 1;
    return {
      case_id: `case-${id}`,
      index: id,
      chunk_index: Math.floor(offset / 3),
      position_index: positionId - 1,
      position_id: positionId,
      status: "completed",
      duration_seconds: 1,
      dry_file: `/dataset/dry-${String(id).padStart(2, "0")}.wav`,
      metrics: {
        realtime_x: 10 + id,
        esr: id / 1_000,
        human_weighted_esr: id / 1_100,
        mrstft: id / 300,
        nam_esr: 0.04,
      },
      url: `/runs/run-1/cases/case-${id}`,
      position_url: `/runs/run-1/positions/${positionId}`,
    };
  });
  return {
    run: {
      id: "run-1",
      name: "Velvet Explorer",
      creator: "Studio North",
      amp_id: "detail-amp",
      amp_name: "Detail Amp",
      amp_control_count: 2,
      amp_control_names: ["gain", "tone"],
      unique_positions_used: 2,
      training_positions: [[0, 1], [1, 0]],
      training_dry_files: ["clean.wav", "drive.wav"],
      audio_duration_sum: 360,
      turns: 2,
      training_time: 7_200,
      description: "A human-readable aggregate run.",
      parameter_count: 40_000,
      status: "completed",
      total_cases: 21,
      completed_cases: 21,
      created_at: "2026-09-03T16:54:00Z",
      metrics: {
        nam_a2_speed_ratio: { mean: 1.16 },
        diagnostics: {
          findings: {
            strengths: [{ title: "Stable level", evidence: "Level error remains consistent.", scope: "all cases" }],
            significant: [{ title: "Tail at high gain", evidence: "Position 03 dominates the ESR tail.", signal_strength: 2.4 }],
          },
        },
      },
    },
    metric_distributions: metrics(0.03),
    nam_metric_distributions: metrics(0.05),
    training_coverage: {
      available: true,
      training_position_count: 2,
      training_control_count: 2,
      analyzed_settings: 3,
      esr_distance_correlation: {
        spearman_rho: 1,
        pearson_r: 0.98,
        reading: "strong positive; mean ESR tended to increase farther from training coverage",
      },
    },
    positions,
    cases,
  };
}

function markup() {
  return `<!doctype html><html><body>
    <time id="report-started"></time>
    <main id="run-overview" data-run-id="run-1">
      <section id="report-loading"></section><section id="report-error" hidden><p id="report-error-message"></p><button id="retry-report"></button></section>
      <div id="run-report-content" hidden>
        <span id="report-status"></span><h1 id="report-name"></h1><b id="report-creator"></b><a id="report-amp-link"></a><p id="report-description"></p>
        <strong id="report-progress"></strong><i id="report-progress-fill"></i><span id="report-progress-copy"></span><dl id="report-facts"></dl>
        <div id="headline-metrics"></div><table><tbody id="distribution-body"></tbody></table>
        <p id="coverage-correlation"></p><p id="coverage-summary"></p><dl id="coverage-facts"></dl><a id="coverage-worst-link"></a><p id="coverage-empty" hidden></p>
        <div><svg id="coverage-chart"></svg><div id="coverage-tooltip" hidden></div></div>
        <div id="findings-list"></div><p id="findings-empty" hidden></p>
        <select id="position-sort"><option value="error-desc">Error desc</option><option value="id-asc">ID</option></select>
        <table><tbody id="position-body"></tbody></table><p id="positions-empty" hidden></p>
        <select id="case-position-filter"><option value="all">All</option></select>
        <select id="case-sort"><option value="index">Index</option><option value="esr-desc">ESR</option></select>
        <input id="case-search"><table><tbody id="case-body"></tbody></table><p id="cases-empty" hidden></p>
        <button id="case-previous"></button><span id="case-page"></span><button id="case-next"></button>
        <p id="provenance-summary"></p><table><thead id="provenance-position-head"></thead><tbody id="provenance-position-body"></tbody></table><p id="provenance-positions-empty" hidden></p>
        <ol id="provenance-files"></ol><p id="provenance-files-empty" hidden></p>
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

test("run overview renders aggregate evidence, drill-down links, and paginated cases", async () => {
  const dom = new JSDOM(markup(), { runScripts: "outside-only", url: "https://arena.test/runs/run-1" });
  const requests = [];
  dom.window.fetch = async (url) => {
    requests.push(String(url));
    return { ok: true, status: 200, json: async () => payload() };
  };
  dom.window.eval(await readFile(SCRIPT_URL, "utf8"));
  await waitFor(() => assert.equal(dom.window.document.querySelector("#run-report-content").hidden, false));
  const document = dom.window.document;

  assert.deepEqual(requests, ["/api/v1/runs/run-1/overview"]);
  assert.equal(document.querySelector("#report-name").textContent, "Velvet Explorer");
  assert.equal(document.querySelector("#report-started").textContent, "03.09.2026 16:54");
  assert.match(document.querySelector("#headline-metrics").textContent, /Mean ESR/);
  assert.match(document.querySelector("#headline-metrics").textContent, /40\.0% lower than NAM-A2-FULL/);
  assert.match(document.querySelector("#distribution-body").textContent, /Human-weighted ESR/);
  assert.match(document.querySelector("#coverage-correlation").textContent, /Spearman \+1\.000 · Pearson \+0\.980/);
  assert.equal(document.querySelectorAll("#coverage-chart .coverage-point").length, 3);
  assert.equal(document.querySelector("#coverage-worst-link").getAttribute("href"), "/runs/run-1/positions/3");
  assert.match(document.querySelector("#findings-list").textContent, /Tail at high gain/);

  const firstPosition = document.querySelector("#position-body tr a");
  assert.equal(firstPosition.textContent, "Position 03");
  assert.equal(firstPosition.getAttribute("href"), "/runs/run-1/positions/3");
  assert.equal(document.querySelectorAll("#case-body tr").length, 20);
  assert.equal(document.querySelector("#case-page").textContent, "Page 1 / 2 · 21 cases");
  document.querySelector("#case-next").click();
  assert.equal(document.querySelectorAll("#case-body tr").length, 1);
  assert.match(document.querySelector("#case-body").textContent, /Case 021/);

  document.querySelector("#case-search").value = "dry-03";
  document.querySelector("#case-search").dispatchEvent(new dom.window.Event("input"));
  assert.equal(document.querySelectorAll("#case-body tr").length, 1);
  assert.equal(document.querySelector("#case-body a").getAttribute("href"), "/runs/run-1/cases/case-3");
  assert.equal(document.querySelector("#provenance-summary").textContent, "2 positions · 2 dry files");
  assert.match(document.querySelector("#provenance-files").textContent, /drive\.wav/);
});
