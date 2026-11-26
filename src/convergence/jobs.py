"""The Job executor of live runs (step 4): each approved run walked by a Kubernetes Job in
the service's own namespace, not in the engine's process.

On approve the engine makes the Job `run-<id>` in the namespace of the run's unit (as
pubdeploy reads the repository's deploy block), and beside it a Secret holding only the
run's token, which the Job owns, so both go when the Job does. The Job runs as the
namespace's ServiceAccount `pubdeploy-executor`, with the namespace's own Secret
`pubdeploy-executor` (if it has one) as its environment: the credentials a fulfil needs are
the namespace's, never the engine's. Its image is a thin one (Python, uv, git, kubectl,
kustomize, gh, bws, terraform, psql): it checks the repository out at the commit of main
the engine records on the run, then runs that commit's own `./pubdeploy executor`, so the
service's pins of pubdeploy and its providers decide, as on a laptop. The executor reads
the run and posts its moves at the engine's site with the run's token (`GET
/runs/ID/executor`, `POST /runs/ID/events`).

The engine holds the service's walk lock while the Job walks, as the inline executor does,
and watches the Job: one that failed or is gone while the run has not ended (its executor
never posted its exit) ends the run, "executor lost". Stop and cancel reach the Job through
the run itself, which its executor reads every few seconds.
"""

from __future__ import annotations

import base64
import json
import subprocess
import threading
from collections.abc import Callable
from typing import Any, Protocol

from .runs import Run, Runs

ACCOUNT = "pubdeploy-executor"  # the namespace's ServiceAccount, and its optional Secret of credentials
GRACE = 120  # what the Job may run past the engine's fulfil time
TTL = 86400  # how long a finished Job, and its token's Secret, are kept
WATCH = 10.0  # how often the Job is looked at
LABEL = "convergence.sustenaut.eu/run"

# The Job's one command: the repository at the recorded commit, then its own ./pubdeploy
# executor. A namespace's GITHUB_TOKEN (a private repository) reaches git as a header, never
# in a remote URL.
SCRIPT = """set -eu
git init -q repo
cd repo
git remote add origin "https://github.com/$REPOSITORY.git"
set --
if [ -n "${GITHUB_TOKEN:-}" ]; then
  set -- -c "http.https://github.com/.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)"
fi
git "$@" fetch -q --depth 1 --no-tags origin "$COMMIT"
git checkout -q --detach FETCH_HEAD
exec ./pubdeploy executor --engine "$ENGINE" --run "$RUN"
"""


def job_name(run_id: str) -> str:
    """`run-<id>`, as a Kubernetes name takes it (lower case)."""
    return f"run-{run_id.lower()}"


def job_manifest(run: Run, namespace: str, engine_url: str, deadline_seconds: int) -> dict[str, Any]:
    name = job_name(run.id)
    labels = {"app.kubernetes.io/name": ACCOUNT, "app.kubernetes.io/managed-by": "convergence-engine",
              LABEL: name}
    env = [{"name": "REPOSITORY", "value": run.repository}, {"name": "COMMIT", "value": run.commit},
           {"name": "ENGINE", "value": engine_url}, {"name": "RUN", "value": run.id},
           {"name": "HOME", "value": "/work/home"},
           {"name": "PUBDEPLOY_RUN_TOKEN", "valueFrom": {"secretKeyRef": {"name": name, "key": "token"}}}]
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0, "activeDeadlineSeconds": deadline_seconds + GRACE, "ttlSecondsAfterFinished": TTL,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": ACCOUNT, "restartPolicy": "Never",
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                        "fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": "executor", "image": run.image, "workingDir": "/work",
                        "command": ["sh", "-c", SCRIPT], "env": env,
                        "envFrom": [{"secretRef": {"name": ACCOUNT, "optional": True}}],
                        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "1Gi"}},
                        "volumeMounts": [{"name": "work", "mountPath": "/work"}]}],
                    "volumes": [{"name": "work", "emptyDir": {}}]}}}}


def token_manifest(run: Run, namespace: str, job_uid: str) -> dict[str, Any]:
    """The run's token, owned by its Job: gone with it."""
    name = job_name(run.id)
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace, "labels": {LABEL: name},
                         "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": name,
                                              "uid": job_uid}]},
            "data": {"token": base64.b64encode(run.token.encode()).decode()}}


def ended(job: dict[str, Any]) -> str:
    """Why a Job ended without its executor's exit reaching the engine, or "" while it may still."""
    for condition in (job.get("status") or {}).get("conditions") or []:
        if condition.get("status") == "True" and condition.get("type") == "Failed":
            return f"its Job failed ({condition.get('reason') or 'no reason given'})"
        if condition.get("status") == "True" and condition.get("type") == "Complete":
            return "its Job ended without the executor's exit"
    return ""


class Kube(Protocol):
    def create(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """The object made, as the API server answers it."""

    def job(self, namespace: str, name: str) -> dict[str, Any] | None:
        """The Job, or None once it is gone."""


class Kubectl:  # pragma: no cover - the pod's: kubectl as the engine's ServiceAccount; the suite's is a fake
    def __init__(self, env: dict[str, str], timeout: float = 30.0):
        self.env, self.timeout = env, timeout

    def _kubectl(self, args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(["kubectl", *args], input=stdin, env=self.env, capture_output=True, text=True,
                              timeout=self.timeout, check=False)

    def create(self, manifest: dict[str, Any]) -> dict[str, Any]:
        done = self._kubectl(["create", "-o", "json", "-f", "-"], json.dumps(manifest))
        if done.returncode != 0:
            raise RuntimeError(f"kubectl create {manifest['kind']}: {done.stderr.strip()[-300:]}")
        return json.loads(done.stdout)

    def job(self, namespace: str, name: str) -> dict[str, Any] | None:
        done = self._kubectl(["get", "job", name, "-n", namespace, "-o", "json", "--ignore-not-found"])
        if done.returncode != 0:
            raise RuntimeError(f"kubectl get job: {done.stderr.strip()[-300:]}")
        return json.loads(done.stdout) if done.stdout.strip() else None


class JobExecutor:
    def __init__(self, runs: Runs, kube: Kube, *, engine_url: str, deadline_seconds: int,
                 watch: float = WATCH, lock_seconds: float | None = None, misses: int = 3):
        self.runs, self.engine, self.kube = runs, runs.engine, kube
        self.engine_url, self.deadline_seconds, self.watch, self.misses = engine_url, deadline_seconds, watch, misses
        self.lock_seconds = lock_seconds if lock_seconds is not None else runs.approvals.lock_seconds
        self.threads: list[threading.Thread] = []

    def start(self, run: Run) -> None:
        thread = threading.Thread(target=self._run, args=(run,), daemon=True, name=f"run {run.id} job")
        self.threads = [t for t in self.threads if t.is_alive()]
        self.threads.append(thread)
        thread.start()

    def wait(self, timeout: float = 10.0) -> None:
        """Every thread, ended (the suite's)."""
        while self.threads:
            self.threads.pop(0).join(timeout)

    def stop(self, run: Run, need: Any, cancel: bool = False) -> None:
        """Nothing to do here: the Job's executor reads Stop and each cancel from the run."""

    def _run(self, run: Run) -> None:
        lock = self.engine.walking(run.repository)
        if not lock.acquire(timeout=self.lock_seconds):
            with self.runs.lock:
                for need in run.needs.values():
                    if need.state == "waiting":
                        self.runs.transition(run, need.name, "blocked", "a walk of that service kept running")
            self.runs.ended_walking(run)
            return
        try:
            try:
                namespace = self._make(run)
            except Exception as e:  # noqa: BLE001 - said on the run: nothing walked
                self.runs.lost(run, f"its Job could not be made ({type(e).__name__}: {str(e)[:300]})")
                return
            self._follow(run, namespace)
        finally:
            lock.release()

    def _make(self, run: Run) -> str:
        namespace, run.commit = self.engine.job_place(run.repository, run.unit)
        if not run.image:
            raise ValueError("the engine names no executor image")
        job = self.kube.create(job_manifest(run, namespace, self.engine_url, self.deadline_seconds))
        self.kube.create(token_manifest(run, namespace, job["metadata"]["uid"]))
        with self.runs.lock:
            self.runs._note(run, {"need": "", "from": run.state, "to": run.state, "by": "engine",  # noqa: SLF001
                                  "result": f"its Job {job_name(run.id)} walks commit {run.commit[:12]} "
                                            f"in {namespace}, image {run.image}"})
        return namespace

    def _follow(self, run: Run, namespace: str) -> None:
        """Until the run ends: the Job looked at every `watch` seconds; failed or gone, the run's
        executor is lost. A Job the API server cannot be asked about `misses` times in a row is lost too."""
        missed = 0
        while True:
            with self.runs.changed:
                if run.ended:
                    return
                self.runs.changed.wait(self.watch)
                if run.ended:
                    return
            try:
                job = self.kube.job(namespace, job_name(run.id))
            except Exception as e:  # noqa: BLE001 - asked again; lost after `misses`
                missed += 1
                if missed >= self.misses:
                    self.runs.lost(run, f"its Job could not be read ({type(e).__name__})")
                    return
                continue
            missed = 0
            why = "its Job is gone" if job is None else ended(job)
            if why:  # an exit posted just before the Job ended has ended the run: lost does nothing then
                self.runs.lost(run, why)
                return


def factory(kube: Kube, **kwargs: Any) -> Callable[[Runs], JobExecutor]:
    return lambda runs: JobExecutor(runs, kube, **kwargs)
