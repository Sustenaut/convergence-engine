import { useEffect, useState } from "react";
import { Button, Callout, ModeSwitch, Panel, type NeedMode } from "@sustenaut/ui";
import { api, Refused } from "./api";

type NeedChoice = { name: string; grade: string; mode: NeedMode };

/** "Start a run": a repository, a unit and Check or Fulfil, then each need's mode from the unit's
 *  compiled machine. Starting posts the run, and the page opens it. */
export function StartPage({ services, go = (to: string) => window.location.assign(to) }: { services: Record<string, string[]>; go?: (to: string) => void }) {
  const repositories = Object.keys(services).sort();
  const [repository, setRepository] = useState(repositories[0] ?? "");
  const [unit, setUnit] = useState(services[repositories[0]]?.[0] ?? "");
  const [mode, setMode] = useState<NeedMode>("fulfil");
  const [needs, setNeeds] = useState<NeedChoice[]>([]);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    if (!repository || !unit) return;
    let live = true;
    api
      .needs(repository, unit, mode)
      .then((found) => live && setNeeds(found.needs))
      .catch((e) => live && setProblem(e instanceof Refused ? e.message : "The unit's machine could not be read."));
    return () => {
      live = false;
    };
  }, [repository, unit, mode]);

  const start = async () => {
    setProblem("");
    try {
      const modes = Object.fromEntries(needs.filter((n) => n.grade === "auto").map((n) => [n.name, n.mode]));
      go(await api.start({ repository, unit, mode, modes }));
    } catch (e) {
      setProblem(e instanceof Refused ? e.message : "The engine could not be reached: try again.");
    }
  };

  const select = "min-h-11 rounded-control border border-rule-strong bg-surface px-2.5";
  return (
    <Panel title="Start a run" as="section">
      <p className="m-0 text-sm text-muted">The run opens on its own page. It plans first and asks for approval before it changes anything.</p>
      <div className="grid gap-4 sm:grid-cols-3">
        <label className="flex flex-col gap-1.5 text-sm font-bold">
          Repository
          <select
            className={select}
            value={repository}
            onChange={(e) => {
              setRepository(e.target.value);
              setUnit(services[e.target.value]?.[0] ?? "");
            }}
          >
            {repositories.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-bold">
          Unit
          <select className={select} value={unit} onChange={(e) => setUnit(e.target.value)}>
            {(services[repository] ?? []).map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </label>
        <ModeSwitch name="run" legend="Run" value={mode} onChange={setMode} />
      </div>
      {needs.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-rule pt-4">
          <div className="text-sm font-bold">Needs in {unit}, from its machine</div>
          {needs.map((n) => (
            <div key={n.name} className="flex flex-wrap items-center justify-between gap-3">
              <span>
                {n.name} <span className="text-xs text-muted">{n.grade}</span>
              </span>
              {n.grade === "auto" && mode === "fulfil" ? (
                <ModeSwitch
                  name={n.name}
                  value={n.mode}
                  onChange={(m) => setNeeds((all) => all.map((x) => (x.name === n.name ? { ...x, mode: m } : x)))}
                />
              ) : (
                <span className="text-sm text-muted">{n.grade === "auto" ? "Check" : "needs a person"}</span>
              )}
            </div>
          ))}
          <p className="m-0 mt-2 text-sm text-muted">You can switch any of these on the run page before you approve.</p>
        </div>
      )}
      {problem && <Callout tone="danger">{problem}</Callout>}
      <Button variant="primary" className="self-start" disabled={!repository || !unit} onClick={start}>
        Start and plan
      </Button>
    </Panel>
  );
}
