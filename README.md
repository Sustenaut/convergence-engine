# convergence-engine

Walks every deploy service read-only every 30 minutes, shows each need's state on a
page per service at <https://convergence.sustenaut.eu>, and journals every walk.

It is the second host for `pubdeploy walk`, beside the laptop, with its own narrower
credentials. This is step 4a of the operating plane (the phase 4 spec): it reads and
changes nothing. Fulfilling from the page is step 4b, and Google keys step 4c.

## What it does

A round runs at start and then every 30 minutes, one service after another:

1. **The list.** It refreshes a shallow clone of each state repository
   (`CONVERGENCE_STATES`, by default `Sustenaut/release-state`) and takes every
   repository whose entry has the role `deploy`. The state says whether a repository
   is walked; its own `pubkit.json` deploy block says how.
2. **The clones.** Each owner's zones repository (`CONVERGENCE_ZONES`, by default
   Sustenaut's `shared-infra`) and each service are shallow clones of their default
   branch, side by side under `/work/clones/OWNER/`, refreshed before each walk.
   `/work` is the claim `engine-work`, so the clones, the launchers' wheels (fetched
   from GitHub, else storage's wheel mirror, each checked against its sha256) and
   uv's cache outlive a restart.
3. **The walk.** In the service's clone it runs the repository's own pubdeploy:

   ```sh
   ./pubdeploy check --json --journal https://storage.sustenaut.eu --zones ../shared-infra/pubkit.json
   ```

   through `./pubsec run --` when there is a Bitwarden read token, so the walk uses
   the pubdeploy, pubsec and need types that repository pins, never the engine's.
   `check` is a walk that cannot fulfil. kubectl runs as the pod's ServiceAccount
   (pubdeploy's `plane` channel, `PUBKIT_PLANE`), with the reads
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
| `POST /services/OWNER/NAME/fulfil` | a person's Fulfil of one unit (the button) |
| `/fulfils/ID` | what that Fulfil printed, as pubdeploy prints it (names and states), and how it ended; it reloads itself while it runs |
| `/healthz` | open; 200 while rounds end in time |

They are plain HTML with no script, `Cache-Control: private, no-store`, and every
string from a walk escaped.

**Fulfil** (step 4b) is the one thing a page does: `./pubdeploy walk --fulfil --yes
--unit U` in the service's clone, which makes each auto need that is missing or
drifted. The person is oauth2-proxy's `X-Forwarded-Email`, which only the proxy can
set (the network policy lets nothing else reach the engine), and the journal records
them (`PUBKIT_WALK_BY`). The form carries a token made from that person, the service
and the unit, and the request must come from this site (`Origin`). A service is never
fulfilled while it is being walked, by a round or another Fulfil, and a Fulfil may take
`CONVERGENCE_FULFIL_SECONDS` (1200). Its walk alone gets the installation token that
may write and the Bitwarden write token's file: every round mints a token limited to
read and runs `check`, so it cannot write. `k8s.namespace` stays an admin's step: the
deploy channel needs a kubeconfig on the node that only `sustenaut-app` writes.

## Credentials

Every credential is a file mounted in the pod, read when used, and handed to a walk's
subprocess in an environment built from nothing (PATH, HOME, the cluster's address,
`GH_TOKEN`, `PUBKIT_STORAGE_TOKEN`, `PUBKIT_WALK_BY`, and `BWS_ACCESS_TOKEN` when
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
| The ServiceAccount itself | in-cluster | kubectl's reads; for a Fulfil, `k8s/admin/write-role.yaml`'s writes (Secrets, a Deployment's restart, a Job from a CronJob), refused in the system namespaces and its own by an admission policy |

The engine's own calls (the token mint, storage) go to its call log through the
integrations library, into `calls_convergence_engine`. It is an always-on sender in
observability's `OBSERVABILITY_SENDERS`, so rounds that stop show as `sender_silent`.

## Settings

| Variable | Default | What |
|---|---|---|
| `CONVERGENCE_STATES` | `Sustenaut/release-state` | the state repositories, comma-separated |
| `CONVERGENCE_ZONES` | `{"Sustenaut": "Sustenaut/shared-infra"}` | per owner, the repository whose pubkit.json keeps the hosting zones |
| `CONVERGENCE_EVERY_MINUTES` | `30` | how often a round starts |
| `CONVERGENCE_WALK_SECONDS` | `600` | how long one walk may take |
| `CONVERGENCE_STORAGE_URL` | `https://storage.sustenaut.eu` | storage |
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
make check        # and pubkit sync --check
```
