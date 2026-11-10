"""The convergence engine: every deploy service's walk, read-only, every 30 minutes.

The services are the repositories whose entry in the owner's state repository has
the role `deploy`. Each is a shallow clone of its default branch, refreshed before
each walk, and walked with its own ./pubdeploy, so the engine never chooses a
pubdeploy, pubsec or need type for a service. Each walk is journaled to storage's
tenant `plane` by pubdeploy itself; the engine shows the journals, one page per
service, and publishes each need's state as a fact, from which observability's
`need_drift` rule finds a need that left `ready`.

It changes nothing (step 4a): it runs `pubdeploy check`, never `walk --fulfil`.
"""

__version__ = "0.1.0"
