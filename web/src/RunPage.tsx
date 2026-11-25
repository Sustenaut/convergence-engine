import { useEffect, useRef, useState } from "react";
import {
  Callout,
  ConfirmByName,
  Journal,
  MachineStrip,
  ModeSwitch,
  NeedRow,
  NeedTable,
  Panel,
  RunHeader,
  type NeedMode,
} from "@sustenaut/ui";
import { api, Refused } from "./api";
import {
  age,
  continuing,
  endedBecause,
  journal,
  lastSeq,
  machineStates,
  merged,
  rowState,
  STOPPABLE,
  summary,
  switchable,
  type Run,
} from "./view";

/** How long Approve stays disabled after a switch changes the plan, so a person sees the new plan. */
export const SETTLE_MS = 1500;

const ENDED = ["done", "failed", "declined", "expired", "stopped"];

const stateTone: Record<string, "neutral" | "success" | "warning" | "danger"> = {
  awaiting: "warning",
  done: "success",
  failed: "danger",
};

/** The live run page: the run's header and actions, its plan, a row per need, and its journal. It
 *  polls the engine every poll_ms for the journal after the last event it holds, and redraws. */
export function RunPage({ initial, mayAct, now = () => Date.now() }: { initial: Run; mayAct: boolean; now?: () => number }) {
  const [run, setRun] = useState(initial);
  const [problem, setProblem] = useState("");
  const [settling, setSettling] = useState(false);
  const [, tick] = useState(0);
  const held = useRef(run);
  held.current = run;

  useEffect(() => {
    if (ENDED.includes(run.state)) return;
    const timer = setInterval(async () => {
      try {
        const polled = await api.poll(held.current.id, lastSeq(held.current));
        setRun((r) => merged(r, polled));
      } catch {
        // The next poll tries again; a page that lost the engine keeps what it showed.
      }
      tick((n) => n + 1); // time in state, recomputed on every redraw
    }, run.poll_ms || 2000);
    return () => clearInterval(timer);
  }, [run.state, run.poll_ms]);

  const act = async (action: () => Promise<unknown>) => {
    setProblem("");
    try {
      const answer = await action();
      if (answer && typeof answer === "object" && "id" in answer) setRun((r) => merged(r, answer as Run));
    } catch (e) {
      setProblem(e instanceof Refused ? e.message : "The engine could not be reached: try again.");
    }
  };

  const switchMode = (name: string, mode: NeedMode) =>
    act(async () => {
      const { plan_key } = await api.mode(run.id, name, mode);
      setRun((r) => ({ ...r, plan_key, needs: { ...r.needs, [name]: { ...r.needs[name], mode } } }));
      setSettling(true);
      setTimeout(() => setSettling(false), SETTLE_MS);
    });

  const awaiting = run.state === "awaiting";
  const frozen = !["planning", "awaiting"].includes(run.state) || !mayAct;
  const plan = summary(run);
  const finishing = continuing(run);
  const explain = awaiting ? (
    <>
      Set what each need should do, then approve. <b>Approving freezes these choices.</b>
    </>
  ) : run.stopping && !ENDED.includes(run.state) ? (
    <>
      Stop is in effect. Needs that hadn't started won't start.
      {finishing.length > 0 && (
        <>
          {" "}
          <b>{finishing.join(", ")}</b> {finishing.length === 1 ? "runs" : "run"} to its end unless you cancel it below.
        </>
      )}
    </>
  ) : run.state === "planning" ? (
    "The plan is being walked: each need is checked before anything changes."
  ) : run.state === "running" || run.state === "approved" ? (
    "Each need walks as its machine allows, up to the run's limit at once."
  ) : endedBecause(run) ? (
    <>
      This run ended {run.state}: <b>{endedBecause(run)}</b>
    </>
  ) : (
    `This run ended ${run.state}.`
  );

  return (
    <div className="flex flex-col gap-5">
      <RunHeader
        title={run.repository.split("/").pop() ?? run.repository}
        unit={run.unit}
        mode={run.mode}
        state={{
          label:
            run.stopping && !ENDED.includes(run.state)
              ? `Stopping${finishing.length ? ` · ${finishing.length} ${finishing.length === 1 ? "need runs" : "needs run"} to its end` : ""}`
              : run.state === "awaiting"
                ? "Awaiting approval"
                : run.state,
          tone: stateTone[run.state],
        }}
        links={run.github_run_url ? [{ label: "GitHub run", href: run.github_run_url }] : []}
        facts={[<span key="id" className="font-mono">{run.id}</span>]}
        explain={explain}
        primary={
          run.mode === "fulfil" && (awaiting || run.state === "planning")
            ? {
                label: `Approve ${plan.fulfil} ${plan.fulfil === 1 ? "fulfil" : "fulfils"}`,
                disabled: !awaiting || !mayAct || settling,
                onClick: () => act(() => api.approve(run.id, run.plan_key)),
              }
            : undefined
        }
        secondary={{
          label: run.stopping ? "Stop pressed" : "Stop",
          disabled: !mayAct || !STOPPABLE.includes(run.state) || !!run.stopping,
          onClick: () => act(() => api.stop(run.id)),
        }}
      />
      {problem && <Callout tone="danger">{problem}</Callout>}
      <Panel as="div">
        <div className="flex flex-wrap items-baseline gap-7">
          <div>
            <div className="text-xs font-bold tracking-wide text-muted uppercase">Plan</div>
            <div className="mt-1" data-testid="plan-summary">
              {plan.needs} needs · <b>{plan.fulfil} to fulfil</b> · {plan.check} check only
              {plan.person > 0 && ` · ${plan.person} ${plan.person === 1 ? "needs" : "need"} a person`}
            </div>
          </div>
          <div>
            <div className="text-xs font-bold tracking-wide text-muted uppercase" id="plan-key-label">
              Plan key
            </div>
            <div className="mt-1 font-mono text-sm" data-testid="plan-key" aria-labelledby="plan-key-label">
              {run.plan_key}
            </div>
          </div>
          <div className="max-w-prose text-sm text-muted">
            A fingerprint of the plan as it stands: each need, its state and what it should do. Approve approves
            exactly this plan, so a plan that changed meanwhile is refused.
            {awaiting && " It changes with every switch, and Approve unlocks 1.5 s after the last change."}
          </div>
        </div>
      </Panel>
      <NeedTable caption="Needs" headings={["Need", "Grade", "What it should do", "Machine", "State", "Last result", "On Stop"]}>
        {Object.entries(run.needs).map(([name, need]) => {
          const shown = rowState(run, name);
          const finishes = finishing.includes(name);
          return (
            <NeedRow
              key={name}
              name={name}
              type={need.type}
              grade={need.grade}
              mode={
                switchable(need) ? (
                  <ModeSwitch name={name} value={need.mode} disabled={frozen} onChange={(m) => switchMode(name, m)} />
                ) : (
                  "—"
                )
              }
              machine={<MachineStrip need={name} states={machineStates(run, name)} current={need.state} />}
              state={shown.state}
              stateLabel={shown.label}
              since={age(need.since, now())}
              note={finishes ? "Runs to its end" : undefined}
              result={need.result}
              stop={need.stop}
              emphasis={finishes}
            >
              {finishes && mayAct && (
                <ConfirmByName
                  name={name}
                  warning={need.stop_warning}
                  onConfirm={() => act(() => api.cancel(run.id, name))}
                />
              )}
            </NeedRow>
          );
        })}
      </NeedTable>
      <Journal entries={journal(run)} />
    </div>
  );
}
