"""Settings > Training libraries: Unsloth, TRL and Datasets install into the
backend's Python only from the modal's click, hold every other installed
package where it is, leave out what the project removes or must not import,
come out again on Remove, and training jobs refuse while they are missing.

Seams: subprocess.Popen (pip), read by _pip at call time; _spawn (the
background thread), installed_versions, _requires, hardware_fit and
_record_path on the service module; importlib.metadata.version for the job
refusal. Nothing runs pip, reads this machine's hardware or touches the network.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.services import training_libraries as tl

PRACTICAL = {"practical": True, "reason": "NVIDIA GPU with 16 GB of memory.",
             "gpu": "test card", "vram_mb": 16311, "ram_gb": 64}
NOT_PRACTICAL = {"practical": False, "reason": "No GPU was found. Guaardvark's trainer needs an NVIDIA GPU.",
                 "gpu": None, "vram_mb": None, "ram_gb": 8}

# A stock environment as far as these tests care.
STOCK = {"torch": "2.6.0+cu124", "torchvision": "0.21.0+cu124", "transformers": "5.16.1",
         "peft": "0.20.0", "numpy": "1.26.4", "accelerate": "1.2.1", "fsspec": "2026.7.0",
         "typeguard": "2.13.3", "pip": "25.0.1", "legacy-thing": "not-a-version"}

# What a successful install leaves, on top of STOCK.
INSTALLED = {"unsloth": "2026.10.1", "unsloth-zoo": "2026.10.1", "trl": "1.13.0",
             "datasets": "4.8.5", "bitsandbytes": "0.50.2", "pyarrow": "25.0.1",
             "accelerate": "1.15.0", "fsspec": "2026.2.0", "typeguard": "4.6.0"}


class FakePip:
    """subprocess.Popen for `python -m pip ...`: records each call, prints the
    given lines, and applies what the call would do to `env`."""

    def __init__(self, env, outcomes=None, lines=None):
        self.env = env
        self.calls = []
        self.outcomes = list(outcomes or [])
        self.lines = list(lines or [])

    def __call__(self, cmd, **kwargs):
        args = cmd[3:]
        call = {"cmd": cmd, "args": args, "env": kwargs.get("env") or {}}
        for i, arg in enumerate(args):
            if arg == "-c":
                call.setdefault("constraints", []).append(Path(args[i + 1]).read_text())
        self.calls.append(call)
        code = self.outcomes.pop(0) if self.outcomes else 0
        if code == 0:
            if args[0] == "install" and "--no-deps" not in args:
                self.env.update({k: v for k, v in INSTALLED.items() if k not in ("unsloth", "unsloth-zoo")})
            elif args[0] == "install" and any(a.startswith("unsloth==") for a in args):
                self.env.update({"unsloth": INSTALLED["unsloth"], "unsloth-zoo": INSTALLED["unsloth-zoo"]})
            elif args[0] == "install":
                for spec in (a for a in args if "==" in a):
                    name, version = spec.split("==")
                    self.env[name] = version
            elif args[0] == "uninstall":
                for name in args[2:]:
                    self.env.pop(name, None)
        lines = [line + "\n" for line in self.lines]
        return SimpleNamespace(stdout=iter(lines), wait=lambda: code, kill=lambda: None, pid=1)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A fresh service state over a fake environment; returns its versions."""
    versions = dict(STOCK)
    monkeypatch.setattr(tl, "installed_versions", lambda: dict(versions))
    monkeypatch.setattr(tl, "_requires", lambda name: [])
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(PRACTICAL))
    monkeypatch.setattr(tl, "_record_path", lambda: tmp_path / "record.json")
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: None)
    monkeypatch.setattr(tl, "_tokens", {})
    monkeypatch.setattr(tl, "_run", tl._idle_run())
    return versions


@pytest.fixture
def pip(env, monkeypatch):
    fake = FakePip(env)
    monkeypatch.setattr(tl.subprocess, "Popen", fake)
    return fake


# ---- what the modal lists ------------------------------------------------------------

def test_a_stock_install_lists_the_three_libraries_missing_with_sizes(env):
    status = tl.status()

    assert [(lib["id"], lib["version"], lib["state"]) for lib in status["libraries"]] == [
        ("unsloth", "2026.10.1", "missing"), ("trl", "1.13.0", "missing"), ("datasets", "4.8.5", "missing")]
    assert status["ready"] is False
    assert status["download_mb"] == pytest.approx(78 + 1.4 + 51)
    assert {c["name"] for c in status["changes"]} == {"accelerate", "fsspec", "typeguard"}
    assert {l["name"] for l in status["left_out"]} == {"xformers", "torchao"}
    assert status["removal"] == {"remove": [], "restore": {}}
    assert "plan_token" not in status


def test_the_plan_token_comes_only_when_asked_for(env):
    assert tl.status(with_plan_token=True)["plan_token"]


def test_installed_libraries_read_ready_and_other_versions_are_named(env):
    env.update(INSTALLED)
    env["trl"] = "1.12.0"
    status = tl.status()

    states = {lib["id"]: (lib["state"], lib["installed_version"]) for lib in status["libraries"]}
    assert states == {"unsloth": ("installed", "2026.10.1"), "trl": ("other_version", "1.12.0"),
                      "datasets": ("installed", "4.8.5")}
    assert status["ready"] is True
    assert status["download_mb"] == 0
    assert status["changes"] == []


def test_unmet_requirements_name_the_package_and_skip_what_is_left_out(env, monkeypatch):
    env.update(INSTALLED)
    env["accelerate"] = "1.2.1"
    env.pop("bitsandbytes")
    declared = {
        "unsloth": ["xformers>=0.0.27.post2", "torchao>=0.13.0", "bitsandbytes>=0.45.5",
                    "pytest; extra == 'dev'", "mlx==0.32.3; sys_platform == 'darwin' and platform_machine == 'arm64'"],
        "trl": ["accelerate>=1.4.0", "datasets>=4.7.0"],
    }
    monkeypatch.setattr(tl, "_requires", lambda name: declared.get(name, []))

    unmet = tl.unmet_requirements(env)

    assert unmet == ["unsloth 2026.10.1 needs bitsandbytes, which is not installed",
                     "trl 1.13.0 needs accelerate>=1.4.0 (1.2.1 installed)"]


# ---- only the click installs -------------------------------------------------------------

def test_install_refuses_without_a_plan_token(env, pip):
    for token in (None, "", "made-up", 42):
        with pytest.raises(tl.Refused) as refused:
            tl.start_install(token)
        assert refused.value.status == 403
        assert refused.value.code == "NEEDS_CLICK"
    assert pip.calls == []
    assert tl._run["state"] == "idle"


def test_a_plan_token_starts_one_run_only(env, monkeypatch):
    started = []
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: started.append(name))
    token = tl.issue_plan_token()

    tl.start_install(token)
    assert started == ["training-libraries-install"]
    assert tl._run["state"] == "running"

    tl._run["state"] = "completed"
    with pytest.raises(tl.Refused) as refused:
        tl.start_install(token)
    assert refused.value.code == "NEEDS_CLICK"
    assert started == ["training-libraries-install"]


def test_an_expired_plan_token_is_refused(env, monkeypatch):
    token = tl.issue_plan_token()
    tl._tokens[token] = 0
    with pytest.raises(tl.Refused):
        tl.start_install(token)


def test_a_second_run_is_refused_while_one_is_running(env):
    tl.start_install(tl.issue_plan_token())
    with pytest.raises(tl.Refused) as refused:
        tl.start_install(tl.issue_plan_token())
    assert refused.value.code == "BUSY"


def test_a_machine_that_is_not_practical_installs_only_anyway(env, monkeypatch):
    started = []
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(NOT_PRACTICAL))
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: started.append(args))

    with pytest.raises(tl.Refused) as refused:
        tl.start_install(tl.issue_plan_token())
    assert refused.value.code == "NOT_PRACTICAL"
    assert "No GPU was found" in str(refused.value)
    assert started == []

    tl.start_install(tl.issue_plan_token(), anyway=True)
    assert started == [(True,)]


def test_nothing_but_the_routes_starts_a_run():
    """No task, schedule or startup step can install: the only code that
    calls start_install or start_remove is the route behind the modal."""
    backend = Path(tl.__file__).resolve().parents[1]
    skip = {"tests", "venv", ".venv", "node_modules", "__pycache__"}
    callers = set()
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            if "start_install(" in text or "start_remove(" in text:
                callers.add(path.relative_to(backend).as_posix())
    assert callers == {"api/training/routes.py", "services/training_libraries.py"}


# ---- the install ---------------------------------------------------------------------

def test_install_holds_installed_packages_and_leaves_out_xformers_and_torchao(env, pip):
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "completed", tl._run["error"]
    assert tl._run["restart_needed"] is True
    first, second = pip.calls
    assert first["cmd"][:3] == [sys.executable, "-m", "pip"]
    assert first["args"][0] == "install" and "--no-deps" not in first["args"]
    assert "trl==1.13.0" in first["args"] and "datasets==4.8.5" in first["args"]
    assert set(tl.UNSLOTH_REQUIREMENTS) <= set(first["args"])
    hold = first["constraints"][-1].splitlines()
    for held in ("torch==2.6.0+cu124", "transformers==5.16.1", "peft==0.20.0", "numpy==1.26.4", "pip==25.0.1"):
        assert held in hold
    for free in ("accelerate", "fsspec", "typeguard", "unsloth", "trl", "datasets", "legacy-thing"):
        assert not any(line.startswith(f"{free}==") for line in hold), free
    assert second["args"][:1] == ["install"] and "--no-deps" in second["args"]
    assert {"unsloth==2026.10.1", "unsloth-zoo==2026.10.1"} <= set(second["args"])
    for call in pip.calls:
        assert not any("xformers" in a or "torchao" in a for a in call["args"])
        assert "PIP_CONSTRAINT" not in call["env"]


def test_install_records_what_it_added_and_moved(env, pip, tmp_path):
    tl._claim("install", 130)
    tl._install(False)

    record = json.loads((tmp_path / "record.json").read_text())
    assert {"unsloth", "unsloth-zoo", "trl", "datasets", "bitsandbytes", "pyarrow"} <= set(record["added"])
    assert record["changed"]["accelerate"] == {"from": "1.2.1", "to": "1.15.0"}
    assert record["changed"]["fsspec"] == {"from": "2026.7.0", "to": "2026.2.0"}
    assert "torch" not in record["changed"]


def test_a_failed_pip_pass_fails_the_run_and_goes_no_further(env, monkeypatch):
    fake = FakePip(env, outcomes=[1], lines=["ERROR: ResolutionImpossible: torch==2.6.0+cu124 is held"])
    monkeypatch.setattr(tl.subprocess, "Popen", fake)
    tl._claim("install", 130)
    tl._install(False)

    assert len(fake.calls) == 1
    assert tl._run["state"] == "failed"
    assert "exit 1" in tl._run["error"]
    assert "ERROR: ResolutionImpossible: torch==2.6.0+cu124 is held" in tl._run["log"]


def test_an_install_that_leaves_requirements_unmet_is_failed(env, pip, monkeypatch):
    monkeypatch.setattr(tl, "_requires", lambda name: ["accelerate>=9"] if name == "trl" else [])
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "failed"
    assert "trl 1.13.0 needs accelerate>=9" in tl._run["error"]


def test_progress_follows_the_megabytes_pip_fetches(env):
    tl._claim("install", 100)
    tl._on_pip_line("Downloading pyarrow-25.0.1-cp312-cp312-manylinux_2_28_x86_64.whl (50.1 MB)", fetching=True)
    assert tl._run["progress"] == 5 + int(80 * 0.501)
    tl._on_pip_line("Using cached trl-1.13.0-py3-none-any.whl (1010 kB)", fetching=True)
    assert tl._run["fetched_mb"] == pytest.approx(51.11)
    tl._on_pip_line("Installing collected packages: pyarrow, trl", fetching=True)
    assert tl._run["progress"] == 85
    assert tl._run["phase"] == "Installing collected packages"


# ---- remove --------------------------------------------------------------------------

def _record(tmp_path, added, changed):
    (tmp_path / "record.json").write_text(json.dumps({"added": added, "changed": changed}))


def test_remove_uninstalls_what_the_install_added_and_puts_back_moved_versions(env, pip, tmp_path):
    env.update(INSTALLED)
    _record(tmp_path, {"unsloth": "2026.10.1", "bitsandbytes": "0.50.2", "pyarrow": "25.0.1"},
            {"accelerate": {"from": "1.2.1", "to": "1.15.0"}, "fsspec": {"from": "2026.7.0", "to": "2026.2.0"}})
    tl._claim("remove", 1)
    tl._remove()

    assert tl._run["state"] == "completed", tl._run["error"]
    uninstall, restore = pip.calls
    assert uninstall["args"][:2] == ["uninstall", "-y"]
    assert set(uninstall["args"][2:]) == {"unsloth", "unsloth-zoo", "trl", "datasets", "bitsandbytes", "pyarrow"}
    assert "--no-deps" in restore["args"]
    assert {"accelerate==1.2.1", "fsspec==2026.7.0"} <= set(restore["args"])
    assert env["accelerate"] == "1.2.1"
    assert not (tmp_path / "record.json").exists()


def test_remove_leaves_alone_a_package_moved_again_since(env, tmp_path):
    env.update(INSTALLED)
    env["accelerate"] = "1.16.0"
    _record(tmp_path, {}, {"accelerate": {"from": "1.2.1", "to": "1.15.0"}})

    assert tl.removal_plan()["restore"] == {}


def test_remove_without_a_record_takes_out_only_the_libraries(env):
    env.update(INSTALLED)
    assert tl.removal_plan()["remove"] == ["datasets", "trl", "unsloth", "unsloth-zoo"]


def test_remove_is_refused_when_nothing_is_installed(env):
    with pytest.raises(tl.Refused) as refused:
        tl.start_remove(tl.issue_plan_token())
    assert refused.value.code == "NOTHING_TO_REMOVE"


# ---- jobs refuse while the libraries are missing -------------------------------------

def _missing(*names):
    def version(name):
        if name in names:
            raise tl.metadata.PackageNotFoundError(name)
        return "1.0"
    return version


def test_the_job_refusal_names_what_is_missing_and_where_to_install_it(env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth", "trl", "datasets"))
    reason = tl.unavailable_reason()
    assert reason == ("Training needs unsloth, trl and datasets, which are not installed. "
                      "Install them in Settings > Training libraries.")

    monkeypatch.setattr(tl.metadata, "version", _missing("trl"))
    assert tl.unavailable_reason() == ("Training needs trl, which is not installed. "
                                       "Install it in Settings > Training libraries.")

    monkeypatch.setattr(tl.metadata, "version", _missing())
    assert tl.unavailable_reason() is None


def test_jobs_wait_while_an_install_runs(env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing())
    tl._claim("install", 130)
    assert "Settings > Training libraries" in tl.unavailable_reason()


# ---- the routes ----------------------------------------------------------------------

@pytest.fixture
def client(env):
    from backend.api.training.routes import training_bp

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _error(response):
    return response.get_json()["error"]


def test_the_status_route_hands_out_a_token_only_with_plan(client):
    plain = client.get("/api/training/libraries").get_json()["data"]
    planned = client.get("/api/training/libraries?plan=1").get_json()["data"]
    assert "plan_token" not in plain
    assert planned["plan_token"] in tl._tokens
    assert [lib["state"] for lib in planned["libraries"]] == ["missing", "missing", "missing"]


def test_the_install_route_needs_the_confirm_and_the_token(client):
    no_confirm = client.post("/api/training/libraries/install", json={})
    assert no_confirm.status_code == 403 and _error(no_confirm)["code"] == "NEEDS_CLICK"

    no_token = client.post("/api/training/libraries/install", json={"confirm": "install"})
    assert no_token.status_code == 403 and _error(no_token)["code"] == "NEEDS_CLICK"
    assert tl._run["state"] == "idle"

    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/libraries/install", json={"confirm": "install", "plan_token": token})
    assert started.status_code == 202
    assert started.get_json()["data"]["run"]["state"] == "running"


def test_the_install_route_passes_anyway_only_when_true(client, monkeypatch):
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(NOT_PRACTICAL))
    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    refused = client.post("/api/training/libraries/install",
                          json={"confirm": "install", "plan_token": token, "anyway": "yes"})
    assert refused.status_code == 409 and _error(refused)["code"] == "NOT_PRACTICAL"

    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/libraries/install",
                          json={"confirm": "install", "plan_token": token, "anyway": True})
    assert started.status_code == 202


def test_install_and_remove_wait_for_a_running_training_job(client):
    db.session.add(TrainingJob(job_id="busy", name="busy", base_model="org/base", status="running"))
    db.session.commit()
    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    for path, confirm in (("install", "install"), ("remove", "remove")):
        response = client.post(f"/api/training/libraries/{path}", json={"confirm": confirm, "plan_token": token})
        assert response.status_code == 409 and _error(response)["code"] == "TRAINING_RUNNING"
    assert tl._run["state"] == "idle"
    assert token in tl._tokens


def test_job_creation_refuses_while_the_libraries_are_missing(client, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth", "trl", "datasets"))
    response = client.post("/api/training/jobs", json={
        "name": "job", "base_model": "org/base", "dataset_id": 1, "start_immediately": True})

    assert response.status_code == 409
    assert _error(response)["code"] == "TRAINING_LIBRARIES_MISSING"
    assert "Settings > Training libraries" in _error(response)["message"]
    assert db.session.query(TrainingJob).count() == 0


def test_resume_refuses_while_the_libraries_are_missing(client, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth"))
    job = TrainingJob(job_id="old", name="old", base_model="org/base", status="failed",
                      pipeline_stage="training", is_resumable=True, checkpoint_path="/ckpt/checkpoint-10")
    db.session.add(job)
    db.session.commit()

    response = client.post(f"/api/training/jobs/{job.id}/resume")

    assert response.status_code == 409
    assert "Settings > Training libraries" in _error(response)["message"]
    db.session.expire_all()
    assert db.session.get(TrainingJob, job.id).status == "failed"


def test_other_hosts_cannot_install_or_remove():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    for path in ("/api/training/libraries/install", "/api/training/libraries/remove"):
        with app.test_request_context(path, method="POST"):
            assert _is_protected() is True, path
    with app.test_request_context("/api/training/libraries", method="GET"):
        assert _is_protected() is False
