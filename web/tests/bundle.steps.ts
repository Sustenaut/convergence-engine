// The built bundle (src/convergence/static/run.js) run in a document of its own, as a browser runs
// it: a classic script after the page's data block. It is built by `make web`, before the suite.
import { JSDOM } from "jsdom";
import { expect } from "vitest";
import { Given, Then, When } from "quickpickle";
import bundle from "../../src/convergence/static/run.js?raw";

type World = { source: string; dom: JSDOM; errors: string[] };

const run = {
  id: "r1",
  repository: "Sustenaut/integrations",
  unit: "ingest",
  mode: "fulfil",
  state: "awaiting",
  machine: null,
  plan: [],
  plan_key: "b7e2",
  needs: {},
  journal: [],
  github_run_url: "",
  expires_at: "2026-11-20T10:00:00Z",
  stopping: false,
  poll_ms: 60000,
};

async function runIt(world: World, data: unknown) {
  world.errors = [];
  world.dom = new JSDOM(
    `<!doctype html><script type="application/json" id="page-data">${JSON.stringify(data)}</script>` +
      `<main><div id="page"><h1>without script</h1></div></main>`,
    { runScripts: "outside-only", url: "https://convergence.example/runs/r1" },
  );
  const window = world.dom.window as unknown as Window & { eval: (code: string) => unknown };
  window.fetch = (async () => new Response(JSON.stringify({ needs: [] }))) as typeof fetch;
  window.addEventListener("error", (e) => world.errors.push(e.message));
  try {
    window.eval(world.source);
  } catch (e) {
    world.errors.push(String(e));
  }
  await new Promise((r) => setTimeout(r, 50));
}

Given("the built bundle", (world: World) => {
  expect(bundle.length, "src/convergence/static/run.js is empty: run make web").toBeGreaterThan(1000);
  world.source = bundle;
});

When("it runs on a page whose data block is a Start a run form for {string}", (world: World, repository: string) =>
  runIt(world, { kind: "new", services: { [repository]: ["integrations"] } }),
);

When("it runs on a page whose data block is a run awaiting approval", (world: World) =>
  runIt(world, { kind: "run", run, may_act: true }),
);

Then("it raised no error", (world: World) => {
  expect(world.errors).toEqual([]);
});

Then("the page shows the heading {string} and the button {string}", (world: World, heading: string, button: string) => {
  const page = world.dom.window.document.getElementById("page")!;
  expect([...page.querySelectorAll("h1, h2")].map((h) => h.textContent)).toContain(heading);
  expect([...page.querySelectorAll("button")].map((b) => b.textContent)).toContain(button);
});
