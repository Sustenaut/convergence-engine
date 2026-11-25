# convergence-engine

Walks every deploy service read-only every 30 minutes, shows each need's state on a
page per service at <https://convergence.sustenaut.eu>, and journals every walk.

It is the second host for `pubdeploy walk`, beside the laptop, with its own narrower
credentials. This is step 4a of the operating plane (the phase 4 spec): it reads and
changes nothing. Fulfilling from the page is step 4b, Google keys step 4c, and
requests a person approves on the page (for go-live workflows and other callers) the
step after.

## What it does

A new pod lists the services at once: the state repositories' services, and each
one's deploy units as its `pubrules.json` names them (the deploy block's
`namespace`, then each of its `units`), walking nothing, so the overview and Start a
run show them while the first round waits 5 minutes (`CONVERGENCE_FIRST_ROUND_SECONDS`),
not to judge the engine's own deploy mid-rollout. Each round reads the units again.

A round runs after that wait and then every 30 minutes, one service after another:

1. **The list.** It refreshes a shallow clone of each state repository
   (`CONVERGENCE_STATES`, by default `Sustenaut/release-state`) and takes every
   repository whose entry has the role `deploy`. The state says whether a repository
   is walked; its own `pubrules.json` deploy block says how.
2. **The clones.** Each owner's zones repository (`CONVERGENCE_ZONES`, by default
   Sustenaut's `shared-infra`) and each service are shallow clones of their default
   branch, side by side under `/work/clones/OWNER/`, refreshed before each walk.
   `/work` is the claim `engine-work`, so the clones, the launchers' wheels (fetched
   from GitHub, else storage's wheel mirror, each checked against its sha256) and
   uv's cache outlive a restart.
3. **The walk.** In the service's clone it runs the repository's own pubdeploy:

   ```sh
   ./pubdeploy check --json --journal https://storage.sustenaut.eu --zones ../shared-infra/pubrules.json
   ```

   through `./pubsec run --` when there is a Bitwarden read token, so the walk uses
   the pubdeploy, pubsec and need types that repository pins, never the engine's.
   `check` is a walk that cannot fulfil. kubectl runs as the pod's ServiceAccount
   (pubdeploy's `plane` channel, `PUBRULES_PLANE`), with the reads
   `k8s/admin/cluster-role.yaml` grants and, in the namespaces
   `k8s/admin/secret-readers.yaml` lists, Secrets' key names and lengths. That file
   is generated from the service list, with every service checked out beside this
   repository: `python -m convergence secret-readers ../release-state/state.json ..`.
4. **The journal.** pubdeploy writes each unit's deploy-journal/2 document to
   storage's private tenant `plane` and moves the unit's head
   (`<owner>.<repository>.<unit>`) to it, as from a laptop, with `from: plane` and
   `by: schedule`. The head's log is the walk history the page shows.
5. **The facts.** Each need's state in each walk is one row of an Avro file, put in
   storage's `need-states` buffer. The integrations ingest pod loads it into
   BigQuery, and observability's rule `need_drift` finds a need that was `ready` in
   one walk and is not in the next. The engine says what it found, never that
   something is wrong: the rule, its signal and the notifier's post are theirs.

A service whose clone or walk fails keeps its last good walk on its page, with the
failure beside it, and the round goes on.

## The pages

Behind oauth2-proxy and the team sign-in, for people with the role `convergence`
(the team access page at auth.sustenaut.eu/realms/team/access is the one list):

| Path | What |
|---|---|
| `/` | every service, each unit's counts, its last walk, and a failure if the latest try failed |
| `/services/OWNER/NAME` | each unit's needs: type, grade, state, when checked, what was found, and what makes it ready (the proposal or the person's step, or for an auto need the Fulfil button); and the unit's walks |
| `/services/OWNER/NAME/walks/HASH` | one journaled walk, from storage |
| `POST /services/OWNER/NAME/fulfil` | a person's Fulfil of one unit (the button): a request of theirs, which they approve once its plan is walked |
| `/approvals` | the open requests, oldest first, then the last 50 ended: kind, repository and unit (or the caller), who asked, age and state; the front page links here with the number awaiting |
| `/approvals/ID` | one request: its plan and the plan's sha256, where it came from (with a link to the GitHub run), Approve and Decline while it awaits, each state change with who and when, and a Fulfil's output and end states; it reloads itself while it works |
| `POST /approvals/ID` | a person's Approve or Decline |
| `/fulfils/ID` | 4b's links: redirects to `/approvals/ID` |
| `GET /runs/ID` | a live run's page for a browser: drawn once with its public view as a JSON data block, then kept current by `/static/run.js`; without script it refreshes every 5 s. CSP `script-src 'self'; connect-src 'self'` |
| `GET /runs/new` | Start a run, for a signed-in person: repository, unit and mode, then each need's mode from the unit's compiled machine (`GET /runs/new/needs?repository=R&unit=U&mode=M`, JSON) |
| `GET /static/run.js`, `/static/run.css` | the run page's bundle and styles (`web/`: React on @sustenaut/ui and the theme), built into the image; nothing loads from anywhere else |
| `GET /runs/ID?since=SEQ` | a live run (JSON, also for `Accept: application/json`): its state, machine, plan, plan key, each need's state, mode, grade, type, stop class and result, its journal after SEQ, and `poll_ms` (2000); public() throughout, and kept an hour once it has ended |
| `POST /runs` | a signed-in approver's live run, JSON `{repository, unit, mode: check\|fulfil, modes?}`: a request of theirs, which awaits approval as any (a check needs none) |
| `POST /runs/ID/approve` | approve it, JSON `{plan_key}`: the request's approval (the same rule), which freezes the modes |
| `POST /runs/ID/stop` | before approval, decline; while it walks, stop (each need as its class says); 204 once it has ended |
| `POST /runs/ID/needs/NAME/mode` | switch a need to check or fulfil, before approval only (409 after): the plan's key again |
| `POST /runs/ID/needs/NAME/cancel` | cancel a need that must finish (stop class continue) while it fulfils: JSON `{"confirm": "NAME"}`, its name typed |
| `/theme/…` | the vendored theme's files, as `theme.json` pins them |
| `/healthz` | open; 200 while rounds end in time |

Open without the sign-in, for callers with a bearer token (oauth2-proxy's
`--skip-auth-route`; the engine reads no `X-Forwarded-*` header there):

| Path | What |
|---|---|
| `POST /runs/ID/events` | a remote executor's event, `{seq, need, from, to, result?, at}`, with the run's own bearer token: `seq` is the need's own, one more than its last, and `from` its state (409 otherwise); idempotent on the need and its seq |
| `POST /requests` | a caller's request, JSON: `{"kind": "fulfil", "unit": "U"}` (with `"mode": "live"` and maybe `"modes": {NEED: "check"\|"fulfil"}`, a live run), `{"kind": "check", "unit": "U"}` (read-only: the plan walk, then done with each need's state, asking no one) or `{"kind": "gate", "plan": "text"}`; 201 with the request, or 4xx/503 with `{"refused": "<reason>"}` and nothing stored |
| `GET /requests/ID` | the request's state, its plan (each need's name, state and what the fulfil changes) and result (every need's name, type, grade, state and what the walk found, the error, or a note), for the identity that asked: the same run, or the same ServiceAccount; poll every 15 s. It never names who approved it (a run's log may be public): the page and the journal do |

The pages are plain HTML with no script, `Cache-Control: private, no-store`, and every
string from a walk or a caller escaped. They wear Sustenaut's theme
([Sustenaut/theme](https://github.com/Sustenaut/theme)): its `fonts.css`, `tokens.css`,
`base.css` and fonts are vendored in `src/convergence/theme/`, `src/convergence/theme.json`
pins the tag and each file's sha256, and the engine serves them itself at `/theme/…`
(no CDN: the CSP allows only the site's own styles and fonts). A file that differs from
its pin is not served. To move to a new tag, copy the same files from it and update
`theme.json`.

**Buttons are always shown.** Each unit's Fulfil is at the top of its service page and
again on the unit; Approve and Decline are on every request page. One that cannot be used
now is disabled, and says why, on hover and beside it (for a screen reader too): not
signed in with the role `convergence`, nothing to fulfil (every need ready), nothing the
engine can fulfil (a person's step or a proposal left), a request for that unit already
open, a walk of the service running, an engine without a write token; or for a request,
its plan still being walked, the person's own Fulfil, or a state that no longer awaits a
decision.

**Requests.** A request is something a caller wants done that a person approves first
(`approvals.py`). A `fulfil` runs `./pubdeploy walk --fulfil --yes --unit U` once
approved; a `gate` ends at approved, and its caller goes on by itself. States only move
forward: `planning` (a fulfil's plan walk) → `awaiting` → `approved` | `declined` |
`expired` (after `CONVERGENCE_REQUEST_HOURS`, 24); a gate has ended once approved; a fulfil then `running` → `done` |
`failed`. A fulfil's plan is a read-only walk of the unit (`check --unit U`) on
arrival: each need that is not ready, by name, type, state and grade, and what its
fulfil changes, names only. The approval is bound to the plan's sha256: the unit is
walked read-only again just before the Fulfil, and if that plan differs the request
ends `failed`, "plan changed since approval". A unit with nothing to do is `done` at
once, with no approval, and so is one whose plan holds nothing the fulfil changes (no
auto need missing or drifted): it ends `failed`, "nothing the engine can fulfil; needs a
person: …" with each need and what was found (a need missing, drifted or blocked that
only a person mends), or `done` with that list as its note when the rest is only what the
engine cannot judge from where it walks (an admin channel's check, and the gate after it).
Nobody is asked to approve a plan the fulfil would change nothing of. A gate's plan is the caller's text, 8 KB at most.

Who may ask (`callers.py`), with `Authorization: Bearer`:

- a **GitHub Actions OIDC token** (`id-token: write`, audience
  `https://convergence.sustenaut.eu`), checked against GitHub's JWKS: its issuer, its
  audience, its expiry, a repository owner in `CONVERGENCE_REQUEST_OWNERS`
  (Sustenaut), and `ref` refs/heads/main. The run is recorded (repository,
  repository_id, sha, workflow_ref, run_id, actor), and a fulfil may only name the
  token's own repository's units;
- a **Kubernetes ServiceAccount's token** (audience the same), checked by the cluster's
  TokenReview, for the ServiceAccounts `CONVERGENCE_REQUESTERS` lists
  (`integrations:app`); gates only. Only a JWT whose issuer is the cluster's
  (`https://kubernetes.default.svc.cluster.local`) is sent to the TokenReview; any
  other token is refused without asking;
- a **person**: the Fulfil button is their request and their approval in one.

**Approve and Decline** are for a person with the role `convergence` (oauth2-proxy's
`X-Forwarded-Email` and `X-Forwarded-Groups`, which only the proxy can set: the network
policy lets nothing else reach the engine). The form carries a token made from that
person, the request and its plan's hash, and must come from this site (`Origin`). A
fulfil's walk records the approver (`PUBRULES_WALK_BY`).

**The record.** Every state change appends one event (`convergence-approval/1`: id,
kind, state, at, by, repository, unit, sha, run url, plan sha256, the end states'
need names and states; never a value) to storage's tenant `plane`, head
`convergence.approvals`, before it counts. A request whose first event is refused is
not taken (503 `journal-refused`), and an approval the journal refuses does not happen.
Each event also holds the open requests and the last ended ones, so a new pod picks
them up from the head: a request that was planning or running ends `failed`, and an
approved fulfil runs.

**Live runs** (`runs.py`, `executor.py`). A fulfil or check asked with `"mode": "live"`
(inline, the unit in one walk, stays the default) is walked need by need. Its plan walk is
the request's; its machine is the service's own, `pubdeploy machine --unit U --mode M
[--modes NEED=MODE,...]` (XState v5 JSON: a parallel state `running`, one child per need,
each need's meta naming its mode, grade, type, stop class, after-edges and budget), kept
with the run so a later change of the deploy block never rewrites a run in flight. A
need moves through `waiting`, `checking`, then `ready`, or `found` (missing or drift),
which a fulfil need of grade auto leaves for `fulfilling` and a check again; `failed`,
`blocked`, `stopped` and `unknown` (the walk could not judge it) end it too. The executor walks each need by itself, `pubdeploy
walk --unit U --need NAME --budget-seconds S [--fulfil --yes]`, once every need it comes
after has ended (after one that failed, was blocked, stopped or left unknown, it is blocked), as many at
once as the machine's `parallel` (4) allows, one run of a service at a time; a walk past
its need's budget and 60 s more fails it. A need's modes switch until approval, which
freezes them, and the plan's key, which the approval binds to, counts them. Stop before
approval declines; while it walks, what has not started never starts, and a need walking
stops as its class says: `signal` (terminated), `shorten` (SIGUSR1: pubdeploy's walk ends
the step `unknown`, its journal kept) or `continue` (it finishes, noted; only its own cancel, its name typed, ends it). A run ends
`stopped` when a person stopped it, `failed` when a need failed or was blocked, else
`done` (noting each need left unknown), its request in the same state (`stopped` is new to a
caller), and each need's journal goes to the need-states facts. Each move is an event of
the run's journal, written with the run to storage's head `convergence.runs` (tenant
`plane`) before it counts; a new pod reads the runs back from it: one awaiting approval
waits on, one that was walking ended with its pod, so its needs not ended fail ("executor
lost") and nothing walks again. What a run shows is public() (no email, no link).

**Fulfil** (step 4b) runs in the service's clone and makes each auto need that is
missing or drifted. A service is never walked by two at once: a request's walks wait
for a round or another request to end, and a Fulfil may take
`CONVERGENCE_FULFIL_SECONDS` (1200). Its walk alone gets the installation token that
may write and the Bitwarden write token's file: every round mints a token limited to
read and runs `check`, so it cannot write. `k8s.namespace` stays an admin's step: the
deploy channel needs a kubeconfig on the node that only `sustenaut-app` writes.

## Credentials

Every credential is a file mounted in the pod, read when used, and handed to a walk's
subprocess in an environment built from nothing (PATH, HOME, the cluster's address,
`GH_TOKEN`, `PUBRULES_STORAGE_TOKEN`, `PUBRULES_WALK_BY`, and `BWS_ACCESS_TOKEN` when
there is one). No value is on a command line, in a remote URL or in a log.
Through `./pubsec run`, a walk's child process also gets every value the walked
repository's with-secrets.json project gives it, as deploy-check.yml's walk does on
Actions: only the read account's token can read them, and only that child holds them.

| What | Where | For |
|---|---|---|
| GitHub App "Sustenaut convergence" (`github-app.json`): contents and metadata read; actions write (`deploy.rollout`, `service.health`: dispatching the deploy workflow), secrets write (`github.secret`, `github.deploy-settings`), variables write (`github.variable`, `github.gate`), environments write (`github.deploy-settings` in an environment) | Bitwarden `convergence-app-id`, `-app-key`, `-installation-id`; Secret `convergence-github-app` | the clones and `gh` in the walks: a round's token is minted with the read permissions alone (`tokens.py`), a Fulfil's with the installation's |
| The ServiceAccount's projected token, audience storage.sustenaut.eu | `/var/run/secrets/storage/token` | principal `k8s:convergence/app`: reads and writes tenant `plane`, writes `need-states` and `calls-convergence-engine` |
| A read-only Bitwarden machine account's token (optional) | Bitwarden `convergence-bitwarden-read-token`; Secret `convergence-bitwarden` | `./pubsec run`, so the bitwarden.* needs are checked |
| The write machine account's token, read and write on Sustenaut (optional) | Bitwarden `convergence-bitwarden-write-token`; Secret `convergence-bitwarden-write` | a Fulfil's walk alone, as `PUBSEC_WRITE_TOKEN_FILE`: `bitwarden.generated` and the other needs that write to Bitwarden |
| A projected token for Google, audience `convergence-google-read` | `/var/run/secrets/google-read/token` | gcloud's credential, without a key (`google.py`): traded through shared-infra's Workload Identity pool `convergence` for `convergence-key-reader`, which views the project (accounts, keys, Workload Identity bindings); every walk, a Fulfil's too, gets it, since no walk makes a Google key |
| The ServiceAccount itself | in-cluster | kubectl's reads; for a Fulfil, `k8s/admin/write-role.yaml`'s writes (Secrets, a Deployment's restart, a Job from a CronJob, a one-off Job and the delete of a failed one for its rerun), refused in the system namespaces and its own by an admission policy; TokenReviews (`k8s/admin/token-review.yaml`), for the requests of listed ServiceAccounts |
| GitHub's OIDC keys (public, no credential) | `https://token.actions.githubusercontent.com/.well-known/jwks`, through the integrations library (`github.actions-oidc`) | checking a run's request; reused for an hour, fetched again for a key not seen yet |
| A caller's bearer token | the request's `Authorization` header | checked and dropped: never stored, logged or journaled |

The engine's own calls (the token mint, storage) go to its call log through the
integrations library, into `calls_convergence_engine`. It is an always-on sender in
observability's `OBSERVABILITY_SENDERS`, so rounds that stop show as `sender_silent`.

## Settings

| Variable | Default | What |
|---|---|---|
| `CONVERGENCE_STATES` | `Sustenaut/release-state` | the state repositories, comma-separated |
| `CONVERGENCE_ZONES` | `{"Sustenaut": "Sustenaut/shared-infra"}` | per owner, the repository whose pubrules.json keeps the hosting zones |
| `CONVERGENCE_EVERY_MINUTES` | `30` | how often a round starts |
| `CONVERGENCE_WALK_SECONDS` | `600` | how long one walk may take |
| `CONVERGENCE_STORAGE_URL` | `https://storage.sustenaut.eu` | storage |
| `CONVERGENCE_FULFIL_SECONDS` | `1200` | how long a Fulfil may take |
| `CONVERGENCE_SITE` | `https://convergence.sustenaut.eu` | the site: forms' `Origin`, and the audience of a caller's token |
| `CONVERGENCE_REQUEST_HOURS` | `24` | how long a request awaits a decision before it expires (1 to 168) |
| `CONVERGENCE_REQUEST_OWNERS` | `Sustenaut` | the owners whose repositories' GitHub runs may ask, comma-separated |
| `CONVERGENCE_REQUESTERS` | none (`integrations:app` in `k8s/engine.yaml`) | the ServiceAccounts, `NAMESPACE:NAME`, that may ask for a gate |
| `CONVERGENCE_RUN_POLL_MS` | `2000` | how often a live run's page asks for its state |
| `CONVERGENCE_IMAGE` | none | the image a live run names as the one its needs were walked in |
| `CONVERGENCE_*_FILE`, `CONVERGENCE_GITHUB_APP_DIR` | as `k8s/engine.yaml` mounts them | the credentials |

`python -m convergence services state.json` prints the services a state file lists.

## Going live

`scripts/go-live-convergence.sh` walks the steps before the engine's first walk of
itself, from David's laptop: the GitHub App, its installation, the read-only machine
account, then `./with-secrets ./pubdeploy walk --fulfil --server sustenaut` (the admin channel), which applies `k8s/admin/`,
fills the Secrets, registers the sign-in client and opens the gate. After that the
engine walks itself too.

The image installs its lock from GitHub's releases and PyPI, and when that fails,
from storage's mirrors of the same files (`scripts/mirrored-lock.py`), with the same
hashes.

Step 4b's own steps (the App's write permissions, the write token, the write role)
are `scripts/go-live-fulfil.sh`.

## Development

```sh
make venv
make functional   # the Gherkin suite, 100% line coverage
make check        # and pubrules sync --check
```
