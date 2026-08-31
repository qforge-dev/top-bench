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
    amp_control_count: 7,
    unique_positions_used: 165,
    created_at: "2026-08-31T12:34:56Z",
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
  assert.deepEqual(
    [...document.querySelectorAll("#amp-comparison-chart .amp-point-label")].map((label) => label.textContent),
    ["Blackface v22 · 30s", "Blackface v21 · 60s"],
  );
  assert.equal(document.querySelector("#amp-standings .amp-link").getAttribute("href"), "/runs/v21");
  const selectedRow = document.querySelector('[data-run-id="v21"]');
  assert.equal(selectedRow.querySelector('td[data-label="Amp parameters"]').textContent, "7");
  assert.equal(
    selectedRow.querySelector('td[data-label="Positions per amp parameter"]').textContent,
    "23.5714",
  );
  assert.equal(
    selectedRow.querySelector('td[data-label="Started (UTC)"] time').getAttribute("datetime"),
    "2026-08-31T12:34:56Z",
  );
  assert.equal(
    selectedRow.querySelector('td[data-label="Started (UTC)"]').textContent,
    "31.08.2026 12:34",
  );

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

test("live progress refreshes do not replace the amp comparison graph", async () => {
  const liveRun = run("live", "Live model", 0.04, 12, 60);
  liveRun.status = "running";
  liveRun.completed_cases = 148;
  const payload = { runs: [liveRun] };
  let intervalCallback;
  let responsePayload = payload;
  const dom = new JSDOM(markup(payload), {
    runScripts: "outside-only",
    url: "https://arena.test/amps/blackface-63",
  });
  Object.defineProperty(dom.window.document, "hidden", { configurable: true, value: false });
  dom.window.setInterval = (callback) => {
    intervalCallback = callback;
    return 1;
  };
  dom.window.fetch = async () => ({ ok: true, json: async () => responsePayload });
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const originalPoint = dom.window.document.querySelector("#amp-comparison-chart .amp-run-point");
  responsePayload = JSON.parse(JSON.stringify(payload));
  responsePayload.runs[0].completed_cases = 149;
  intervalCallback();
  await waitFor(() => assert.match(
    dom.window.document.querySelector("#selected-summary").textContent,
    /149 \/ 150/,
  ));
  assert.strictEqual(
    dom.window.document.querySelector("#amp-comparison-chart .amp-run-point"),
    originalPoint,
  );
});
