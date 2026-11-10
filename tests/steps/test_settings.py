"""Steps for state.json, the settings, and `python -m convergence services`."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.__main__ import main
from convergence.config import settings
from convergence.state import services

scenarios("../features/settings.feature")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ctx() -> dict:
    return {"env": {}}


@given(parsers.parse("a state file '{text}'"))
def state_file(ctx: dict, text: str) -> None:
    ctx["text"] = text


@given(parsers.parse("a state file '{text}' on disk"))
def state_on_disk(ctx: dict, text: str, tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(text)
    ctx["path"] = str(tmp_path / "state.json")


@when("I list its services")
def list_services(ctx: dict) -> None:
    try:
        ctx["services"] = services(ctx["text"])
    except ValueError as e:
        ctx["error"] = str(e)


@then(parsers.parse('the services are "{names}"'))
def services_are(ctx: dict, names: str) -> None:
    assert ctx["services"] == [n.strip() for n in names.split(",")]


@then(parsers.parse('it is refused with "{message}"'))
def refused(ctx: dict, message: str) -> None:
    assert ctx["error"] == message


@given("no settings")
def no_settings(ctx: dict) -> None:
    ctx["env"] = {}


@given(parsers.parse('the setting {name} is "{value}"'))
def setting(ctx: dict, name: str, value: str) -> None:
    ctx["env"][name] = value


@then(parsers.parse('the state repositories are "{names}"'))
def states_are(ctx: dict, names: str) -> None:
    assert settings(ctx["env"]).states == tuple(n.strip() for n in names.split(","))


@then(parsers.parse('{owner}\'s zones are in "{slug}"'))
def zones_in(ctx: dict, owner: str, slug: str) -> None:
    assert settings(ctx["env"]).zones[owner] == slug


@then(parsers.parse("a round comes every {seconds:d} seconds"))
def every(ctx: dict, seconds: int) -> None:
    assert settings(ctx["env"]).every_seconds == seconds


@then("there is no Bitwarden token file")
def no_bws(ctx: dict) -> None:
    assert settings(ctx["env"]).bitwarden_token is None


@then(parsers.parse('the Bitwarden token file is "{path}"'))
def bws_file(ctx: dict, path: str) -> None:
    assert settings(ctx["env"]).bitwarden_token == Path(path)


@then(parsers.parse('the settings are refused with "{message}"'))
def settings_refused(ctx: dict, message: str) -> None:
    with pytest.raises(ValueError) as e:
        settings(ctx["env"])
    assert str(e.value) == message


@when(parsers.re(r'I run convergence "(?P<command>[a-z-]*)" on (?P<what>it with the checkouts|it|nothing)$'))
def run_cli(ctx: dict, command: str, what: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    args = [command, ctx["path"]] if what == "it" else []
    if what == "it with the checkouts":
        args = [command, ctx["path"], str(ctx.get("checkouts", Path(ctx["path"]).parent / "none"))]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        ctx["code"] = main(args)
    ctx["out"], ctx["err"] = out.getvalue(), err.getvalue()


@then(parsers.parse('it succeeds and prints "{text}"'))
def succeeds(ctx: dict, text: str) -> None:
    assert ctx["code"] == 0 and ctx["out"].strip() == text


@then(parsers.parse("it fails with {code:d}"))
def fails(ctx: dict, code: int) -> None:
    assert ctx["code"] == code and ctx["err"]


def test_the_pod_module_builds_nothing_without_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.delenv("CONVERGENCE_SERVE", raising=False)
    import convergence.main

    assert importlib.reload(convergence.main).app is None


@given(parsers.parse('a checkout of {name} whose deploy block has the namespace "{ns}" and a unit in "{unit}"'))
def checkout(ctx: dict, name: str, ns: str, unit: str, tmp_path: Path) -> None:
    (tmp_path / "co" / name).mkdir(parents=True)
    (tmp_path / "co" / name / "pubkit.json").write_text(json.dumps(
        {"deploy": {"namespace": ns, "units": {"eu": {"namespace": unit}, "odd": "x"}}}))
    ctx["checkouts"] = tmp_path / "co"


@then(parsers.parse('it succeeds and binds the namespaces "{names}"'))
def binds(ctx: dict, names: str) -> None:
    assert ctx["code"] == 0
    found = re.findall(r"^  namespace: (\S+)$", ctx["out"], re.M)
    assert found == [n.strip() for n in names.split(",")]
    assert ctx["out"].count("kind: RoleBinding") == len(found)


@given("an image lock with a GitHub release wheel and a PyPI package")
def image_lock(ctx: dict) -> None:
    ctx["lock"] = ("pkg @ https://github.com/O/pkg/releases/download/v1.0/pkg-1.0-py3-none-any.whl \\\n"
                   f"    --hash=sha256:{'c' * 64}\n    # via -r requirements.in\n"
                   f"fastapi==1.0 \\\n    --hash=sha256:{'d' * 64}\n")


@when("it is pointed at the mirrors")
def pointed(ctx: dict) -> None:
    spec = importlib.util.spec_from_file_location("mirrored_lock", ROOT / "scripts" / "mirrored-lock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx["mirrored"] = module.mirrored(ctx["lock"])


@then(parsers.parse('the wheel comes from "{url}"'))
def wheel_from(ctx: dict, url: str) -> None:
    assert ctx["mirrored"].splitlines()[0] == f"pkg @ {url.replace('<its sha256>', 'c' * 64)} \\"


@then("the PyPI package and every hash are as they were")
def kept(ctx: dict) -> None:
    assert ctx["mirrored"].splitlines()[1:] == ctx["lock"].splitlines()[1:]
