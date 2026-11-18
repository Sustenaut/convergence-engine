"""Requests a person approves first: who may ask, the plan and its hash, the decisions,
the journal of every change, and the pages."""

from __future__ import annotations

import base64
import json
import re
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.approvals import Approvals, Ask, Ledger
from convergence.callers import GitHubOIDC, Refused, Requester, jwks_from
from convergence.clones import Ran
from convergence.journals import Journals
from convergence.page import create_app

from .fakes import Storage, journal
from .world import ISSUER, REPOSITORY, SA_TOKEN, SITE, WHO, World, cluster_token, person

scenarios("approvals.feature")


def _states(found: str) -> dict[str, str]:
    return dict(re.findall(r'"([^"]+)" (\w+)', found))


@given(parsers.re(r"the engine has walked Sustenaut/notifier and found (?!.* graded )(?P<found>.+)"), target_fixture="world")
def walked(tmp_path: Path, found: str) -> World:
    w = World(tmp_path, needs=_states(found))
    w.engine.round()
    w.held = False
    w.bearer = ""
    return w


# --- Asking


def _post(world: World, bearer: str, body, raw: bytes | None = None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    world.answer = world.client.post("/requests", content=raw if raw is not None else json.dumps(body),
                                     headers={**headers, "Content-Type": "application/json"})
    if world.answer.status_code == 201:
        world.ask = world.approvals.asks[world.answer.json()["id"]]
        world.bearer = bearer
    return world.answer


def _hold(world: World) -> None:
    """Hold the service's walk lock, so a request's plan walk waits for the scenario."""
    if not world.held:
        world.engine.walking(REPOSITORY).acquire()
        world.held = True


def _release(world: World) -> None:
    if world.held:
        world.engine.walking(REPOSITORY).release()
        world.held = False
    world.wait()


@when(parsers.re(r'a GitHub run asks for a gate with the plan "(?P<text>.*)"'))
def run_asks_gate(world: World, text: str) -> None:
    _post(world, world.token(), {"kind": "gate", "plan": text})


@given(parsers.re(r'a GitHub run asked for a gate with the plan "(?P<text>.*)"'))
def run_asked_gate(world: World, text: str) -> None:
    assert _post(world, world.token(), {"kind": "gate", "plan": text}).status_code == 201
    world.gate = world.ask


@when(parsers.re(r'a GitHub run asks for a fulfil of the unit "(?P<unit>[^"]+)"'))
def run_asks_fulfil(world: World, unit: str) -> None:
    _hold(world)
    assert _post(world, world.token(), {"kind": "fulfil", "unit": unit}).status_code == 201


@when(parsers.re(r'a GitHub run asks for a check of the unit "(?P<unit>[^"]+)"'))
def run_asks_check(world: World, unit: str) -> None:
    _hold(world)
    assert _post(world, world.token(), {"kind": "check", "unit": unit}).status_code == 201


@given("the engine without a write token has walked Sustenaut/notifier", target_fixture="world")
def walked_no_write(tmp_path: Path) -> World:
    w = World(tmp_path, fulfil=False)
    w.engine.round()
    w.held, w.bearer = False, ""
    return w


@given(parsers.re(r'a GitHub run asked for a fulfil of the unit "(?P<unit>[^"]+)"'))
def run_asked_fulfil(world: World, unit: str) -> None:
    run_asks_fulfil(world, unit)
    world.fulfil = world.ask


@given("the request's plan has been walked")
@when("the request's plan has been walked")
def plan_walked(world: World) -> None:
    _release(world)


def _token_for(world: World, which: str) -> str:
    keys, now = world.keys, world.now
    if which == "a token signed by another key":
        return keys.mint(keys.claims(now), key=keys.other_key())
    if which == "a token of an unknown key":
        return keys.mint(keys.claims(now), kid="another-key")
    if which == "a token with another algorithm":
        head = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(keys.claims(now)).encode()).rstrip(b"=").decode()
        return f"{head}.{body}.c2ln"
    if which == "a token in two parts":
        return world.token().rsplit(".", 1)[0]
    changed = {"a token for another audience": {"aud": "https://elsewhere.example"},
               "a token of another owner": {"repository_owner": "Other", "repository": "Other/x"},
               "a token of another's repository": {"repository": "Other/x"},
               "a token from another branch": {"ref": "refs/heads/feature"},
               "an expired token": {"exp": int(now) - 3600},
               "a token of another issuer": {"iss": "https://elsewhere.example"},
               "a token not yet valid": {"nbf": int(now) + 3600}}
    if which in changed:
        return world.token(**changed[which])
    return {"a token that is not a JWT": "not-a-jwt", "no token": ""}[which]


@when(parsers.re(r"a GitHub run asks for a gate with (?P<which>(?:a|an|no) [^\"]+)"))
def run_asks_with(world: World, which: str) -> None:
    _post(world, _token_for(world, which), {"kind": "gate", "plan": "x"})


@then(parsers.re(r'the answer is (?P<status>\d+) and the request is "(?P<state>[^"]+)"'), converters={"status": int})
def answer_and_state(world: World, status: int, state: str) -> None:
    assert world.answer.status_code == status, world.answer.text
    assert world.answer.json()["state"] == state
    assert world.answer.headers["Cache-Control"] == "private, no-store"


@then(parsers.re(r'the answer is (?P<status>\d+) refusing "(?P<reason>[^"]+)"'), converters={"status": int})
def answer_refusing(world: World, status: int, reason: str) -> None:
    assert (world.answer.status_code, world.answer.json()) == (status, {"refused": reason})


@then("the request records the run's repository, sha, workflow and run")
def records_the_run(world: World) -> None:
    r = world.ask.requester
    assert (r.kind, r.repository, r.repository_id, r.sha, r.run_id, r.actor) == \
        ("github", REPOSITORY, "4242", "0123456789abcdef0123", "777", "schryer")
    assert r.workflow_ref.endswith("go-live.yml@refs/heads/main")
    assert world.ask.run_url == f"https://github.com/{REPOSITORY}/actions/runs/777"
    (event,) = world.storage.events()
    assert event["run_url"] == world.ask.run_url and event["sha"] == r.sha and event["repository"] == REPOSITORY


@then(parsers.re(r"the journal holds (?P<count>\d+) events?(?:, (?P<states>.*))?"), converters={"count": int})
def journal_holds(world: World, count: int, states: str | None) -> None:
    events = world.storage.events()
    assert len(events) == count
    if states:
        assert [e["state"] for e in events] == re.findall(r'"([^"]+)"', states)
    assert all(e["schema"] == "convergence-approval/1" for e in events)


@then("no request is stored and the journal holds 0 events")
def nothing_stored(world: World) -> None:
    assert world.approvals.asks == {} and world.storage.events() == []


@when("a GitHub run asks for a gate twice, then with a key GitHub has just published")
def keys_twice(world: World) -> None:
    for _ in range(2):
        assert _post(world, world.token(), {"kind": "gate", "plan": "x"}).status_code == 201
    from .world import Keys
    newer = Keys("newer-key")
    world.keys.jwks = {"keys": [*world.keys.jwks["keys"], *newer.jwks["keys"],
                                {"kty": "EC", "kid": "ignored"}]}
    world.now += 120
    assert _post(world, newer.mint(newer.claims(world.now)), {"kind": "gate", "plan": "x"}).status_code == 201


@then(parsers.parse("GitHub's keys were fetched {count:d} times"))
def keys_fetched(world: World, count: int) -> None:
    assert world.keys.fetched == count


@given("GitHub's keys cannot be fetched")
def keys_down(world: World) -> None:
    def down():
        raise OSError("no route")
    world.callers.github.fetch = down


@then("the keys are fetched through the caller, and a refusal lets no one in")
def keys_through_caller(world: World) -> None:
    class Caller:
        def __init__(self, ok):
            self.ok, self.asked = ok, []

        def call(self, plugin, operation, request):
            self.asked.append((plugin, operation, request.url))
            ok = self.ok

            class Answer:
                status = 200 if ok else 503

                def __init__(self):
                    self.ok = ok

                def json(self):
                    return world.keys.jwks
            return Answer()
    good = Caller(True)
    oidc = GitHubOIDC(jwks_from(good), SITE, ("Sustenaut",), clock=lambda: world.now)
    assert oidc.verify(world.token()).repository == REPOSITORY
    assert good.asked == [("github.actions-oidc", "identity", f"{ISSUER}/.well-known/jwks")]
    with pytest.raises(Refused) as e:
        oidc.verify(world.token(iss="https://elsewhere.example"))
    assert e.value.reason == "wrong-issuer"
    refused = GitHubOIDC(jwks_from(Caller(False)), SITE, ("Sustenaut",), clock=lambda: world.now)
    with pytest.raises(Refused) as e:
        refused.verify(world.token())
    assert (e.value.status, e.value.reason) == (503, "keys-unavailable")


BODIES = {"a request of another kind": {"kind": "deploy"},
          "a gate without a plan": {"kind": "gate"},
          "a gate whose plan is over 8 KB": {"kind": "gate", "plan": "x" * 8193},
          "a fulfil of another repository": {"kind": "fulfil", "repository": "Sustenaut/other", "unit": "notifier"},
          "a fulfil of a unit not there": {"kind": "fulfil", "unit": "x"},
          "a check of another repository": {"kind": "check", "repository": "Sustenaut/other", "unit": "notifier"}}
RAW = {"a body that is not JSON": b"nope", "a body over 16 KB": json.dumps({"plan": "x" * 17000}).encode()}


@when(parsers.parse("a GitHub run posts {body}"))
def run_posts(world: World, body: str) -> None:
    _post(world, world.token(), BODIES.get(body), RAW.get(body))


ACCOUNTS = {"the ServiceAccount integrations:app": SA_TOKEN,
            "the ServiceAccount notifier:app": cluster_token("system:serviceaccount:notifier:app"),
            "a token the cluster does not know": cluster_token("system:serviceaccount:gone:app"),
            "a token of a user, not a ServiceAccount": cluster_token("someone"),
            "a token while the cluster is down": SA_TOKEN}


@when(parsers.re(r'the ServiceAccount integrations:app asks for a gate with the plan "(?P<text>.*)"'))
def account_asks_gate(world: World, text: str) -> None:
    _post(world, SA_TOKEN, {"kind": "gate", "plan": text})


@given(parsers.re(r'the ServiceAccount integrations:app asked for a gate with the plan "(?P<text>.*)"'))
def account_asked_gate(world: World, text: str) -> None:
    assert _post(world, SA_TOKEN, {"kind": "gate", "plan": text, "unit": "approvals"}).status_code == 201


@when(parsers.re(r"(?P<account>.+) asks for (?P<kind>a fulfil|a gate)"))
def account_asks(world: World, account: str, kind: str) -> None:
    world.cluster.down = account.endswith("is down")
    _post(world, ACCOUNTS[account], {"kind": "fulfil", "unit": "notifier"} if kind == "a fulfil"
          else {"kind": "gate", "plan": "x"})


@then("the cluster was asked to review the token for this site's audience")
def cluster_asked(world: World) -> None:
    assert world.cluster.asked[-1]["spec"] == {"token": SA_TOKEN, "audiences": [SITE]}
    assert world.ask.requester == Requester("kubernetes", "integrations:app")


@given("the engine has no TokenReview")
def no_cluster(world: World) -> None:
    world.callers.cluster = None


@given("the engine takes no callers")
def no_callers(world: World) -> None:
    world.client = TestClient(create_app(world.engine, Journals(world.storage), world.approvals, None, site=SITE))


# --- Polling


def _poll(world: World, bearer: str, id_: str | None = None) -> None:
    world.answer = world.client.get(f"/requests/{id_ or world.ask.id}", headers={"Authorization": f"Bearer {bearer}"})


@then(parsers.re(r'the run\'s poll says "(?P<state>[^"]+)"(?: with (?P<found>.*))?'))
def run_poll(world: World, state: str, found: str | None) -> None:
    _poll(world, world.token())
    answer = world.answer.json()
    assert world.answer.status_code == 200 and answer["state"] == state
    assert answer["url"] == f"{SITE}/approvals/{world.ask.id}"
    if found:
        assert {n["need"]: n["state"] for n in answer["result"]["needs"]} == _states(found)


@when("another run of the repository polls it")
def other_run_polls(world: World) -> None:
    _poll(world, world.token(run_id="778"))


@when("the ServiceAccount integrations:app polls it")
def account_polls(world: World) -> None:
    _poll(world, SA_TOKEN)


@when("the run polls a request that is not there")
def poll_missing(world: World) -> None:
    _poll(world, world.token(), "not-there")


@then("nothing was walked for it")
def nothing_walked(world: World) -> None:
    assert world.runner.walked("--unit") == []


# --- Pages and decisions


def _page(world: World, headers: dict | None = None, id_: str | None = None):
    world.answer = world.client.get(f"/approvals/{id_ or world.ask.id}", headers=headers or person())
    return world.answer


def _form(world: World) -> dict[str, str]:
    page = _page(world).text
    return dict(re.findall(r'name="(token|plan)" value="([^"]+)"', page))


def _decide(world: World, decision: str, headers: dict | None = None, form: dict | None = None,
            id_: str | None = None) -> None:
    data = {**(form if form is not None else _form(world)), "decision": decision}
    world.answer = world.client.post(f"/approvals/{id_ or world.ask.id}", data=data, headers=headers or person(),
                                     follow_redirects=False)
    world.wait()


@when("the person opens the request's page")
def open_page(world: World) -> None:
    _page(world)


@when("a person without the role opens the request's page")
def open_page_no_role(world: World) -> None:
    _page(world, person(role=False))


@then("the page shows the plan escaped and the buttons")
def plan_escaped(world: World) -> None:
    text = world.answer.text
    assert "<pre>&lt;b&gt;Deploy&lt;/b&gt; notifier</pre>" in text and "<b>Deploy" not in text
    assert "Approve</button>" in text and "Decline</button>" in text
    assert f"Plan sha256 <code>{world.ask.plan_sha256}</code>" in text


@then(parsers.parse('Approve and Decline are shown disabled, saying "{why}"'))
def decide_disabled(world: World, why: str) -> None:
    text = world.answer.text
    assert world.answer.status_code == 200 and 'name="decision"' not in text
    assert re.search(r'<button type="button" disabled title="[^"]*" aria-describedby="why-approve">Approve</button>', text)
    assert re.search(r'class=quiet disabled title="[^"]*" aria-describedby="why-approve">Decline</button>', text)
    assert why in unescape(re.search(r'id="why-approve">([^<]*)<', text).group(1))
    _release(world)


@when("the plan has been walked and the person declined it")
def walked_and_declined(world: World) -> None:
    _release(world)
    assert world.approvals.decide(world.ask.id, WHO, False) == ""
    _page(world)


@then("the page reloads itself")
def page_reloads(world: World) -> None:
    assert world.answer.headers["Refresh"] == "5"


@when("the person approves it")
def approves(world: World) -> None:
    _decide(world, "approve")


@when("the person approves it and the walk makes it ready")
def approves_ready(world: World) -> None:
    world.runner.fulfil_prints(REPOSITORY, journal(states={"k8s.secret:s": "ready"}), stderr="k8s.secret:s: made\n")
    _decide(world, "approve")


@when("the person approves it and the Fulfil walk fails")
def approves_fails(world: World) -> None:
    world.runner.fulfils[REPOSITORY] = Ran(2, "", "pubdeploy: no\n")
    _decide(world, "approve")


@when("the person approves it and the clone fails before the Fulfil")
def approves_clone_fails(world: World) -> None:
    world.runner.failing[REPOSITORY] = "gone"
    _decide(world, "approve")


@when("the person approves it while a walk of the service keeps running")
def approves_while_running(world: World) -> None:
    form = _form(world)
    world.approvals.lock_seconds = 0.2
    _hold(world)
    _decide(world, "approve", form=form)
    _release(world)


@when("the person declines it")
def declines(world: World) -> None:
    _decide(world, "decline")


@given("the person declined the fulfil")
def declined_fulfil(world: World) -> None:
    assert world.approvals.decide(world.fulfil.id, WHO, False) == ""


@when("the need drifts before the person approves it")
def drifts(world: World) -> None:
    world.runner.walk_prints(REPOSITORY, journal(states={"k8s.secret:s": "drift"}), code=1)
    _decide(world, "approve")


@then(parsers.re(r'the request is "(?P<state>[^"]+)" with a plan of (?P<found>.*)'))
def awaiting_with_plan(world: World, state: str, found: str) -> None:
    assert world.ask.state == state
    assert {n["need"]: n["state"] for n in world.ask.plan} == _states(found)
    (need,) = world.ask.plan
    assert need == {"need": "k8s.secret:s", "name": "s", "type": "k8s.secret", "state": "missing", "grade": "auto",
                    "change": "the fulfil makes it: k8s.secret:s is missing"}


@then(parsers.parse('the plan walk ran "check --unit notifier" as the run\'s repository'))
def plan_walk_ran(world: World) -> None:
    ((argv, _, env),) = [c for c in world.runner.calls if c[0][0] != "git" and "--unit" in c[0]]
    assert argv[:4] == ["./pubdeploy", "check", "--unit", "notifier"]
    assert env["PUBRULES_WALK_BY"] == REPOSITORY and env["GH_TOKEN"] == "ghs_read"


@then(parsers.re(r'the request ends "(?P<state>[^"]+)" with (?P<found>.*)'))
def ends_with(world: World, state: str, found: str) -> None:
    assert world.ask.state == state, world.ask.error
    assert {n["need"]: n["state"] for n in world.ask.needs} == _states(found)


@then(parsers.re(r'the request ends "(?P<state>[^"]+)", saying "(?P<text>[^"]+)"'))
def ends_saying(world: World, state: str, text: str) -> None:
    world.wait()
    assert world.ask.state == state and text in world.ask.error, (world.ask.state, world.ask.error)


@then(parsers.re(r'the request ends "(?P<state>[^"]+)"'))
def ends(world: World, state: str) -> None:
    assert world.ask.state == state, world.ask.error


@then('the request ends "done" without an approval')
def done_without_approval(world: World) -> None:
    assert world.ask.state == "done" and world.ask.plan == []
    assert [c["state"] for c in world.ask.changes] == ["planning", "done"]


@then(parsers.re(r'the request is "(?P<state>[^"]+)"'))
def request_is(world: World, state: str) -> None:
    assert world.ask.state == state


@then("the Fulfil walk ran with PUBRULES_WALK_BY the person")
def fulfil_as_person(world: World) -> None:
    (env,) = world.walk_envs(fulfil=True)
    assert env["PUBRULES_WALK_BY"] == WHO and env["GH_TOKEN"] == "ghs_write"
    checks = [env for argv, _, env in world.runner.calls if "check" in argv and "--unit" in argv]
    assert [e["PUBRULES_WALK_BY"] for e in checks] == [REPOSITORY, WHO]


@then("every event names the request, its plan's sha256, and no value")
def events_named(world: World) -> None:
    events = world.storage.events()
    assert {e["id"] for e in events} == {world.ask.id}
    assert all(e["plan_sha256"] == world.ask.plan_sha256 for e in events[1:])
    assert events[-1]["needs"] == [{"need": "k8s.secret:s", "type": "k8s.secret", "grade": "auto", "state": "ready", "detail": "k8s.secret:s is ready"}]
    assert [e["by"] for e in events] == [REPOSITORY, "engine", WHO, WHO, "engine"]
    text = json.dumps(events)
    for value in ("ghs_write", "ghs_read", "storage-token", "bws-write", world.bearer):
        assert value not in text
    hashes = [e["hash"] for e in reversed(world.storage.logs["convergence.approvals"])]
    assert [e["previous"] for e in events] == [None, *hashes[:-1]]


@then("no Fulfil walk ran")
def no_fulfil(world: World) -> None:
    assert world.runner.walked("--fulfil") == []


@then(parsers.parse('a second decision is refused, saying "{text}"'))
def second_decision(world: World, text: str) -> None:
    assert text in world.approvals.decide(world.ask.id, WHO, True)
    assert world.approvals.decide("nope", WHO, True) == "there is no such request"


@when("25 hours pass")
def hours_pass(world: World) -> None:
    world.now += 25 * 3600
    world.approvals.expire_due()


@then(parsers.parse('the journal\'s last event is "{state}" by "{by}"'))
def last_event(world: World, state: str, by: str) -> None:
    last = world.storage.events()[-1]
    assert (last["state"], last["by"]) == (state, by)


@given("storage refuses every write")
def refuses_every(world: World) -> None:
    world.storage.refuse_writes = True


@given("storage refuses the next write")
def refuses_next(world: World) -> None:
    world.storage.refuse_upload(1)


@given("storage refuses the next write after the approval")
def refuses_after_approval(world: World) -> None:
    world.storage.refuse_upload(2)


@then("the request is \"awaiting\", and the log says the expiry was refused")
def expiry_refused(world: World) -> None:
    assert world.ask.state == "awaiting"
    assert any("refused its expiry" in line for line in world.lines)


@when("the approvals are listed")
def approvals_listed(world: World) -> None:
    world.answer = world.client.get("/approvals", headers=person())


@then("the log says the journal refused its end")
def log_refused_end(world: World) -> None:
    assert any("the journal refused its end" in line for line in world.lines)


@then(parsers.re(r'the page answers (?P<status>\d+)'), converters={"status": int})
def page_answers(world: World, status: int) -> None:
    assert world.answer.status_code == status, world.answer.text


@then(parsers.parse('the page answers {status:d} saying "{text}"'))
def page_answers_saying(world: World, status: int, text: str) -> None:
    assert world.answer.status_code == status and text in world.answer.text


# --- Restarts


@given("the person approved the fulfil, and the engine stopped before running it")
def approved_not_run(world: World) -> None:
    world.approvals._move(world.fulfil, "approved", WHO, approver=WHO)  # noqa: SLF001 - no thread: the pod stopped


@given("a third request was planning when the engine stopped")
def third_planning(world: World) -> None:
    ask = Ask("third", "fulfil", Requester("person", WHO), REPOSITORY, "notifier", world.now, world.now + 3600)
    world.approvals._take(ask, "planning")  # noqa: SLF001 - no thread: the pod stopped
    world.third = ask


@when("a new pod starts the approvals from the head")
def new_pod(world: World) -> None:
    world.runner.fulfil_prints(REPOSITORY, journal(states={"k8s.secret:s": "ready"}))
    world.approvals = world.new_approvals()
    world.approvals.start()
    world.approvals.wait()


@then(parsers.parse('the gate is still "{state}" and can be approved'))
def gate_still(world: World, state: str) -> None:
    gate = world.approvals.asks[world.gate.id]
    assert gate.state == state and gate.text == "x" and gate.requester.run_id == "777"
    assert world.approvals.decide(gate.id, WHO, True) == ""


@then(parsers.parse('the approved fulfil runs, and the planning one ended "{state}"'))
def resumed(world: World, state: str) -> None:
    fulfil, third = world.approvals.asks[world.fulfil.id], world.approvals.asks["third"]
    assert fulfil.state == "done" and fulfil.needs == [{"need": "k8s.secret:s", "type": "k8s.secret", "grade": "auto", "state": "ready", "detail": "k8s.secret:s is ready"}]
    assert third.state == state and "restarted" in third.error


@then("a new head starts empty, and a refused head stops the start")
def empty_and_refused(world: World) -> None:
    empty = Approvals(world.engine, Ledger(Storage()), log=world.lines.append)
    empty.start()
    assert empty.asks == {}
    refusing = Storage()
    refusing.refuse = "storage said 503"
    with pytest.raises(RuntimeError):
        Approvals(world.engine, Ledger(refusing)).start()


@when(parsers.parse('the person opens "{path}"'))
def opens(world: World, path: str) -> None:
    world.answer = world.client.get(path, headers=person())


@then(parsers.parse("the page lists {opened:d} open requests and {ended:d} ended"))
def lists(world: World, opened: int, ended: int) -> None:
    open_part, ended_part = world.answer.text.split("<h2>Ended</h2>")
    assert open_part.count('<a href="/approvals/') == opened
    assert ended_part.count('<a href="/approvals/') == ended
    assert "integrations:app (approvals) " not in open_part and "integrations:app (ServiceAccount)" in open_part
    assert "go-live.yml@refs/heads/main run by schryer" in open_part


@then(parsers.parse("the front page says {count:d} awaiting"))
def front_page(world: World, count: int) -> None:
    assert f'<a href="/approvals">Approvals</a>: {count} awaiting' in world.client.get("/").text


@when("the person opens the request's page while its plan is walked")
def open_while_planning(world: World) -> None:
    _page(world)


@then("the page reloads itself and links to the run")
def reloads(world: World) -> None:
    assert world.answer.headers["Refresh"] == "5"
    assert f'<a href="https://github.com/{REPOSITORY}/actions/runs/777">the run</a>' in world.answer.text
    assert "being walked read-only" in world.answer.text
    _release(world)


@then("a request that is not there is 404")
def not_there(world: World) -> None:
    assert _page(world, id_="nope").status_code == 404


ASKERS = {"no one signed in": {"Origin": SITE}, "the person without the role": person(role=False),
          "the person, from another site": person(origin="https://elsewhere.example"),
          "another person": person("other@example.org"), "the person": person()}


@when(parsers.re(r"(?P<asker>.+) posts (?P<decision>\w+) with (?P<form>.+)"))
def posts_decision(world: World, asker: str, decision: str, form: str) -> None:
    data = _form(world)
    if form == "a form for another plan":
        data["plan"] = "0" * 64
    _decide(world, decision, ASKERS[asker], data, "nope" if form.endswith("not there") else None)


@given("the walk of Sustenaut/notifier prints no journal")
def prints_no_journal(world: World) -> None:
    world.runner.walks[REPOSITORY] = Ran(2, "", "pubdeploy: no\n")


@given("the walk of Sustenaut/notifier prints another unit's journal")
def prints_other_unit(world: World) -> None:
    world.runner.walk_prints(REPOSITORY, journal(unit="other", states={"k8s.secret:s": "missing"}), code=1)


@then(parsers.parse('the request\'s page says "{text}"'))
def request_page_says(world: World, text: str) -> None:
    assert text in _page(world).text


@then(parsers.parse('the plan says "{need}" is "{change}"'))
def plan_says(world: World, need: str, change: str) -> None:
    assert {n["need"]: n["change"] for n in world.ask.plan}[need] == change


@then("the poll answer holds no email")
def poll_no_email(world: World) -> None:
    _poll(world, world.token())
    answer = world.answer.json()
    assert "approver" not in answer and WHO not in world.answer.text and "@" not in world.answer.text


@then("the approved gate has left the open requests, in the next journal event too")
def gate_left_open(world: World) -> None:
    gate = world.ask
    assert gate.ended and gate in world.approvals.listed()[1]
    assert _post(world, world.token(), {"kind": "gate", "plan": "next"}).status_code == 201
    last = world.storage.events()[-1]
    assert gate.id not in [a["id"] for a in last["open"]] and gate.id in [a["id"] for a in last["ended"]]
    assert [a["approver"] for a in last["ended"] if a["id"] == gate.id] == [WHO]


@then("the cluster was not asked")
def cluster_not_asked(world: World) -> None:
    assert world.cluster.asked == []


@given(parsers.parse('the person\'s Fulfil of the unit "{unit}" has its plan and awaits its own approval'))
def press_awaiting(world: World, unit: str) -> None:
    ask = Ask("pressed", "fulfil", Requester("person", WHO, actor=WHO), REPOSITORY, unit, world.now,
              world.now + 3600, approve_on_plan=WHO)
    world.approvals._take(ask, "awaiting", plan=[], plan_sha256="0" * 64)  # noqa: SLF001 - held at awaiting
    world.ask = ask




@when("the person approves it twice with the same form")
def approves_twice(world: World) -> None:
    form = _form(world)
    _decide(world, "approve", form=form)
    assert world.answer.status_code == 303
    _decide(world, "approve", form=form)


@then("the second answer sends them to the request's page")
def second_answer(world: World) -> None:
    assert world.answer.status_code == 303 and world.answer.headers["location"] == f"/approvals/{world.ask.id}"


@when("another person declines it, and then the person approves it with the form they had")
def other_declines_then(world: World) -> None:
    form = _form(world)
    other = person("other@example.org")
    _decide(world, "decline", headers=other, form=dict(re.findall(
        r'name="(token|plan)" value="([^"]+)"', _page(world, other).text)))
    assert world.answer.status_code == 303
    _decide(world, "approve", form=form)


@when("the person presses Fulfil and the journal refuses its approval")
def press_approval_refused(world: World) -> None:
    _hold(world)
    world.ask = world.approvals.press(REPOSITORY, "notifier", WHO)
    world.storage.refuse_upload(2)  # the plan's event counts, the approval's does not
    _release(world)


@then("the request awaits a decision with the buttons, and the log says why")
def awaits_with_buttons(world: World) -> None:
    assert world.ask.state == "awaiting" and "Approve</button>" in _page(world).text
    assert any("its press's approval did not count" in line for line in world.lines)


@given(parsers.re(r"the engine has walked Sustenaut/notifier and found (?P<found>.+ graded .+)"),
       target_fixture="world")
def walked_graded(tmp_path: Path, found: str) -> World:
    parts = re.findall(r'"([^"]+)" (\w+) graded (\w+)(?: detailing "([^"]+)")?', found)
    w = World(tmp_path, needs={n: s for n, s, _, _ in parts})
    w.runner.walk_prints(REPOSITORY, journal(states={n: s for n, s, _, _ in parts}, grades={n: g for n, _, g, _ in parts},
                                             details={n: [d] for n, _, _, d in parts if d}), code=1)
    w.engine.round()
    w.held = False
    w.bearer = ""
    return w


@then("the request never awaited an approval")
def never_awaited(world: World) -> None:
    assert "awaiting" not in [c["state"] for c in world.ask.changes]


@then(parsers.parse('the journal\'s last event gives the reason and the needs {names}'))
def last_event_needs(world: World, names: str) -> None:
    last = world.storage.events()[-1]
    assert last["reason"] == world.ask.error
    assert [n["need"] for n in last["needs"]] == re.findall(r'"([^"]+)"', names)


@then(parsers.re(r'the run\'s poll says "(?P<state>[^"]+)", giving the (?P<what>reason|note) "(?P<text>[^"]+)"'))
def poll_with(world: World, state: str, what: str, text: str) -> None:
    _poll(world, world.token())
    result = world.answer.json()
    assert result["state"] == state
    assert text in result["result"]["error" if what == "reason" else "note"]


@then(parsers.parse('the request ends "{state}" with the note "{text}"'))
def ends_noted(world: World, state: str, text: str) -> None:
    assert world.ask.state == state and text in world.ask.note, (world.ask.state, world.ask.note)


@then(parsers.parse('the service page shows "{text}" beside the need'))
def service_page_shows(world: World, text: str) -> None:
    page = world.client.get(f"/services/{REPOSITORY}", headers=person()).text
    assert f"<br><span class=why>{text}</span>" in page


@then(parsers.parse('the run\'s poll lists the plan\'s need "{need}" as "{state}"'))
def poll_plan(world: World, need: str, state: str) -> None:
    _poll(world, world.token())
    (entry,) = world.answer.json()["plan"]
    assert (entry["need"], entry["state"]) == (need, state) and entry["change"].startswith("the fulfil makes it")


@then(parsers.parse('the run\'s poll gives "{need}" as type "{kind}", grade "{grade}", state "{state}", found "{detail}"'))
def poll_gives_need(world: World, need: str, kind: str, grade: str, state: str, detail: str) -> None:
    _poll(world, world.token())
    found = {n["need"]: n for n in world.answer.json()["result"]["needs"]}
    assert found[need] == {"need": need, "type": kind, "grade": grade, "state": state, "detail": detail}


@given(parsers.parse('the walk now finds "{need}" {state} detailing "{detail}"'))
@when(parsers.parse('the walk now finds "{need}" {state} detailing "{detail}"'))
def walk_now_finds(world: World, need: str, state: str, detail: str) -> None:
    world.runner.walk_prints(REPOSITORY, journal(states={need: state}, details={need: [detail]}), code=1)
