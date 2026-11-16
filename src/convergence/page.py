"""The pages: plain HTML from the server, no script, nothing loaded from elsewhere.

They sit behind oauth2-proxy and the team sign-in (k8s/oauth2-proxy.yaml), which
lets in only people holding the role `convergence`. They show what the walks found
and change nothing.

    /                                         every service, its units' counts
    /services/OWNER/NAME                      each unit's needs, and its walks
    /services/OWNER/NAME/walks/HASH           one walk's journal, from storage
    POST /services/OWNER/NAME/fulfil          a person's Fulfil of one unit: a request they approve
    /approvals                                the open requests, then the last ended ones
    /approvals/ID                             one request: its plan, source, changes and output
    POST /approvals/ID                        a person's Approve or Decline
    POST /requests                            a caller's request (a bearer token, no sign-in)
    GET /requests/ID                          its state, for the caller that asked
    /healthz                                  open: has a round ended lately

A person's Fulfil, Approve and Decline are the things a page does. The person is
oauth2-proxy's X-Forwarded-Email, which only the proxy can set (admin/network-policy.yaml
lets nothing else reach the engine), holding the role `convergence`
(X-Forwarded-Groups). The form carries a token made from that person and what the
button is for (the service and the unit, or the request and its plan's hash), and the
request must come from this site's own page (Origin), so another site cannot make a
signed-in browser press a button.

/requests is the one place the proxy lets through without the sign-in
(k8s/oauth2-proxy.yaml): it reads no X-Forwarded-* header, only the bearer token
callers.py checks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from html import escape
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .approvals import Approvals, Ask
from .callers import Callers, Refused
from .engine import Engine, Service
from .journals import Journals

STATES = ("ready", "missing", "drift", "blocked", "unknown")
STYLE = ("body{font:15px/1.5 system-ui;margin:2rem auto;max-width:76rem;padding:0 1rem;color-scheme:light dark}"
         "table{border-collapse:collapse;width:100%;margin-bottom:1.5rem}th,td{padding:.25rem .5rem;"
         "border-bottom:1px solid #8884;text-align:left;vertical-align:top}td.n,th.n{text-align:right}"
         ".ready{color:#2a7a2a}.missing,.drift,.blocked{font-weight:600;color:#b03a2e}.unknown{color:#8a6d00}"
         "code{font-size:13px}h2{margin-top:2rem}")
HEADERS = {"Cache-Control": "private, no-store",
           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
AUTO = ("The engine fulfils this once a person approves its plan (the button below). From a laptop: "
        "<code>./with-secrets ./pubdeploy walk --fulfil --unit {unit}</code>")
ROLE = "convergence"
BODY_BYTES = 16384


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f"<title>{escape(title)}</title><style>{STYLE}</style><h1>{escape(title)}</h1>{body}</html>",
                        status_code=status, headers=HEADERS)


def _table(head: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f"<p>{escape(empty)}</p>"
    th = "".join(f"<th>{escape(h)}" for h in head)
    return f"<table><tr>{th}</tr>" + "".join("<tr>" + "".join(f"<td>{c}" for c in row) for row in rows) + "</table>"


def _state(state: str) -> str:
    return f"<span class={escape(state) if state in STATES else 'unknown'}>{escape(state)}</span>"


REQUEST_CLASSES = {"done": "ready", "approved": "ready", "declined": "missing", "expired": "missing",
                   "failed": "missing"}


def _request_state(state: str) -> str:
    return f"<span class={REQUEST_CLASSES.get(state, 'unknown')}>{escape(state)}</span>"


def _counts(summary: dict[str, int]) -> str:
    return ", ".join(f"{summary[s]} {_state(s)}" for s in STATES if summary.get(s)) or "no needs"


def _todo(need: dict[str, Any], unit: str) -> str:
    """What gets this need to ready: nothing, the engine's fulfil, the proposal, or a person's step."""
    if need["state"] == "ready":
        return ""
    fix = escape(need.get("fix") or "")
    if need["grade"] == "auto" and need["state"] in ("missing", "drift"):
        return AUTO.format(unit=escape(unit)) + (f"<br>{fix}" if fix else "")
    return fix


def _needs(document: dict[str, Any]) -> str:
    unit = document["service"]["unit"]
    rows = [[f"<code>{escape(n['id'])}</code>", escape(n["type"]), escape(n["grade"]), _state(n["state"]),
             escape(n.get("checked_at") or ""), escape(n.get("summary") or ""), _todo(n, unit)]
            for n in document["needs"]]
    walk = document["walk"]
    said = (f"<p>Walked {escape(walk['started_at'])} from {escape(walk['from'])} by {escape(walk['by'] or 'unknown')}, "
            f"{walk['seconds']:.1f} s, at <code>{escape(document['service']['commit'][:12])}</code> with pubdeploy "
            f"{escape(document['service']['pubdeploy'])}: {_counts(document['summary'])}.</p>")
    return said + _table(["Need", "Type", "Grade", "State", "Checked", "Found", "To make it ready"], rows,
                         "This unit declares no needs.")


def _service_link(repository: str) -> str:
    return f'<a href="/services/{escape(repository)}">{escape(repository)}</a>'


def _fulfillable(document: dict[str, Any]) -> bool:
    return any(n["grade"] == "auto" and n["state"] in ("missing", "drift") for n in document["needs"])


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600} h {seconds % 3600 // 60} min" if seconds >= 3600 else f"{seconds // 60} min"


def _source(ask: Ask) -> str:
    """The repository and unit a request is about, or the caller that asked."""
    if ask.repository:
        return escape(ask.repository) + (f", unit {escape(ask.unit)}" if ask.unit else "")
    return escape(ask.requester.name) + (f" ({escape(ask.unit)})" if ask.unit else "")


def _requester(ask: Ask) -> str:
    r = ask.requester
    if r.kind == "github":
        return f"{escape(r.workflow_ref or r.repository)} run by {escape(r.actor or 'unknown')}"
    return escape(r.name) + (" (ServiceAccount)" if r.kind == "kubernetes" else "")


def _plan_table(ask: Ask) -> str:
    if ask.kind == "gate":
        return f"<h2>Plan</h2><pre>{escape(ask.text)}</pre>"
    if ask.plan is None:
        return "<h2>Plan</h2><p>The unit is being walked read-only for its plan.</p>"
    rows = [[f"<code>{escape(n['need'])}</code>", escape(n["type"]), escape(n["grade"]), _state(n["state"]),
             escape(n["change"])] for n in ask.plan]
    return "<h2>Plan</h2>" + _table(["Need", "Type", "Grade", "State", "What the fulfil changes"], rows,
                                    "Nothing to do: every need is ready.")


def create_app(engine: Engine, journals: Journals, approvals: Approvals, callers: Callers | None = None,
               site: str = "https://convergence.sustenaut.eu", key: bytes | None = None,
               clock=time.time) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    key = key or secrets.token_bytes(32)  # a new pod's forms are made afresh

    def token(*parts: str) -> str:
        return hmac.new(key, "\n".join(parts).encode(), hashlib.sha256).hexdigest()

    def person(request: Request) -> str:
        """The signed-in person holding the role, or ""."""
        actor = request.headers.get("x-forwarded-email", "").strip()
        groups = {g.strip() for g in request.headers.get("x-forwarded-groups", "").split(",")}
        return actor if actor and ROLE in groups else ""

    def actor_of(request: Request) -> str:
        actor = person(request)
        if not actor:
            raise HTTPException(403, "no signed-in person with the role convergence")
        if request.headers.get("origin", "") != site:
            raise HTTPException(403, "a button's form comes from this site's own page")
        return actor

    async def form_of(request: Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}

    def button(request: Request, repository: str, unit: str) -> str:
        actor = person(request)
        if not actor or engine.fulfil_token is None:
            return ""
        return (f'<form method="post" action="/services/{escape(repository)}/fulfil">'
                f'<input type="hidden" name="unit" value="{escape(unit)}">'
                f'<input type="hidden" name="token" value="{token("fulfil", actor, repository, unit)}">'
                f'<button>Fulfil the auto needs of {escape(unit)}</button> as {escape(actor)}: '
                f"the plan is walked first, and you approve it</form>")

    def service(owner: str, name: str) -> Service:
        found = engine.status.services.get(f"{owner}/{name}")
        if found is None:
            raise HTTPException(404, "no such service")
        return found

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        ok = engine.healthy()
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    @app.get("/")
    def index() -> HTMLResponse:
        status = engine.status
        rows = []
        for s in status.services.values():
            units = "<br>".join(f"{escape(d['service']['unit'])}: {_counts(d['summary'])}"
                                for d in (s.last.documents if s.last else []))
            rows.append([_service_link(s.repository), units or "not walked yet",
                         escape(s.last.started_at if s.last else ""), escape(s.failure)])
        problem = f"<p class=missing>{escape(status.problem)}</p>" if status.problem else ""
        problem += f'<p><a href="/approvals">Approvals</a>: {approvals.awaiting()} awaiting a decision.</p>'
        return _page("Convergence", problem + _table(["Service", "Units", "Last walk", "Latest try failed"], rows,
                                                     "No service has the role deploy yet, or no round has run."))

    @app.get("/services/{owner}/{name}")
    def page(owner: str, name: str, request: Request) -> HTMLResponse:
        s = service(owner, name)
        body = f"<p class=missing>The latest try, {escape(s.tried_at)}, failed: {escape(s.failure)}</p>" \
            if s.failure else ""
        for document in s.last.documents if s.last else []:
            unit = document["service"]["unit"]
            body += f"<h2>Unit {escape(unit)}</h2>" + _needs(document)
            if _fulfillable(document):
                body += button(request, s.repository, unit)
            try:
                walks = journals.history(s.repository, unit)
                rows = [[f'<a href="/services/{escape(s.repository)}/walks/{escape(w["hash"])}">{escape(w["time"])}</a>',
                         escape(w["principal"])] for w in walks]
                body += "<h3>Walks</h3>" + _table(["When", "Written by"], rows, "No walk is journaled yet.")
            except Exception as e:  # noqa: BLE001 - the history is optional; the page says why it is missing
                body += f"<p>The walk history could not be read ({escape(type(e).__name__)}).</p>"
        if not body:
            body = "<p>Not walked yet.</p>"
        return _page(s.repository, body)

    @app.get("/services/{owner}/{name}/walks/{hash_}")
    def journal(owner: str, name: str, hash_: str) -> HTMLResponse:
        s = service(owner, name)
        try:
            document = journals.journal(hash_)
        except Exception as e:  # noqa: BLE001 - not a hash, not stored, or storage refused
            missing = isinstance(e, ValueError) or type(e).__name__ == "NotFound"
            raise HTTPException(404 if missing else 502, "no such walk" if missing else "storage did not answer") \
                from None
        if document.get("service", {}).get("repository") != s.repository:
            raise HTTPException(404, "no such walk")
        return _page(f"{s.repository}, unit {document['service']['unit']}", _needs(document))

    @app.post("/services/{owner}/{name}/fulfil")
    async def fulfil(owner: str, name: str, request: Request):
        s = service(owner, name)
        actor = actor_of(request)
        form = await form_of(request)
        unit = form.get("unit", "")
        if not hmac.compare_digest(form.get("token", ""), token("fulfil", actor, s.repository, unit)):
            raise HTTPException(403, "the form has expired, or is someone else's: open the page again")
        started = approvals.press(s.repository, unit, actor)
        if isinstance(started, str):
            return _page(f"{s.repository}, unit {unit}", f"<p class=missing>Not started: {escape(started)}</p>",
                         status=409)
        return RedirectResponse(f"/approvals/{started.id}", status_code=303)

    @app.get("/fulfils/{id_}")
    def fulfilled(id_: str) -> RedirectResponse:  # 4b's links: a Fulfil is a request now
        return RedirectResponse(f"/approvals/{id_}", status_code=303)

    @app.get("/approvals")
    def listed() -> HTMLResponse:
        now = clock()
        opened, ended = approvals.listed()

        def rows(asks: list[Ask]) -> list[list[str]]:
            return [[f'<a href="/approvals/{escape(a.id)}">{escape(a.kind)}</a>', _source(a), _requester(a),
                     _age(now - a.created_at), _request_state(a.state)] for a in asks]
        head = ["Kind", "Repository and unit, or caller", "Asked by", "Age", "State"]
        return _page("Approvals", "<h2>Open</h2>" + _table(head, rows(opened), "No request is open.")
                     + "<h2>Ended</h2>" + _table(head, rows(ended), "No request has ended yet."))

    @app.get("/approvals/{id_}")
    def approval(id_: str, request: Request) -> HTMLResponse:
        ask = approvals.get(id_)
        if ask is None:
            raise HTTPException(404, "no such request")
        run = f' (<a href="{escape(ask.run_url)}">the run</a>)' if ask.run_url else ""
        sha = f" at <code>{escape(ask.requester.sha[:12])}</code>" if ask.requester.sha else ""
        body = (f"<p>{escape(ask.kind)} of {_source(ask)}, asked by {_requester(ask)}{sha}{run}. "
                f"State: {_request_state(ask.state)}.</p>")
        if ask.plan_sha256:
            body += f"<p>Plan sha256 <code>{escape(ask.plan_sha256)}</code></p>"
        body += _plan_table(ask)
        actor = person(request)
        if ask.state == "awaiting" and actor:
            hidden = (f'<input type="hidden" name="token" value="{token("decide", actor, ask.id, ask.plan_sha256)}">'
                      f'<input type="hidden" name="plan" value="{escape(ask.plan_sha256)}">')
            body += (f'<form method="post" action="/approvals/{escape(ask.id)}">{hidden}'
                     f'<button name="decision" value="approve">Approve</button> '
                     f'<button name="decision" value="decline">Decline</button> as {escape(actor)}</form>')
        if ask.error:
            body += f"<p class=missing>It did not finish: {escape(ask.error)}</p>"
        body += "<h2>Changes</h2>" + _table(["State", "When", "By"], [
            [_request_state(c["state"]), escape(c["at"]), escape(c["by"])] for c in ask.changes], "None yet.")
        if ask.kind == "fulfil" and (ask.said or ask.needs):
            body += f"<h2>The Fulfil</h2><pre>{escape(chr(10).join(ask.said))}</pre>"
            body += "".join(_needs(d) for d in ask.documents) or _table(
                ["Need", "State"], [[f"<code>{escape(n['need'])}</code>", _state(n["state"])] for n in ask.needs],
                "No need.")
        page = _page(f"Request {ask.id}", body + '<p><a href="/approvals">Back to the approvals</a></p>')
        if ask.state in ("planning", "running") or (ask.state == "approved" and ask.kind == "fulfil"):
            page.headers["Refresh"] = "5"  # no script: the browser reloads it
        return page

    @app.post("/approvals/{id_}")
    async def decide(id_: str, request: Request):
        actor = actor_of(request)
        ask = approvals.get(id_)
        if ask is None:
            raise HTTPException(404, "no such request")
        form = await form_of(request)
        if form.get("plan", "") != ask.plan_sha256 or not hmac.compare_digest(
                form.get("token", ""), token("decide", actor, ask.id, ask.plan_sha256)):
            raise HTTPException(403, "the form has expired, is someone else's, or shows another plan: open it again")
        if form.get("decision") not in ("approve", "decline"):
            raise HTTPException(400, "approve or decline")
        refused = approvals.decide(ask.id, actor, form["decision"] == "approve")
        if refused:
            return _page(f"Request {ask.id}", f"<p class=missing>Not done: {escape(refused)}</p>", status=409)
        return RedirectResponse(f"/approvals/{ask.id}", status_code=303)

    def requester_of(request: Request):
        if callers is None:
            raise Refused(503, "no-callers")
        return callers.verify(request.headers.get("authorization", ""))

    def refusal(e: Refused) -> JSONResponse:
        return JSONResponse({"refused": e.reason}, status_code=e.status, headers=HEADERS)

    @app.post("/requests")
    async def ask(request: Request) -> JSONResponse:
        try:
            requester = requester_of(request)
            raw = await request.body()
            if len(raw) > BODY_BYTES:
                raise Refused(413, "too-large")
            try:
                body = json.loads(raw)
            except ValueError:
                raise Refused(400, "not-json") from None
            taken = approvals.ask(requester, body)
        except Refused as e:
            return refusal(e)
        return JSONResponse(approvals.answer(taken, site), status_code=201,
                            headers={**HEADERS, "Location": f"/requests/{taken.id}"})

    @app.get("/requests/{id_}")
    def poll(id_: str, request: Request) -> JSONResponse:
        try:
            requester = requester_of(request)
            ask = approvals.get(id_)
            if ask is None or not ask.requester.same(requester):
                raise Refused(403, "not-your-request")
        except Refused as e:
            return refusal(e)
        return JSONResponse(approvals.answer(ask, site), headers=HEADERS)

    return app
