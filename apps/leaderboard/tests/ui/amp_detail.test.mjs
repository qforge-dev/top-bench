import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

const SCRIPT_URL = new URL("../../src/top_arena_server/static/amp_detail.js", import.meta.url);

function run(id, name, esr, realtime, budget) {
  return {
    id,
    name,
    creator: "Test Lab",
    amp_id: "blackface-63",
    amp_name: "Blackface 63",
    status: "completed",
    total_cases: 150,
    completed_cases: 150,
    unique_positions_used: 165,
    audio_duration_sum: budget,
    metrics: {
      esr: { mean: esr },
      human_weighted_esr: { mean: esr + 0.002 },
      mrstft: { mean: 0.42 + esr },
      realtime_x: { mean: realtime },
      nam_a2_full: {
        esr: { mean: 0.0842 },
        human_weighted_esr: { mean: 0.0433 },
        mrstft: { mean: 0.4412 },
      },
    },
  };
}

function markup(payload) {
  return `<!doctype html><html><body>
    <main class="amp-main" data-amp-id="blackface-63">
      <span id="amp-run-count"></span><span id="amp-case-count"></span>
      <ol id="amp-standings"></ol><p id="standings-empty" hidden></p>
      <h2 id="comparison-title"></h2><p id="comparison-guidance"></p>
      <div class="comparison-tabs" role="tablist">
        <button role="tab" data-chart-mode="speed" aria-selected="true">Speed</button>
        <button role="tab" data-chart-mode="positions" aria-selected="false">Positions</button>
        <button role="tab" data-chart-mode="budget" aria-selected="false">Recording budget</button>
      </div>
      <div><svg id="amp-comparison-chart"></svg><p id="comparison-description"></p><div id="amp-chart-tooltip" hidden></div></div>
      <input id="amp-model-filter"><table><tbody id="amp-model-body"></tbody></table><p id="model-filter-empty" hidden></p>
      <section id="selected-model"><h2 id="selected-model-title"></h2><a id="selected-model-link"></a><div id="selected-summary"></div><div id="selected-profile"></div></section>
      <section id="amp-empty-state" hidden></section>
    </main>
    <script id="amp-initial-data" type="application/json">${JSON.stringify(payload)}</script>
  </body></html>`;
}

test("amp page scales model selection through a searchable table and chart tabs", async () => {
  const payload = {
    runs: [
      run("v22", "Blackface v22 · 30s", 0.0571, 18.7, 30),
      run("v21", "Blackface v21 · 60s", 0.0357, 4.93, 60),
    ],
  };
  const dom = new JSDOM(markup(payload), { runScripts: "outside-only", url: "https://arena.test/amps/blackface-63" });
  dom.window.setInterval = () => 1;
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const { document } = dom.window;
  assert.equal(document.querySelector("#amp-run-count").textContent, "2 model runs");
  assert.equal(document.querySelectorAll("#amp-model-body tr").length, 2);
  assert.equal(document.querySelector("#selected-model-title").textContent, "Blackface v21 · 60s");
  assert.equal(document.querySelector("#selected-model-link").getAttribute("href"), "/runs/v21");
  assert.equal(document.querySelectorAll("#amp-comparison-chart .amp-run-point").length, 2);
  assert.equal(document.querySelector("#amp-standings .amp-link").getAttribute("href"), "/runs/v21");

  document.querySelector('[data-chart-mode="positions"]').click();
  assert.equal(document.querySelector("#comparison-title").textContent, "Quality vs positions");
  assert.match(document.querySelector("#comparison-guidance").textContent, /leaner/);

  document.querySelector('[data-chart-mode="budget"]').click();
  assert.equal(document.querySelector("#comparison-title").textContent, "Quality vs recording budget");
  assert.match(document.querySelector("#amp-comparison-chart .amp-axis-title").textContent, /recording budget/i);

  document.querySelector('[data-run-id="v22"]').click();
  assert.equal(document.querySelector("#selected-model-title").textContent, "Blackface v22 · 30s");
  assert.equal(document.querySelector("#selected-model-link").getAttribute("href"), "/runs/v22");
  assert.equal(document.querySelector("#amp-model-body tr.is-selected").dataset.runId, "v22");

  const filter = document.querySelector("#amp-model-filter");
  filter.value = "v21";
  filter.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.deepEqual(
    [...document.querySelectorAll("#amp-model-body tr")].map((row) => row.dataset.runId),
    ["v21"],
  );
});
