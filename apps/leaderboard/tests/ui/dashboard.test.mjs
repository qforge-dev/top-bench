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
    created_at: "2026-08-31T12:34:56Z",
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
    <div class="live-indicator"></div><span id="connection-label"></span>
    <span id="refresh-status"></span><span id="result-count"></span>
    <span id="summary-run-count"></span><span id="summary-completed-count"></span>
    <select id="amp-scope-filter">
      <option value="normal" selected>Normal amps</option>
      <option value="simple">Simple amps</option>
      <option value="all">All amps</option>
    </select>
    <select id="amp-filter"><option value="">All amps</option></select>
    <select id="creator-filter"><option value="">All creators</option></select>
    <input id="model-filter"><button id="clear-filters" type="button"></button>
    <table>
      <thead><tr>
        <th aria-sort="none"><button class="sort-button" data-sort="name"><span>↕</span></button></th>
        <th aria-sort="none"><button class="sort-button" data-sort="esr"><span>↕</span></button></th>
      </tr></thead>
      <tbody id="leaderboard-body"></tbody>
    </table>
    <nav id="leaderboard-pagination">
      <button id="page-previous" type="button"></button>
      <span id="current-page"></span><span id="total-pages"></span>
      <button id="page-next" type="button"></button>
    </nav>
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
  Object.defineProperty(dom.window.document, "hidden", { configurable: true, value: false });
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
  assert.equal(
    selectedRow.querySelector('td[data-label="Positions per amp parameter"]').textContent,
    "0.8333",
  );
  assert.equal(
    selectedRow.querySelector('td[data-label="Started (UTC)"] time').getAttribute("datetime"),
    "2026-08-31T12:34:56Z",
  );
  assert.equal(
    selectedRow.querySelector('td[data-label="Started (UTC)"]').textContent,
    "31.08.2026 12:34",
  );
  assert.equal(
    selectedRow.querySelector('td[data-label="Amp"] .amp-link').getAttribute("href"),
    "/amps/pg-clean",
  );
  assert.equal(selectedRow.querySelectorAll(".metric-details").length, 0);
  const esrCell = selectedRow.querySelector('td[data-label="ESR"]');
  assert.equal(esrCell.querySelector(".metric-comparison").textContent, "20.0% ▼");
  assert.equal(
    esrCell.querySelector(".metric-comparison").title,
    "20.0% lower than NAM-A2-FULL 0.25.",
  );
  assert.doesNotMatch(esrCell.textContent, /NAM-A2-FULL/);
});

test("amp set defaults to normal and switches graph, list, and amp picker together", async () => {
  const payload = {
    runs: [
      run("run-normal", "Normal Model", "pg-clean", "PG Clean"),
      run("run-simple", "Simple Model", "blackface63-simple", "Blackface 63 Simple"),
    ],
    amps: [
      { id: "pg-clean", name: "PG Clean", amp_type: "guitar", control_names: [] },
      { id: "blackface63-simple", name: "Blackface 63 Simple", amp_type: "guitar", control_names: [] },
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
  const scope = document.querySelector("#amp-scope-filter");
  const amp = document.querySelector("#amp-filter");
  assert.equal(scope.value, "normal");
  assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["run-normal"],
  );
  assert.equal(document.querySelectorAll("#pareto-chart .run-point").length, 1);
  assert.deepEqual([...amp.options].map((option) => option.value), ["", "pg-clean"]);

  scope.value = "simple";
  scope.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["run-simple"],
  );
  assert.equal(document.querySelectorAll("#pareto-chart .run-point").length, 1);
  assert.deepEqual([...amp.options].map((option) => option.value), ["", "blackface63-simple"]);

  scope.value = "all";
  scope.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(document.querySelectorAll("#leaderboard-body tr").length, 2);
  assert.equal(document.querySelectorAll("#pareto-chart .run-point").length, 2);

  document.querySelector("#clear-filters").click();
  assert.equal(scope.value, "normal");
  assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["run-normal"],
  );
});

test("server pagination keeps filtering and sorting global", async () => {
  const allRuns = [
    run("delta", "Delta", "blackface-63", "Blackface 63"),
    run("alpha", "Alpha", "pg-clean", "PG Clean"),
    run("charlie", "Charlie", "blackface-63", "Blackface 63"),
    run("bravo", "Bravo", "pg-clean", "PG Clean"),
  ];
  const amps = [
    { id: "blackface-63", name: "Blackface 63", amp_type: "guitar", control_names: [] },
    { id: "pg-clean", name: "PG Clean", amp_type: "guitar", control_names: [] },
  ];
  const responseFor = (url) => {
    const parameters = new URL(url, "https://arena.test").searchParams;
    let selected = [...allRuns];
    if (parameters.get("amp_id")) {
      selected = selected.filter((value) => value.amp_id === parameters.get("amp_id"));
    }
    if (parameters.get("sort") === "name") {
      selected.sort((left, right) => left.name.localeCompare(right.name));
    }
    if (parameters.get("direction") === "desc") selected.reverse();
    const pageSize = 2;
    const totalPages = Math.max(1, Math.ceil(selected.length / pageSize));
    const page = Math.min(Number(parameters.get("page") || 1), totalPages);
    return {
      amps,
      creators: ["Test Lab"],
      runs: selected.slice((page - 1) * pageSize, page * pageSize),
      chart_runs: selected.map((value) => ({
        id: value.id,
        name: value.name,
        amp_id: value.amp_id,
        amp_name: value.amp_name,
        amp_control_count: value.amp_control_count,
        unique_positions_used: value.unique_positions_used,
        esr: value.metrics.esr.mean,
      })),
      run_ranks: Object.fromEntries(selected.map((value, index) => [value.id, index + 1])),
      page,
      page_size: pageSize,
      total_runs: selected.length,
      total_pages: totalPages,
    };
  };
  const initial = responseFor("/?page=1");
  const requests = [];
  const dom = new JSDOM(markup(initial), {
    runScripts: "outside-only",
    url: "https://arena.test/",
  });
  Object.defineProperty(dom.window.document, "hidden", { configurable: true, value: false });
  dom.window.setInterval = () => 1;
  dom.window.fetch = async (url) => {
    requests.push(String(url));
    return { ok: true, json: async () => responseFor(url) };
  };
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const { document } = dom.window;
  document.querySelector("#page-next").click();
  await waitFor(() => assert.equal(document.querySelector("#current-page").textContent, "2"));
  assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["charlie", "bravo"],
  );
  assert.match(document.querySelector("#result-count").textContent, /3–4 of 4 runs/);
  assert.equal(new URL(requests.at(-1), "https://arena.test").searchParams.get("page"), "2");

  document.querySelector('[data-sort="name"]').click();
  await waitFor(() => assert.deepEqual(
    [...document.querySelectorAll("#leaderboard-body tr")].map((row) => row.dataset.runId),
    ["alpha", "bravo"],
  ));
  const sortedRequest = new URL(requests.at(-1), "https://arena.test").searchParams;
  assert.equal(sortedRequest.get("sort"), "name");
  assert.equal(sortedRequest.get("page"), "1");

  const ampFilter = document.querySelector("#amp-filter");
  ampFilter.value = "pg-clean";
  ampFilter.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await waitFor(() => assert.equal(document.querySelector("#result-count").textContent, "1–2 of 2 runs"));
  assert.ok(
    [...document.querySelectorAll("#leaderboard-body tr")]
      .every((row) => ["alpha", "bravo"].includes(row.dataset.runId)),
  );
  assert.equal(
    new URL(requests.at(-1), "https://arena.test").searchParams.get("amp_id"),
    "pg-clean",
  );
});

test("live progress refreshes do not replace the Pareto graph", async () => {
  const initialRun = run("run-live", "Live model", "pg-clean", "PG Clean");
  initialRun.status = "running";
  initialRun.completed_cases = 48;
  const payload = { runs: [initialRun] };
  let intervalCallback;
  let responsePayload = payload;
  const dom = new JSDOM(markup(payload), {
    runScripts: "outside-only",
    url: "https://arena.test/",
  });
  Object.defineProperty(dom.window.document, "hidden", { configurable: true, value: false });
  dom.window.setInterval = (callback) => {
    intervalCallback = callback;
    return 1;
  };
  dom.window.fetch = async () => ({ ok: true, json: async () => responsePayload });
  const script = await readFile(SCRIPT_URL, "utf8");
  dom.window.eval(script);

  const originalPoint = dom.window.document.querySelector("#pareto-chart .run-point");
  responsePayload = JSON.parse(JSON.stringify(payload));
  responsePayload.runs[0].completed_cases = 49;
  intervalCallback();
  await waitFor(() => assert.match(
    dom.window.document.querySelector('td[data-label="Progress"]').textContent,
    /49\/50/,
  ));
  assert.strictEqual(
    dom.window.document.querySelector("#pareto-chart .run-point"),
    originalPoint,
  );
});

test("Pareto chart plots positions per control against ESR on a logarithmic scale", async () => {
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
  const markers = [...document.querySelectorAll("#pareto-chart .run-marker")];
  assert.equal(markers.length, points.length);
  const labelledMarkers = markers.filter((marker) => marker.querySelector(".point-label"));
  assert.equal(labelledMarkers.length, 1);
  assert.equal(labelledMarkers[0].querySelector(".point-label").textContent, "run-low");
  assert.ok(labelledMarkers.every((marker) => {
    const label = marker.querySelector(".point-label");
    return marker.querySelector(".key-label-line")
      && label.getAttribute("dominant-baseline") === "middle";
  }));
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

test("Pareto chart caps labels while leaving every run available as a point", async () => {
  const runs = Array.from({ length: 20 }, (_, index) => {
    const value = run(`frontier-${index + 1}`, `Frontier ${index + 1}`, "blackface-63", "Blackface 63");
    value.unique_positions_used = (index + 1) * 7;
    value.metrics.esr.mean = 0.2 / (index + 1);
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
  assert.equal(document.querySelectorAll("#pareto-chart .run-point").length, 20);
  assert.equal(document.querySelectorAll("#pareto-chart .point-label").length, 8);
  assert.equal(document.querySelectorAll("#pareto-chart .run-point.on-frontier").length, 20);
});

test("Pareto chart favors fewer training positions per knob and switch", async () => {
  const values = [
    ["broad", 10, 40, 0.1],
    ["narrow", 5, 39, 0.1],
    ["accurate", 5, 40, 0.05],
    ["dominated", 5, 30, 0.2],
  ];
  const runs = values.map(([id, controls, positions, esr]) => {
    const value = run(id, id, "blackface-63", "Blackface 63");
    value.amp_control_count = controls;
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
  const points = new Map(
    [...document.querySelectorAll("#pareto-chart .run-point")]
      .map((point) => [point.getAttribute("aria-label").split(":", 1)[0], point]),
  );
  assert.ok(
    Number(points.get("broad").getAttribute("cx"))
      < Number(points.get("narrow").getAttribute("cx")),
  );
  assert.match(points.get("broad").getAttribute("aria-label"), /4 positions per knob or switch/);
  assert.match(points.get("broad").getAttribute("aria-label"), /on the Pareto frontier/);
  assert.doesNotMatch(points.get("narrow").getAttribute("aria-label"), /on the Pareto frontier/);
  assert.match(points.get("accurate").getAttribute("aria-label"), /on the Pareto frontier/);
  assert.doesNotMatch(
    points.get("dominated").getAttribute("aria-label"),
    /on the Pareto frontier/,
  );
  assert.match(document.querySelector("#pareto-chart .axis-title").textContent, /lower is better/i);
});
