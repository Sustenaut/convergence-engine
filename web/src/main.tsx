// The run page's bundle (/static/run.js): it reads what the engine embedded in the page (a JSON data
// block, #page-data), draws the run, or the Start a run form, in place of the page's script-less
// view, and keeps it current.
import { createRoot } from "react-dom/client";
import { RunPage } from "./RunPage";
import { StartPage } from "./StartPage";
import type { Run } from "./view";

type PageData =
  | { kind: "run"; run: Run; may_act: boolean }
  | { kind: "new"; services: Record<string, string[]> };

const block = document.getElementById("page-data");
const root = document.getElementById("page");
if (block && root) {
  const data = JSON.parse(block.textContent ?? "{}") as PageData;
  root.replaceChildren();
  createRoot(root).render(
    data.kind === "run" ? <RunPage initial={data.run} mayAct={data.may_act} /> : <StartPage services={data.services} />,
  );
}
