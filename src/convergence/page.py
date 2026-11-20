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
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .approvals import Approvals, Ask
from .callers import Callers, Refused
from .engine import Engine, Service
from .journals import Journals

STATES = ("ready", "missing", "drift", "blocked", "unknown")
# The look is Sustenaut/theme's (vendored in theme/, theme.json pins its tag and hashes):
# its tokens, fonts and element defaults, and these few rules made from its tokens.
STYLE = ("body{margin:0;padding:2rem 1rem}main{margin:0 auto;max-width:76rem}"
         "h1,h2,h3{font-family:var(--font-display);line-height:1.25}h2{margin-top:2.5rem}"
         "table{border-collapse:collapse;width:100%;margin-bottom:1.5rem;background:var(--color-surface);"
         "border-radius:var(--radius-panel);overflow:hidden}th,td{padding:.4rem .6rem;"
         "border-bottom:1px solid var(--color-rule);text-align:left;vertical-align:top}"
         "th{color:var(--color-muted);font-weight:600}"
         ".ready{color:var(--color-primary)}.missing,.drift,.blocked{font-weight:600;color:var(--color-bad)}"
         ".unknown{color:var(--color-warn)}"
         ".actions{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;margin:1rem 0;padding:.75rem 1rem;"
         "background:var(--color-surface);border:1px solid var(--color-rule);border-radius:var(--radius-panel)}"
         ".actions form{display:inline}"
         "button{font:inherit;font-weight:600;min-height:44px;padding:.5rem 1rem;border-radius:var(--radius-control);"
         "border:1px solid var(--color-primary);background:var(--color-primary);color:var(--color-on-primary);"
         "cursor:pointer}button.quiet{background:transparent;color:var(--color-ink);border-color:var(--color-rule-strong)}"
         "button:disabled{background:var(--color-sunken);color:var(--color-muted);border-color:var(--color-rule);"
         "cursor:not-allowed}.why{color:var(--color-muted);font-size:.9em}"
         ".callout{padding:.75rem 1rem;border-radius:var(--radius-control);background:var(--color-warn-soft)}"
         "pre{background:var(--color-sunken);padding:.75rem;border-radius:var(--radius-control);overflow:auto}")
THEME = ("fonts.css", "tokens.css", "base.css")
HEADERS = {"Cache-Control": "private, no-store",
           "Content-Security-Policy": "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'"}
# The run pages run the bundle (/static/run.js), which polls this site: script and connect from here only.
RUN_HEADERS = {**HEADERS, "Content-Security-Policy": HEADERS["Content-Security-Policy"]
               + "; script-src 'self'; connect-src 'self'"}
# The run page's bundle and styles (web/, built into static/ when the image is built): React on
# @sustenaut/ui. A page without them still works: it shows the run as it is and refreshes.
STATIC_DIR = Path(__file__).parent / "static"
STATIC_TYPES = {"run.js": "text/javascript; charset=utf-8", "run.css": "text/css; charset=utf-8"}
REFRESH_S = 5
AUTO = ("The engine fulfils this once a person approves its plan (the unit's Fulfil button). From a laptop: "
        "<code>./with-secrets ./pubdeploy walk --fulfil --unit {unit}</code>")
ROLE = "convergence"
BODY_BYTES = 16384


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f"<title>{escape(title)}</title>"
                        + "".join(f'<link rel="stylesheet" href="/theme/{name}">' for name in THEME)
                        + f"<style>{STYLE}</style><main><h1>{escape(title)}</h1>{body}</main></html>",
                        status_code=status, headers=HEADERS)


def _data(value: Any) -> str:
    """JSON for a data block: no "</" a browser would read as the block's end."""
    return json.dumps(value).replace("</", "<\\/")


def _run_page(title: str, body: str, data: dict[str, Any], status: int = 200) -> HTMLResponse:
    """A run page: the theme and the run page's styles, the data the bundle starts from, the page as it
    is now for a reader without script (which refreshes every few seconds), and the bundle."""
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<noscript><meta http-equiv="refresh" content="{REFRESH_S}"></noscript>'
                        f"<title>{escape(title)}</title>"
                        + "".join(f'<link rel="stylesheet" href="/theme/{name}">' for name in THEME)
                        + '<link rel="stylesheet" href="/static/run.css">'
                        f"<style>{STYLE}</style>"
                        f'<script type="application/json" id="page-data">{_data(data)}</script>'
                        f'<main><div id="page"><h1>{escape(title)}</h1>{body}</div></main>'
                        '<script src="/static/run.js"></script></html>',
                        status_code=status, headers=RUN_HEADERS)


def _run_body(view: dict[str, Any]) -> str:
    """The run as a reader without script sees it: its state, its needs and its journal."""
    head = (f"<p>Unit <b>{escape(view['unit'])}</b> · {escape(view['mode'])} · "
            f"<b>{escape(view['state'])}</b>"
            + (f' · <a href="{escape(view["github_run_url"])}">GitHub run</a>' if view.get("github_run_url") else "")
            + f" · <code>{escape(view['id'])}</code></p>")
    needs = [[escape(name), escape(n["grade"]), escape(n["mode"]), f"<span class={escape(n['state'])}>"
              f"{escape(n['state'])}</span>", escape(n["result"]), escape(n["stop"])]
             for name, n in view["needs"].items()]
    journal = "".join(f"<li><code>{escape(e['at'][11:19])} · {escape(e.get('need') or 'run')} · "
                      f"{escape(e.get('from') or '')} → {escape(e.get('to') or '')}"
                      f"{' · ' + escape(e['by']) if e.get('by') else ''}</code>" for e in view["journal"])
    return (head + _table(["Need", "Grade", "Mode", "State", "Last result", "On Stop"], needs, "No needs yet.")
            + f"<h2>Journal</h2><ol>{journal}</ol>"
            + f"<p class=why>Without script this page refreshes every {REFRESH_S} s; approving and stopping "
              "need script.</p>")


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


def _found(need: dict[str, Any]) -> str:
    """What the walk found: its summary, then each detail it named (names, never a value)."""
    details = [escape(str(d)) for d in need.get("details") or [] if str(d).strip()]
    return escape(need.get("summary") or "") + "".join(f"<br><span class=why>{d}</span>" for d in details[:10])


def _needs(document: dict[str, Any]) -> str:
    unit = document["service"]["unit"]
    rows = [[f"<code>{escape(n['id'])}</code>", escape(n["type"]), escape(n["grade"]), _state(n["state"]),
             escape(n.get("checked_at") or ""), _found(n), _todo(n, unit)]
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


def _unusable(label: str, why: str, slot: str) -> str:
    """A button that cannot be used now: shown disabled, and why, on hover and to a screen reader."""
    return (f'<button type="button" disabled title="{escape(why)}" aria-describedby="why-{escape(slot)}">'
            f'{escape(label)}</button> <span class=why id="why-{escape(slot)}">{escape(why)}</span>')


THEME_DIR = Path(__file__).parent / "theme"
THEME_TYPES = {"css": "text/css; charset=utf-8", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}


def _theme() -> dict[str, bytes]:
    """The vendored theme's files, each checked against theme.json's sha256: one that differs is not served."""
    listed = json.loads((THEME_DIR.parent / "theme.json").read_text(encoding="utf-8"))["files"]
    pinned = {f["path"]: f["sha256"] for f in listed}
    files = {name: (THEME_DIR / name).read_bytes() for name in pinned}
    return {name: data for name, data in files.items() if hashlib.sha256(data).hexdigest() == pinned[name]}


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
               clock=time.time, runs: Any = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    key = key or secrets.token_bytes(32)  # a new pod's forms are made afresh

    def token(*parts: str) -> str:
        return hmac.new(key, "\n".join(parts).encode(), hashlib.sha256).hexdigest()

    def person(request: Request) -> str:
        """The signed-in person holding the role, or "". oauth2-proxy adds one
        X-Forwarded-Groups line per group, so every line counts, each maybe a list."""
        actor = request.headers.get("x-forwarded-email", "").strip()
        groups = {g.strip() for line in request.headers.getlist("x-forwarded-groups") for g in line.split(",")}
        return actor if actor and ROLE in groups else ""

    def why_not_fulfil(actor: str, repository: str, document: dict[str, Any]) -> str:
        """Why the unit's Fulfil cannot be pressed now, or ""."""
        unit = document["service"]["unit"]
        if not actor:
            return "Sign in with the role convergence to fulfil"
        if engine.fulfil_token is None:
            return "This engine has no installation token that may write: it fulfils nothing"
        if all(n["state"] == "ready" for n in document["needs"]):
            return "Nothing to fulfil: every need is ready"
        if not _fulfillable(document):
            return "Nothing the engine can fulfil: what is left is a person's step or a proposal"
        if any(a.kind == "fulfil" and a.repository == repository and a.unit == unit and not a.ended
               for a in approvals.asks.values()):
            return "A request to fulfil this unit is already open: see Approvals"
        if engine.walking(repository).locked():
            return "A walk of this service is running: reload when it ends"
        return ""

    def actor_of(request: Request) -> str:
        actor = person(request)
        if not actor:
            raise HTTPException(403, "no signed-in person with the role convergence")
        if request.headers.get("origin", "") != site:
            raise HTTPException(403, "a button's form comes from this site's own page")
        return actor

    async def form_of(request: Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}

    def button(request: Request, repository: str, document: dict[str, Any], slot: str) -> str:
        """The unit's Fulfil, always shown: pressed, its plan is walked and you approve it."""
        actor, unit = person(request), document["service"]["unit"]
        label = f"Fulfil {unit}"
        why = why_not_fulfil(actor, repository, document)
        if why:
            return _unusable(label, why, slot)
        return (f'<form method="post" action="/services/{escape(repository)}/fulfil">'
                f'<input type="hidden" name="unit" value="{escape(unit)}">'
                f'<input type="hidden" name="token" value="{token("fulfil", actor, repository, unit)}">'
                f'<button title="Walk the plan, then approve it as {escape(actor)}">{escape(label)}</button></form>'
                f" <span class=why>as {escape(actor)}: the plan is walked first, and you approve it</span>")

    def service(owner: str, name: str) -> Service:
        found = engine.status.services.get(f"{owner}/{name}")
        if found is None:
            raise HTTPException(404, "no such service")
        return found

    theme = _theme()

    @app.get("/theme/{path:path}")
    def themed(path: str) -> Response:
        """The theme's files, as theme.json pins them; nothing else is served from here."""
        if path not in theme:
            raise HTTPException(404, "no such file")
        return Response(theme[path], media_type=THEME_TYPES[path.rsplit(".", 1)[-1]],
                        headers={"Cache-Control": "private, max-age=86400"})

    static = {name: (STATIC_DIR / name).read_bytes() for name in STATIC_TYPES if (STATIC_DIR / name).is_file()}

    @app.get("/static/{name}")
    def static_file(name: str) -> Response:
        """The run page's bundle and styles, built into static/ with the image; nothing else."""
        if name not in static:
            raise HTTPException(404, "no such file")
        return Response(static[name], media_type=STATIC_TYPES[name],
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})

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
        documents = s.last.documents if s.last else []
        if documents:
            body += '<div class=actions aria-label="Fulfil">' + "<br>".join(
                button(request, s.repository, d, f"top-{i}") for i, d in enumerate(documents)) + "</div>"
        for i, document in enumerate(documents):
            unit = document["service"]["unit"]
            body += (f"<h2>Unit {escape(unit)}</h2><div class=actions>{button(request, s.repository, document, f'unit-{i}')}"
                     f"</div>" + _needs(document))
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
        document = next((d for d in (s.last.documents if s.last else []) if d["service"]["unit"] == unit), None)
        why = why_not_fulfil(actor, s.repository, document) if document else ""
        started = why or approvals.press(s.repository, unit, actor)
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
        pressed = ask.state == "awaiting" and ask.approve_on_plan
        why = ("" if ask.state == "awaiting" and actor and not pressed else
               f"This is {ask.approve_on_plan}'s Fulfil: it is approved by them now its plan is walked" if pressed else
               "Sign in with the role convergence to decide" if ask.state == "awaiting" else
               "The plan is being walked: it can be decided once it is shown" if ask.state == "planning" else
               f"This request is {ask.state}: it no longer awaits a decision")
        if why:  # a person's own Fulfil is approved by them as soon as its plan is stored, never by a button
            body += ('<div class=actions><span role=group aria-label="Decide">'
                     + _unusable("Approve", why, "approve") + "</span> <button type=button class=quiet disabled "
                     f'title="{escape(why)}" aria-describedby="why-approve">Decline</button></div>')
        else:
            hidden = (f'<input type="hidden" name="token" value="{token("decide", actor, ask.id, ask.plan_sha256)}">'
                      f'<input type="hidden" name="plan" value="{escape(ask.plan_sha256)}">')
            body += (f'<div class=actions><form method="post" action="/approvals/{escape(ask.id)}">{hidden}'
                     f'<button name="decision" value="approve">Approve</button> '
                     f'<button name="decision" value="decline" class=quiet>Decline</button></form>'
                     f" <span class=why>as {escape(actor)}</span></div>")
        if ask.error:
            body += f"<p class=missing>It did not finish: {escape(ask.error)}</p>"
        if ask.note:
            body += f"<p class=why>{escape(ask.note)}</p>"
        body += "<h2>Changes</h2>" + _table(["State", "When", "By"], [
            [_request_state(c["state"]), escape(c["at"]), escape(c["by"])] for c in ask.changes], "None yet.")
        if ask.kind == "fulfil" and (ask.said or ask.needs):
            body += f"<h2>The Fulfil</h2><pre>{escape(chr(10).join(ask.said))}</pre>"
            body += "".join(_needs(d) for d in ask.documents) or _table(
                ["Need", "State"], [[f"<code>{escape(n['need'])}</code>", _state(n["state"])] for n in ask.needs],
                "No need.")
        page = _page(f"Request {ask.id}", body + '<p><a href="/approvals">Back to the approvals</a></p>')
        if ask.state in ("planning", "running") or pressed or (ask.state == "approved" and ask.kind == "fulfil"):
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
        wanted = "approved" if form["decision"] == "approve" else "declined"
        refused = approvals.decide(ask.id, actor, wanted == "approved")
        if refused and any(c["state"] == wanted and c["by"] == actor for c in ask.changes):
            refused = ""  # their own decision already counted (a second press, or their Fulfil's own)
        if refused:
            return _page(f"Request {ask.id}", f"<p class=missing>Not done: {escape(refused)}</p>", status=409)
        return RedirectResponse(f"/approvals/{ask.id}", status_code=303)

    def requester_of(request: Request):
        if callers is None:
            raise Refused(503, "no-callers")
        return callers.verify(request.headers.get("authorization", ""))

    def refusal(e: Refused) -> JSONResponse:
        return JSONResponse({"refused": e.reason}, status_code=e.status, headers=HEADERS)

    # --- Live runs (runs.py): JSON for the run page's poll and buttons, and a remote executor

    def run_of(id_: str) -> Any:
        found = runs.get(id_) if runs is not None else None
        if found is None:
            raise HTTPException(404, "no such run")
        return found

    async def json_of(request: Request) -> Any:
        try:
            return json.loads(await request.body() or b"{}")
        except ValueError:
            raise HTTPException(400, "the body is JSON") from None

    def answered(call: Any, ok: int = 200) -> JSONResponse:
        from .runs import Refusal  # noqa: PLC0415
        try:
            out = call()
        except Refusal as e:
            return JSONResponse({"refused": e.why}, status_code=e.status, headers=HEADERS)
        return JSONResponse(out, status_code=ok, headers=HEADERS)

    def units() -> dict[str, list[str]]:
        """Each service's units, as its last walk found them: what Start a run offers."""
        return {s.repository: [d["service"]["unit"] for d in (s.last.documents if s.last else [])]
                for s in engine.status.services.values()}

    @app.get("/runs/new")
    def new_run(request: Request) -> HTMLResponse:
        """Start a run: for a signed-in person, the form (the bundle draws it); for anyone else, why not."""
        if runs is None:
            raise HTTPException(503, "this engine has no live runs")
        if not person(request):
            return _page("Start a run", "<p>Sign in with the role convergence to start a run.</p>", 403)
        body = ("<noscript><p>Starting a run needs script: the form reads each unit's machine.</p></noscript>"
                + _table(["Service", "Units"], [[escape(r), escape(", ".join(u))] for r, u in units().items()],
                         "No service has the role deploy yet."))
        return _run_page("Start a run", body, {"kind": "new", "services": units()})

    @app.get("/runs/new/needs")
    def new_run_needs(request: Request, repository: str, unit: str, mode: str = "fulfil") -> JSONResponse:
        """The needs of a unit's compiled machine, each with the mode it would run in: Start a run's list."""
        actor = person(request)
        if not actor:
            raise HTTPException(403, "no signed-in person with the role convergence")
        if repository not in units() or unit not in units()[repository] or mode not in ("check", "fulfil"):
            raise HTTPException(404, "no such unit")
        machine = engine.machine(repository, unit, mode, {}, actor)
        if isinstance(machine, str):
            return JSONResponse({"refused": machine}, status_code=409, headers=HEADERS)
        regions = ((machine.get("states") or {}).get("running") or {}).get("states") or {}
        needs = [{"name": str((r.get("meta") or {}).get("need") or key), "grade": str((r.get("meta") or {}).get("grade")),
                  "mode": "check" if mode == "check" else str((r.get("meta") or {}).get("mode") or "fulfil")}
                 for key, r in regions.items()]
        return JSONResponse({"needs": needs}, headers=HEADERS)

    @app.get("/runs/{id_}")
    def run_state(id_: str, request: Request, since: int | None = None) -> Response:
        """The run: JSON for a poll (`since`, or Accept: application/json), else its page."""
        run = run_of(id_)
        view = runs.view(run, since or 0)
        if since is not None or "application/json" in request.headers.get("accept", ""):
            headers = {**HEADERS, "Cache-Control": "private, max-age=3600"} if run.ended else HEADERS
            return JSONResponse(view, headers=headers)
        title = f"{run.repository} · {run.unit}"
        return _run_page(title, _run_body(view), {"kind": "run", "run": view, "may_act": bool(person(request))})

    @app.post("/runs")
    async def start_run(request: Request) -> JSONResponse:
        actor = actor_of(request)
        body = await json_of(request)
        if runs is None:
            raise HTTPException(503, "this engine has no live runs")
        if not isinstance(body, dict) or body.get("mode") not in ("check", "fulfil"):
            raise HTTPException(400, "a run is {repository, unit, mode: check|fulfil, modes?}")
        try:
            taken = approvals.person_run(actor, str(body.get("repository") or ""), str(body.get("unit") or ""),
                                         body["mode"], body.get("modes") or {})
        except Refused as e:
            return refusal(e)
        return JSONResponse(runs.view(runs.runs[taken.id]), status_code=201,
                            headers={**HEADERS, "Location": f"/runs/{taken.id}"})

    @app.post("/runs/{id_}/approve")
    async def approve_run(id_: str, request: Request) -> JSONResponse:
        actor, run = actor_of(request), run_of(id_)
        body = await json_of(request)
        if not isinstance(body, dict) or body.get("plan_key") != run.plan_key:
            return JSONResponse({"refused": "the plan changed: look again"}, status_code=409, headers=HEADERS)
        refused = approvals.decide(run.request_id, actor, True)
        if refused:
            return JSONResponse({"refused": refused}, status_code=409, headers=HEADERS)
        return JSONResponse(runs.view(run), headers=HEADERS)

    @app.post("/runs/{id_}/stop")
    def stop_run(id_: str, request: Request):
        actor, run = actor_of(request), run_of(id_)
        if run.ended:
            return Response(status_code=204, headers=HEADERS)
        return answered(lambda: (runs.stop(run, actor), runs.view(run))[1])

    @app.post("/runs/{id_}/needs/{name}/cancel")
    async def cancel_need(id_: str, name: str, request: Request) -> JSONResponse:
        actor, run = actor_of(request), run_of(id_)
        body = await json_of(request)
        return answered(lambda: (runs.cancel(run, name, body, actor), runs.view(run))[1])

    @app.post("/runs/{id_}/needs/{name}/mode")
    async def need_mode(id_: str, name: str, request: Request) -> JSONResponse:
        actor, run = actor_of(request), run_of(id_)
        body = await json_of(request)
        mode = body.get("mode") if isinstance(body, dict) else None
        return answered(lambda: {"plan_key": runs.set_mode(run, name, mode, actor)})

    @app.post("/runs/{id_}/events")
    async def run_event(id_: str, request: Request) -> JSONResponse:
        run = run_of(id_)
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not run.token or not hmac.compare_digest(given.strip(), run.token):
            return JSONResponse({"refused": "not-this-runs-executor"}, status_code=403, headers=HEADERS)
        body = await json_of(request)
        return answered(lambda: runs.event(run, body))

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
