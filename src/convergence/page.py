"""The pages: plain HTML from the server, no script, nothing loaded from elsewhere.

They sit behind oauth2-proxy and the team sign-in (k8s/oauth2-proxy.yaml), which
lets in only people holding the role `convergence`. They show what the walks found
and change nothing.

    /                                         every service, its units' counts
    /services/OWNER/NAME                      each unit's needs, and its walks
    /services/OWNER/NAME/walks/HASH           one walk's journal, from storage
    POST /services/OWNER/NAME/fulfil          a person's Fulfil of one unit
    /fulfils/ID                               what that Fulfil said, and how it ended
    /healthz                                  open: has a round ended lately

A Fulfil is the one thing a page does. The person is oauth2-proxy's X-Forwarded-Email,
which only the proxy can set (admin/network-policy.yaml lets nothing else reach the
engine); the form carries a token made from that person, the service and the unit, and
the request must come from this site's own page (Origin), so another site cannot make
a signed-in browser press the button.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from html import escape
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
AUTO = ("The engine fulfils this from step 4b. Until then, from a laptop: "
        "<code>./with-secrets ./pubdeploy walk --fulfil --unit {unit}</code>")


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


def create_app(engine: Engine, journals: Journals, site: str = "https://convergence.sustenaut.eu",
               key: bytes | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    key = key or secrets.token_bytes(32)  # a new pod's forms are made afresh

    def token(actor: str, repository: str, unit: str) -> str:
        return hmac.new(key, f"{actor}\n{repository}\n{unit}".encode(), hashlib.sha256).hexdigest()

    def actor_of(request: Request) -> str:
        actor = request.headers.get("x-forwarded-email", "").strip()
        if not actor:
            raise HTTPException(403, "no signed-in person: a Fulfil needs the team sign-in")
        return actor

    def button(request: Request, repository: str, unit: str) -> str:
        actor = request.headers.get("x-forwarded-email", "").strip()
        if not actor or engine.fulfil_token is None:
            return ""
        return (f'<form method="post" action="/services/{escape(repository)}/fulfil">'
                f'<input type="hidden" name="unit" value="{escape(unit)}">'
                f'<input type="hidden" name="token" value="{token(actor, repository, unit)}">'
                f'<button>Fulfil the auto needs of {escape(unit)}</button> as {escape(actor)}</form>')

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
        if request.headers.get("origin", "") != site:
            raise HTTPException(403, "a Fulfil comes from this site's own page")
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
        unit = form.get("unit", "")
        if not hmac.compare_digest(form.get("token", ""), token(actor, s.repository, unit)):
            raise HTTPException(403, "the form has expired, or is someone else's: open the page again")
        started = engine.fulfil(s.repository, unit, actor)
        if isinstance(started, str):
            return _page(f"{s.repository}, unit {unit}", f"<p class=missing>Not started: {escape(started)}</p>",
                         status=409)
        return RedirectResponse(f"/fulfils/{started.id}", status_code=303)

    @app.get("/fulfils/{id_}")
    def fulfilled(id_: str) -> HTMLResponse:
        job = engine.status.fulfils.get(id_)
        if job is None:
            raise HTTPException(404, "no such fulfil")
        said = job.walk.said if job.walk else []
        state = ("<p>Running: this page reloads itself.</p>" if not job.done else
                 f"<p class=missing>It did not finish: {escape(job.error)}</p>" if job.error else
                 "<p class=ready>Done. Each unit's needs below are as the walk left them.</p>")
        body = (f"<p>Asked by {escape(job.actor)} at {escape(job.started_at)}.</p>{state}"
                f"<pre>{escape(chr(10).join(said))}</pre>"
                + "".join(_needs(d) for d in (job.walk.documents if job.walk and job.done else []))
                + f'<p><a href="/services/{escape(job.repository)}">Back to {escape(job.repository)}</a></p>')
        page = _page(f"Fulfil of {job.repository}, unit {job.unit}", body)
        if not job.done:  # no script: the browser reloads it
            page.headers["Refresh"] = "5"
        return page

    return app
