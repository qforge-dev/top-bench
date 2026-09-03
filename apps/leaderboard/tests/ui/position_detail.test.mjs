import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

const SCRIPT_URL = new URL("../../src/top_arena_server/static/position_detail.js", import.meta.url);

function distribution(mean, { median = mean, p90 = mean * 1.5, best = mean * 0.5, worst = mean * 2, count = 2 } = {}) {
  return { count, mean, median, p90, best, worst };
}

function metricSet(esr) {
  return {
    esr: distribution(esr), human_weighted_esr: distribution(esr * 0.9), mrstft: distribution(esr * 4),
    level_db: distribution(0.2), peak_db: distribution(0.3), correlation: distribution(0.95, { best: 0.99, worst: 0.9 }),
    realtime_x: distribution(18, { best: 20, worst: 16 }),
  };
}

function payload() {
  return {
    run: {
      id: "run-1", name: "Velvet Explorer", amp_id: "detail-amp", amp_name: "Detail Amp",
      status: "completed", created_at: "2026-09-03T16:54:00Z",
    },
    run_metric_distributions: metricSet(0.025),
    training_coverage: {
      analyzed_settings: 5,
      training_control_count: 2,
      esr_distance_correlation: {
        spearman_rho: 0.7,
        reading: "strong positive; mean ESR tended to increase farther from training coverage",
      },
    },
    position: {
      position_id: 2,
      position_index: 1,
      positions: [[0.7, 0.8]],
      control_names: ["gain", "tone"],
      total_cases: 2,
      completed_cases: 2,
      esr_error_rank: 1,
      metrics: metricSet(0.04),
      training_coverage: {
        nearest_training_distance: 0.12,
        nearest_training_points: [{ measured_step: 1, training_position_id: 4, distance: 0.12, training_position: [0.6, 0.7] }],
      },
      url: "/runs/run-1/positions/2",
    },
    cases: [
      {
        case_id: "case-a", index: 2, chunk_index: 0, position_index: 1, position_id: 2, status: "completed", duration_seconds: 1,
        dry_file: "/dataset/clean.wav", url: "/runs/run-1/cases/case-a", position_url: "/runs/run-1/positions/2",
        metrics: { esr: 0.02, human_weighted_esr: 0.018, mrstft: 0.08, realtime_x: 18, nam_esr: 0.03 },
      },
      {
        case_id: "case-b", index: 5, chunk_index: 1, position_index: 1, position_id: 2, status: "completed", duration_seconds: 1,
        dry_file: "/dataset/heavy.wav", url: "/runs/run-1/cases/case-b", position_url: "/runs/run-1/positions/2",
        metrics: { esr: 0.08, human_weighted_esr: 0.07, mrstft: 0.3, realtime_x: 17, nam_esr: 0.05 },
      },
    ],
  };
}

function markup() {
  return `<!doctype html><html><body>
    <time id="position-started"></time>
    <main id="position-detail" data-run-id="run-1" data-position-id="2">
      <section id="position-loading"></section><section id="position-error" hidden><p id="position-error-message"></p><button id="retry-position"></button></section>
      <div id="position-report-content" hidden>
        <a id="position-run-link"></a><span id="position-status"></span><span id="position-run-name"></span><b id="position-number"></b><p id="position-summary"></p>
        <strong id="position-rank"></strong><span id="position-rank-copy"></span><div id="position-controls"></div><div id="position-insights"></div>
        <table><tbody id="position-distribution-body"></tbody></table><p id="position-distance-label"></p><p id="neighborhood-reading"></p><dl id="neighborhood-facts"></dl>
        <div id="nearest-training-controls"></div><p id="neighborhood-empty" hidden></p>
        <div><svg id="position-case-chart"></svg><div id="position-chart-tooltip" hidden></div></div><p id="position-chart-summary"></p>
        <table><tbody id="position-case-body"></tbody></table><p id="position-cases-empty" hidden></p>
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

test("position report explains controls, training proximity, distribution, and dry-input cases", async () => {
  const dom = new JSDOM(markup(), { runScripts: "outside-only", url: "https://arena.test/runs/run-1/positions/2" });
  const requests = [];
  dom.window.fetch = async (url) => {
    requests.push(String(url));
    return { ok: true, status: 200, json: async () => payload() };
  };
  dom.window.eval(await readFile(SCRIPT_URL, "utf8"));
  await waitFor(() => assert.equal(dom.window.document.querySelector("#position-report-content").hidden, false));
  const document = dom.window.document;

  assert.deepEqual(requests, ["/api/v1/runs/run-1/positions/2"]);
  assert.equal(document.querySelector("#position-run-name").textContent, "Velvet Explorer");
  assert.equal(document.querySelector("#position-rank").textContent, "#1");
  assert.match(document.querySelector("#position-rank-copy").textContent, /of 5 measured settings/);
  assert.match(document.querySelector("#position-controls").textContent, /gain0\.7000/);
  assert.match(document.querySelector("#position-insights").textContent, /60\.0% higher than the whole-run mean/);
  assert.match(document.querySelector("#position-distribution-body").textContent, /Human-weighted ESR/);
  assert.equal(document.querySelector("#position-distance-label").textContent, "Distance 0.1200");
  assert.match(document.querySelector("#neighborhood-reading").textContent, /normalized RMS distance/i);
  assert.match(document.querySelector("#neighborhood-facts").textContent, /\+0\.700/);
  assert.match(document.querySelector("#nearest-training-controls").textContent, /tone0\.7000/);
  assert.equal(document.querySelectorAll("#position-case-chart .position-bar").length, 2);
  assert.equal(document.querySelectorAll("#position-case-chart .position-nam-marker").length, 2);
  assert.equal(document.querySelectorAll("#position-case-body tr").length, 2);
  assert.equal(document.querySelector("#position-case-body a").getAttribute("href"), "/runs/run-1/cases/case-a");
  assert.match(document.querySelector("#position-case-body").textContent, /heavy\.wav/);
});
