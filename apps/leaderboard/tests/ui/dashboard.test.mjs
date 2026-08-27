import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

const SCRIPT_URL = new URL("../../src/top_arena_server/static/dashboard.js", import.meta.url);

function run(id, name, ampId, ampName) {
  return {
    id,
    name,
    creator: "Test Lab",
    amp_id: ampId,
    amp_name: ampName,
    amp_type: "guitar",
    amp_control_count: ampId === "pg-clean" ? 6 : 7,
    unique_positions_used: 5,
    audio_duration_sum: 250,
    turns: 1,
    training_time: 10,
    description: `${ampName} model`,
    parameter_count: 1_000,
    status: "completed",
    total_cases: 50,
    completed_cases: 50,
    metrics: {
      esr: { mean: id === "run-blackface" ? 0.1 : 0.2 },
      human_weighted_esr: { mean: 0.2 },
      mrstft: { mean: 0.3 },
      realtime_x: { mean: 10 },
      nam_a2_full: {
        available_cases: 50,
        esr: { mean: 0.25 },
        human_weighted_esr: { mean: 0.4 },
        mrstft: { mean: 0.5 },
      },
    },
  };
}

function markup(payload) {
  return `<!doctype html><html><body>
    <div class="live-indicator"></div><span id="connection-label"></span>
    <span id="refresh-status"></span><span id="result-count"></span>
    <span id="summary-run-count"></span><span id="summary-completed-count"></span>
    <select id="amp-filter"><option value="">All amps</option></select>
    <select id="creator-filter"><option value="">All creators</option></select>
    <input id="model-filter"><button id="clear-filters" type="button"></button>
    <table><tbody id="leaderboard-body"></tbody></table>
    <svg id="pareto-chart" viewBox="0 0 960 430"></svg>
    <div id="chart-tooltip" hidden></div>
    <script id="leaderboard-initial-data" type="application/json">${JSON.stringify(payload)}</script>
  </body></html>`;
}

test("amp filter lists database amps and filters runs by amp id", async () => {
  const payload = {
    runs: [
      run("run-blackface", "Blackface Model", "blackface-63", "Blackface 63"),
      run("run-pg", "PG Model", "pg-clean", "PG Clean"),
    ],
    amps: [
      { id: "blackface-63", name: "Blackface 63", amp_type: "guitar", control_names: [] },
      { id: "pg-clean", name: "PG Clean", amp_type: "guitar", control_names: [] },
      { id: "unused-amp", name: "Unused Amp", amp_type: "guitar", control_names: [] },
    ],
  };
  const dom = new JSDOM(markup(payload), {
    runScripts: "outside-only",
    url: "https://arena.test/",
  });
  dom.window.setInterval = () => 1;
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const { document } = dom.window;
  const filter = document.querySelector("#amp-filter");
  assert.deepEqual(
    [...filter.options].map((option) => [option.value, option.textContent]),
    [
      ["", "All amps"],
      ["blackface-63", "Blackface 63"],
      ["pg-clean", "PG Clean"],
      ["unused-amp", "Unused Amp"],
    ],
  );

  filter.value = "pg-clean";
  filter.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

  assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["run-pg"],
  );
  assert.equal(document.querySelector("#result-count").textContent, "1 run of 2");
  const selectedRow = document.querySelector("#leaderboard-body tr");
  assert.equal(selectedRow.querySelector(".model-cell p"), null);
  assert.equal(selectedRow.querySelector(".model-metadata"), null);
  assert.equal(selectedRow.querySelector("progress"), null);
  assert.doesNotMatch(selectedRow.querySelector('td[data-label="Amp"]').textContent, /guitar/i);
  assert.equal(selectedRow.querySelector('td[data-label="Amp parameters"]').textContent, "6");
  assert.equal(selectedRow.querySelectorAll(".metric-details").length, 0);
  const esrCell = selectedRow.querySelector('td[data-label="ESR"]');
  assert.match(esrCell.textContent, /NAM-A2-FULL\s+0\.25/);
  assert.match(esrCell.textContent, /Model 20\.0% lower/);
});

test("Pareto chart plots positions against ESR on a logarithmic scale", async () => {
  const values = [
    ["run-low", 1, 0.001],
    ["run-mid", 5, 0.01],
    ["run-high", 10, 0.1],
  ];
  const runs = values.map(([id, positions, esr]) => {
    const value = run(id, id, "blackface-63", "Blackface 63");
    value.unique_positions_used = positions;
    value.metrics.esr.mean = esr;
    return value;
  });
  const dom = new JSDOM(markup({ runs }), {
    runScripts: "outside-only",
    url: "https://arena.test/",
  });
  dom.window.setInterval = () => 1;
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const { document } = dom.window;
  const points = [...document.querySelectorAll("#pareto-chart .run-point")];
  assert.equal(points.length, 3);
  const yByRun = new Map(points.map((point) => [
    point.getAttribute("aria-label").split(":", 1)[0],
    Number(point.getAttribute("cy")),
  ]));
  const upperGap = yByRun.get("run-mid") - yByRun.get("run-high");
  const lowerGap = yByRun.get("run-low") - yByRun.get("run-mid");
  assert.ok(Math.abs(upperGap - lowerGap) < 0.001, "each ESR decade should occupy equal height");
  assert.match(document.querySelector("#pareto-chart .axis-title:last-of-type").textContent, /log/i);
  assert.deepEqual(
    [...document.querySelectorAll("#pareto-chart .axis-text")]
      .map((label) => label.textContent)
      .filter((label) => label.includes("e-") || label.startsWith("0.")),
    ["0.001", "0.01", "0.1"],
  );
});
