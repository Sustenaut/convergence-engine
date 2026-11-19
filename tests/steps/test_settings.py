"""Steps for state.json, the settings, and `python -m convergence services`."""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import shlex
import sys
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, when

from convergence.__main__ import main
from convergence.config import settings
from convergence.state import services

scenarios("../features/settings.feature")

ROOT = Path(__file__).resolve().parents[2]


@given(parsers.parse("a state file '{text}'"))
def state_file(ctx, text: str) -> None:
    ctx.state_text = text


@when("I list its services")
def list_services(ctx) -> None:
    ctx.attempt(lambda: services(ctx.state_text))


@given("no settings")
def no_settings(ctx) -> None:
    ctx.settings_env = {}


@given(parsers.parse('the setting {name} is "{value}"'))
def setting(ctx, name: str, value: str) -> None:
    ctx.settings_env = {**getattr(ctx, "settings_env", {}), name: value}


def _plain(found) -> dict:
    """The settings as JSON reads them: tuples are lists, paths their text."""
    return json.loads(json.dumps(dataclasses.asdict(found), default=str))


@when("the settings are read")
def settings_read(ctx) -> None:
    ctx.attempt(lambda: _plain(settings(getattr(ctx, "settings_env", {}))))


def _exits(args: list[str]) -> None:
    sys.exit(main(args))


@when(parsers.parse('convergence runs "{command}"'))
def convergence_runs(ctx, command: str) -> None:
    ctx.attempt(lambda: _exits(shlex.split(command)))


@when("convergence runs with no command")
def convergence_runs_bare(ctx) -> None:
    ctx.attempt(lambda: _exits([]))


@when("the pod module is loaded")
def pod_module(ctx) -> None:
    import convergence.main

    ctx.attempt(lambda: importlib.reload(convergence.main).app)


@given("an image lock with a GitHub release wheel and a PyPI package")
def image_lock(ctx) -> None:
    ctx.lock = ("pkg @ https://github.com/O/pkg/releases/download/v1.0/pkg-1.0-py3-none-any.whl \\\n"
                f"    --hash=sha256:{'c' * 64}\n    # via -r requirements.in\n"
                f"fastapi==1.0 \\\n    --hash=sha256:{'d' * 64}\n")


@when("it is pointed at the mirrors")
def pointed(ctx) -> None:
    spec = importlib.util.spec_from_file_location("mirrored_lock", ROOT / "scripts" / "mirrored-lock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx.attempt(lambda: module.mirrored(ctx.lock))
