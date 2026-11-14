"""What the engine's pod runs: `uvicorn convergence.main:app`, which starts the rounds.

Its own calls (GitHub's token mint, storage) go through the integrations library's
Caller to its call log: storage's `calls-convergence-engine` buffer, loaded into
BigQuery's `calls_convergence_engine`, as every sender's are. The walks' own calls
are the walked repository's pubdeploy's.
"""

from __future__ import annotations

import os
import threading

REPO = "Sustenaut/convergence-engine"
DATASET = "calls_convergence_engine"
SENDER = "convergence-engine"


def build():  # pragma: no cover - the pod's wiring, with real files and the network
    from integrations.calls import Caller, Network
    from integrations.clients.storage import Storage
    from integrations.storage_sink import StorageSink

    from . import facts, journals
    from .clones import Clones
    from .config import settings
    from .engine import Engine, prepare, read_secret
    from .page import create_app
    from .tokens import Tokens

    s = settings(os.environ)
    prepare(s.work)
    transport = Network()
    sink = StorageSink({REPO: DATASET}, transport=transport, token=lambda: read_secret(s.storage_token),
                       base_url=s.storage_url, sender=SENDER)
    caller = Caller(REPO, "engine", transport=transport, sink=sink)
    tokens = Tokens(caller, read_secret(s.github_app / "app-id"), read_secret(s.github_app / "app-key"),
                    read_secret(s.github_app / "installation-id"))
    plane = Storage(caller, journals.TENANT, base_url=s.storage_url, token_file=s.storage_token,
                    user_agent=SENDER)
    base = {k: os.environ[k] for k in ("PATH", "HOME", "KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT")
            if k in os.environ}
    engine = Engine(s, Clones(s.work, base_env=base), tokens.read, plane.for_tenant(facts.TENANT), base_env=base,
                    fulfil_token=tokens.write)
    stop = threading.Event()
    threading.Thread(target=engine.loop, args=(stop,), daemon=True, name="rounds").start()
    return create_app(engine, journals.Journals(plane))


app = build() if os.environ.get("CONVERGENCE_SERVE") else None
