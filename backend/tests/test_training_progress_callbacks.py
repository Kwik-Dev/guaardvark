"""The text and vision trainers report progress through one helper,
finetune_model.progress_callbacks, so a vision job's bar moves like a text
job's. transformers is needed for the callback class; nothing trains."""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "services" / "training" / "scripts"


@pytest.fixture
def progress_callbacks(monkeypatch):
    pytest.importorskip("transformers")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("finetune_model", None)
    from finetune_model import progress_callbacks
    return progress_callbacks


def test_no_callback_means_no_trainer_callbacks(progress_callbacks):
    assert progress_callbacks(None, 10) == []


def test_a_log_line_reports_step_total_and_loss(progress_callbacks):
    seen = []
    (callback,) = progress_callbacks(lambda *args: seen.append(args), 10)

    callback.on_log(None, SimpleNamespace(global_step=3, epoch=0.5), None,
                    logs={"loss": 1.25, "learning_rate": 2e-4})

    step, total, loss, metrics = seen[-1]
    assert (step, total, loss) == (3, 10, 1.25)
    assert metrics == {"loss": 1.25, "learning_rate": 2e-4, "epoch": 0.5}


def test_the_vision_trainer_hands_its_callback_to_the_trainer():
    tree = ast.parse((SCRIPTS / "finetune_vision.py").read_text())
    trainer_calls = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SFTTrainer"]
    (call,) = trainer_calls
    callbacks = next(kw.value for kw in call.keywords if kw.arg == "callbacks")
    assert "progress_callbacks(progress_callback, max_steps)" in ast.unparse(callbacks)
