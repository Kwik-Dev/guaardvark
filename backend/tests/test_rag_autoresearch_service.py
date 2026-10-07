"""Tests for the RAG Autoresearch orchestrator."""
import pytest
import time
from unittest.mock import patch, MagicMock

try:
    from flask import Flask
    from backend.models import db
    from backend.services.rag_autoresearch_service import RAGAutoresearchService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class TestExperimentCycle:
    def test_single_experiment_keep(self, app):
        """A winning experiment updates the config."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config") as mock_save, \
                 patch.object(svc, "_log_experiment") as mock_log:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 8,
                    "hypothesis": "try more chunks",
                }
                mock_eval.return_value = {"composite_score": 3.5, "num_pairs": 10, "details": []}

                result = svc.run_single_experiment()
                assert result["status"] == "keep"
                assert result["delta"] == 0.5
                mock_save.assert_called_once()

    def test_single_experiment_discard(self, app):
        """A losing experiment reverts the config — promote is NOT called."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config") as mock_save, \
                 patch.object(svc, "_log_experiment") as mock_log, \
                 patch.object(svc, "_promote_config") as mock_promote:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 2,
                    "hypothesis": "try fewer chunks",
                }
                mock_eval.return_value = {"composite_score": 2.5, "num_pairs": 10, "details": []}

                result = svc.run_single_experiment()
                assert result["status"] == "discard"
                assert result["delta"] == -0.5
                mock_promote.assert_not_called()

    def test_tiny_positive_delta_is_discard(self, app):
        """Keep bar matches confirmation — 0.01 of judge jitter is not a keep."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc.eval_harness, "run_retrieval_eval",
                              return_value={"num_scored": 0}), \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"), \
                 patch.object(svc, "_promote_config") as mock_promote:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                    "phase_plateau_count": 0,
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 6, "hypothesis": "nudge",
                }
                mock_eval.return_value = {
                    "composite_score": 3.01, "num_pairs": 10, "details": [],
                    "parse_fail_crash": False,
                }
                result = svc.run_single_experiment()
                assert result["status"] == "discard"
                mock_promote.assert_not_called()

    def test_f0_discard_skips_judge(self, app):
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment",
                              return_value={"parameter": "top_k", "new_value": 2,
                                            "hypothesis": "t", "source": "tpe"}), \
                 patch.object(svc.eval_harness, "run_retrieval_eval") as mock_retr, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config", return_value={
                     "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                     "phase_plateau_count": 0,
                 }), \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"):
                mock_retr.side_effect = [
                    {"num_scored": 5, "mrr": 0.8, "hit_rate_at_k": 0.9, "num_pairs": 5},
                    {"num_scored": 5, "mrr": 0.2, "hit_rate_at_k": 0.3, "num_pairs": 5},
                ]
                result = svc.run_single_experiment()
                assert result["status"] == "discard"
                assert result.get("fidelity") == 0
                mock_eval.assert_not_called()


class TestIdleDetection:
    def test_is_idle_returns_true_after_threshold(self):
        """System is idle when last activity exceeds threshold."""
        svc = RAGAutoresearchService()
        svc._last_activity = time.time() - 700  # 11+ minutes ago
        assert svc.is_idle(idle_minutes=10) is True

    def test_is_idle_returns_false_during_activity(self):
        """System is not idle when recently active."""
        svc = RAGAutoresearchService()
        svc._last_activity = time.time() - 60  # 1 minute ago
        assert svc.is_idle(idle_minutes=10) is False


class TestPause:
    def test_pause_stops_loop(self):
        """Pause flag prevents next experiment from starting."""
        svc = RAGAutoresearchService()
        svc.pause()
        assert svc._paused is True

    def test_resume_clears_pause(self):
        svc = RAGAutoresearchService()
        svc.pause()
        svc.resume()
        assert svc._paused is False


class TestExperimentDeadline:
    def test_deadline_scales_with_measured_pair_cost(self, app):
        from backend.config import AUTORESEARCH_MAX_EXPERIMENT_DURATION, AUTORESEARCH_EXPERIMENT_DEADLINE_HEADROOM
        with app.app_context():
            svc = RAGAutoresearchService()
            from backend.config import AUTORESEARCH_EXPERIMENT_DEADLINE_UNMEASURED
            # unmeasured: never the bare floor (three crashes at calls=0, 2026-08-30)
            assert svc._experiment_deadline_seconds({}) == max(
                AUTORESEARCH_MAX_EXPERIMENT_DURATION, AUTORESEARCH_EXPERIMENT_DEADLINE_UNMEASURED)
            svc.eval_harness.avg_pair_seconds = 33.0  # gemma4 12B, 2026-08-30
            with patch.object(svc.eval_harness, "_get_active_eval_pairs", return_value=[{}] * 18):
                assert svc._experiment_deadline_seconds({}) == AUTORESEARCH_EXPERIMENT_DEADLINE_HEADROOM * 18 * 33.0
            svc.eval_harness.avg_pair_seconds = 0.5  # fast model: floor wins
            with patch.object(svc.eval_harness, "_get_active_eval_pairs", return_value=[{}] * 18):
                assert svc._experiment_deadline_seconds({}) == AUTORESEARCH_MAX_EXPERIMENT_DURATION

    def test_f2_is_skipped_not_crashed_when_budget_is_gone(self, app):
        """A winning F1 subset with no budget left keeps its verdict instead of raising on F2."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc.eval_harness, "_select_judge_subset", return_value=[{}] * 5), \
                 patch.object(svc.eval_harness, "_get_active_eval_pairs", return_value=[{}] * 18), \
                 patch.object(svc.eval_harness, "_budget_ok", return_value=False), \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"):
                mock_load.return_value = {"params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1}
                mock_propose.return_value = {"parameter": "top_k", "new_value": 8, "hypothesis": "more"}
                mock_eval.return_value = {"composite_score": 3.5, "num_pairs": 5, "details": []}
                result = svc.run_single_experiment()
        assert result["status"] != "crash"
        assert mock_eval.call_count == 1  # F1 only
        assert result["fidelity"] == 1


class TestPhaseClamp:
    def test_unknown_persisted_phase_is_clamped_and_saved(self, app):
        from backend.services.rag_experiment_agent import MAX_PHASE
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config") as mock_save, \
                 patch.object(svc, "_log_experiment"):
                mock_load.return_value = {"params": {"top_k": 5}, "baseline_score": 3.0,
                                          "phase": 3, "phase_plateau_count": 387483}
                mock_propose.return_value = {"parameter": "top_k", "new_value": 8, "hypothesis": "more"}
                mock_eval.return_value = {"composite_score": 2.5, "num_pairs": 10, "details": []}
                svc.run_single_experiment()
            assert mock_propose.call_args[0][2] == MAX_PHASE
            saved = mock_save.call_args_list[0][0][0]
            assert saved["phase"] == MAX_PHASE and saved["phase_plateau_count"] <= 1  # was 387483


class TestLedgerProvenance:
    """The judge is resolved during the eval, so it is recorded after it."""

    def _run(self, svc, eval_side_effect):
        with patch.object(svc.agent, "propose_experiment", return_value={
                 "parameter": "top_k", "new_value": 8, "hypothesis": "t", "source": "tpe"}), \
             patch.object(svc.eval_harness, "run_retrieval_eval", return_value={"num_scored": 0}), \
             patch.object(svc.eval_harness, "_get_active_eval_pairs", return_value=[{}] * 11), \
             patch.object(svc.eval_harness, "run_full_eval", side_effect=eval_side_effect), \
             patch.object(svc, "_load_config", return_value={
                 "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                 "phase_plateau_count": 0}), \
             patch.object(svc, "_save_config"), \
             patch.object(svc, "_log_experiment") as log:
            result = svc.run_single_experiment()
        return result, log.call_args[0][0]

    def _resolve_models(self, svc):
        svc.eval_harness.judge_model_name = "qwen3:14b"
        svc.eval_harness.answer_model_name = "gemma4:12b"

    def test_judge_and_answer_model_recorded_after_the_eval(self, app):
        with app.app_context():
            svc = RAGAutoresearchService()
            assert svc.eval_harness.judge_model_name is None

            def evaluate(*a, **k):
                self._resolve_models(svc)
                return {"composite_score": 2.9, "num_pairs": 11, "judged_pairs": 10,
                        "details": [{}], "parse_fail_crash": False}
            result, logged = self._run(svc, evaluate)
        assert result["judge_model"] == logged["judge_model"] == "qwen3:14b"
        metrics = logged["retrieval_metrics"]
        assert metrics["answer_model"] == "gemma4:12b"
        assert metrics["fidelity"] == 1
        assert metrics["judged_pairs"] == 10
        assert metrics["active_pairs"] == 11

    def test_on_proposal_hears_the_change_before_it_is_measured(self, app):
        with app.app_context():
            svc = RAGAutoresearchService()
            heard = []

            def evaluate(*a, **k):
                assert heard, "on_proposal must run before the eval"
                return {"composite_score": 2.9, "num_pairs": 11, "judged_pairs": 11,
                        "details": [{}], "parse_fail_crash": False}
            with patch.object(svc.agent, "propose_experiment", return_value={
                     "parameter": "top_k", "new_value": 8, "hypothesis": "t", "source": "tpe"}), \
                 patch.object(svc.eval_harness, "run_retrieval_eval", return_value={"num_scored": 0}), \
                 patch.object(svc.eval_harness, "run_full_eval", side_effect=evaluate), \
                 patch.object(svc, "_load_config", return_value={
                     "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                     "phase_plateau_count": 0}), \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"):
                svc.run_single_experiment(on_proposal=heard.append)
        assert heard[0]["parameter"] == "top_k" and heard[0]["new_value"] == 8

    def test_judge_recorded_on_a_crash_too(self, app):
        from backend.services.rag_eval_harness import LLMUnavailableError
        with app.app_context():
            svc = RAGAutoresearchService()

            def evaluate(*a, **k):
                self._resolve_models(svc)
                raise LLMUnavailableError("judge went away")
            result, logged = self._run(svc, evaluate)
        assert result["status"] == "crash"
        assert logged["judge_model"] == "qwen3:14b"
        assert logged["retrieval_metrics"]["judged_pairs"] == 0
        assert logged["retrieval_metrics"]["answer_model"] == "gemma4:12b"


class TestCountsLeaveHealthChecksOut:
    def _seed(self):
        from datetime import timedelta
        from backend.models import ExperimentRun, ResearchConfig
        from backend.utils.clock import utcnow
        t = utcnow()
        rows = [
            ("e1", "top_k", "discard", "tpe"),
            ("h1", "pytest_snapshot", "keep", "heal"),     # written before heal rows had their own status
            ("e2", "top_k", "discard", "tpe"),
            ("h2", "pytest_snapshot", "pass", "heal"),
            ("e3", "hybrid_search_alpha", "crash", None),
        ]
        for i, (rid, param, status, source) in enumerate(rows):
            db.session.add(ExperimentRun(
                id=rid, phase=0 if source == "heal" else 1, parameter_changed=param,
                new_value="x", status=status, proposal_source=source,
                composite_score=0.0, created_at=t + timedelta(seconds=i)))
        db.session.add_all([
            ResearchConfig(params={"top_k": 8}, source="local", status="promoted",
                           promoted_at=t, is_active=True),
            ResearchConfig(params={"top_k": 9}, source="local", status="candidate"),
            ResearchConfig(params={"top_k": 7}, source="family_broadcast",
                           status="promoted", promoted_at=t),
        ])
        db.session.commit()

    def test_counts(self, app):
        with app.app_context():
            self._seed()
            svc = RAGAutoresearchService()
            assert svc._count_experiments() == 3
            assert svc._count_health_checks() == 2
            assert svc._count_improvements() == 1

    def test_history_has_no_health_checks(self, app):
        with app.app_context():
            self._seed()
            history = RAGAutoresearchService()._get_recent_history()
            assert [h["id"] for h in history] == ["e1", "e2", "e3"]

    def test_status_reports_the_honest_counts(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        with app.app_context():
            self._seed()
            with patch("backend.utils.llm_service.get_saved_active_model_name",
                       return_value="gemma4:12b"):
                st = RAGAutoresearchService().get_status()
        assert st["total_experiments"] == 3
        assert st["total_health_checks"] == 2
        assert st["total_improvements"] == 1
        assert st["judge"] == {"configured": None, "answer_model": "gemma4:12b",
                               "independent": False, "problem": "judge_unset"}
        assert st["eval_pairs"] == {"active": 0, "not_indexed": 0, "by_status": {}}
        assert st["auto_enabled"] is False
        assert st["last_run"] is None


class TestBaselineIsMeasuredEachRun:
    """A stored baseline may come from another judge or eval set; every run
    scores its own and starts its plateau count from zero."""

    def _config_file(self, tmp_path, monkeypatch, body):
        import json
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        (tmp_path / "data").mkdir(exist_ok=True)
        cfg_file = tmp_path / "data" / "rag_experiment_config.json"
        cfg_file.write_text(json.dumps(body))
        return cfg_file

    def test_measuring_the_baseline_resets_the_plateau_count(self, app, tmp_path, monkeypatch):
        import json
        from backend.services.research_run_service import ResearchRunService
        cfg_file = self._config_file(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 4.94, "phase": 1,
            "phase_plateau_count": 14, "tuned": [], "params": {"top_k": 5},
        })
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.eval_harness, "run_full_eval", return_value={
                     "composite_score": 2.39, "num_pairs": 11, "judged_pairs": 11,
                     "details": [], "parse_fail_crash": False}) as full, \
                 patch.object(svc.eval_harness, "_get_active_eval_pairs",
                              return_value=[{"eval_generation_id": "gen-a"}] * 11), \
                 patch.object(svc.eval_harness, "begin_experiment_budget") as budget:
                measured = ResearchRunService()._measure_baseline(svc, svc._load_config())
            budget.assert_called_once()
            full.assert_called_once()
        saved = json.loads(cfg_file.read_text())
        assert saved["baseline_score"] == 2.39
        assert saved["phase_plateau_count"] == 0
        assert saved["baseline_pairs"] == 11
        assert saved["baseline_eval_generation"] == "gen-a"
        assert saved["baseline_measured_at"] == measured["measured_at"]

    def test_nothing_measured_is_an_error_not_a_zero_baseline(self, app, tmp_path, monkeypatch):
        import json
        from backend.services.research_run_service import ResearchRunService
        cfg_file = self._config_file(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 4.94, "phase": 1,
            "phase_plateau_count": 14, "tuned": [], "params": {"top_k": 5},
        })
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.eval_harness, "run_full_eval", return_value={
                     "composite_score": 0.0, "num_pairs": 0, "details": []}):
                with pytest.raises(RuntimeError, match="no eval pairs"):
                    ResearchRunService()._measure_baseline(svc, svc._load_config())
        assert json.loads(cfg_file.read_text())["baseline_score"] == 4.94

    def test_clear_baseline_forgets_score_and_plateau(self, app, tmp_path, monkeypatch):
        import json
        cfg_file = self._config_file(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 2.39, "phase": 1, "phase_plateau_count": 6,
            "baseline_measured_at": "2026-10-07T05:09:00", "baseline_pairs": 11,
            "tuned": [], "params": {"top_k": 5},
        })
        with app.app_context():
            RAGAutoresearchService().clear_baseline()
        saved = json.loads(cfg_file.read_text())
        assert saved["baseline_score"] == 0.0 and saved["phase_plateau_count"] == 0
        assert "baseline_measured_at" not in saved and "baseline_pairs" not in saved


@pytest.fixture
def nomic(monkeypatch):
    """Active embedding model nomic-embed-text, with no env threshold override."""
    monkeypatch.delenv("GUAARDVARK_CHUNK_SIMILARITY_THRESHOLD", raising=False)
    with patch("backend.config.get_active_embedding_model",
               return_value="nomic-embed-text:latest"):
        yield


@pytest.mark.usefixtures("nomic")
class TestPromotionStoresOnlyTunedParams:
    """A promoted row overrides live retrieval key by key, so it must hold only
    what kept experiments changed, never a copied default."""

    def _keep(self, svc, config, parameter, new_value):
        with patch.object(svc.agent, "propose_experiment", return_value={
                 "parameter": parameter, "new_value": new_value, "hypothesis": "t"}), \
             patch.object(svc.eval_harness, "run_retrieval_eval",
                          return_value={"num_scored": 0}), \
             patch.object(svc.eval_harness, "run_full_eval", return_value={
                 "composite_score": 3.5, "num_pairs": 10, "details": []}), \
             patch.object(svc, "_load_config", return_value=config), \
             patch.object(svc, "_save_config"), \
             patch.object(svc, "_log_experiment"), \
             patch.object(svc, "_emit_socket_event"), \
             patch.object(svc, "_broadcast_to_family"):
            return svc.run_single_experiment()

    def test_top_k_keep_promotes_only_top_k(self, app):
        from backend.models import ResearchConfig
        with app.app_context():
            svc = RAGAutoresearchService()
            config = {"params": svc._baseline_params(), "tuned": [],
                      "baseline_score": 3.0, "phase": 1, "phase_plateau_count": 0}
            result = self._keep(svc, config, "top_k", 8)
            assert result["status"] == "keep"
            row = ResearchConfig.query.filter_by(is_active=True).one()
            assert row.params == {"top_k": 8}
            assert config["tuned"] == ["top_k"]

    def test_earlier_keeps_stay_in_the_promoted_row(self, app):
        from backend.models import ResearchConfig
        with app.app_context():
            svc = RAGAutoresearchService()
            params = svc._baseline_params()
            params["hybrid_search_alpha"] = 0.5
            config = {"params": params, "tuned": ["hybrid_search_alpha"],
                      "baseline_score": 3.0, "phase": 1, "phase_plateau_count": 0}
            self._keep(svc, config, "top_k", 8)
            row = ResearchConfig.query.filter_by(is_active=True).one()
            assert row.params == {"top_k": 8, "hybrid_search_alpha": 0.5}

    def test_tuned_back_to_default_is_not_stored(self, app):
        from backend.models import ResearchConfig
        with app.app_context():
            svc = RAGAutoresearchService()
            config = {"params": svc._baseline_params(), "tuned": ["top_k"]}
            svc._promote_config(config, 3.5, "local")
            row = ResearchConfig.query.filter_by(is_active=True).one()
            assert row.params == {}

    def test_file_without_tuned_record_infers_it(self, app, tmp_path, monkeypatch):
        import json
        from backend.config import AUTORESEARCH_DEFAULT_PARAMS
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        (tmp_path / "data").mkdir()
        cfg_file = tmp_path / "data" / "rag_experiment_config.json"
        cfg_file.write_text(json.dumps({
            "version": 1, "baseline_score": 3.0,
            "params": dict(AUTORESEARCH_DEFAULT_PARAMS, top_k=8),
            "phase": 1, "phase_plateau_count": 0,
        }))
        with app.app_context():
            svc = RAGAutoresearchService()
            assert svc._load_config()["tuned"] == ["top_k"]
            assert json.loads(cfg_file.read_text())["tuned"] == ["top_k"]


@pytest.mark.usefixtures("nomic")
class TestBaselineDedupIsPerModel:
    """Experiments start from the dedup threshold production uses for the
    active embedding model (0.96 for nomic-embed-text), not a global 0.85."""

    def _write(self, tmp_path, monkeypatch, body):
        import json
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        (tmp_path / "data").mkdir()
        cfg_file = tmp_path / "data" / "rag_experiment_config.json"
        cfg_file.write_text(json.dumps(body))
        return cfg_file

    def test_new_config_baseline_carries_the_model_value(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        with app.app_context():
            config = RAGAutoresearchService()._load_config()
        assert config["params"]["dedup_threshold"] == 0.96
        assert config["tuned"] == []

    def test_old_default_is_replaced_and_baseline_remeasured(self, app, tmp_path, monkeypatch):
        import json
        from backend.config import AUTORESEARCH_DEFAULT_PARAMS
        cfg_file = self._write(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 3.0, "phase": 1, "phase_plateau_count": 0,
            "params": dict(AUTORESEARCH_DEFAULT_PARAMS, dedup_threshold=0.85),
        })
        with app.app_context():
            config = RAGAutoresearchService()._load_config()
        assert config["params"]["dedup_threshold"] == 0.96
        assert config["tuned"] == []
        # Measured at 0.85; the next run measures the real baseline.
        assert config["baseline_score"] == 0.0
        assert json.loads(cfg_file.read_text())["params"]["dedup_threshold"] == 0.96

    def test_kept_dedup_experiment_is_not_replaced(self, app, tmp_path, monkeypatch):
        from backend.config import AUTORESEARCH_DEFAULT_PARAMS
        from backend.models import ExperimentRun
        self._write(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 3.0, "phase": 1, "phase_plateau_count": 0,
            "params": dict(AUTORESEARCH_DEFAULT_PARAMS, dedup_threshold=0.9),
        })
        with app.app_context():
            db.session.add(ExperimentRun(
                id="e1", phase=1, parameter_changed="dedup_threshold",
                old_value="0.85", new_value="0.9", status="keep",
                composite_score=3.0, baseline_score=2.9, delta=0.1,
            ))
            db.session.commit()
            config = RAGAutoresearchService()._load_config()
        assert config["params"]["dedup_threshold"] == 0.9
        assert config["tuned"] == ["dedup_threshold"]
        assert config["baseline_score"] == 3.0

    def test_read_config_does_not_create_a_missing_file(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        with app.app_context():
            config, changed = RAGAutoresearchService()._read_config()
        assert changed is True
        assert config["params"]["dedup_threshold"] == 0.96
        assert not (tmp_path / "data" / "rag_experiment_config.json").exists()

    def test_read_config_leaves_a_pending_migration_unsaved(self, app, tmp_path, monkeypatch):
        from backend.config import AUTORESEARCH_DEFAULT_PARAMS
        cfg_file = self._write(tmp_path, monkeypatch, {
            "version": 1, "baseline_score": 3.0, "phase": 1, "phase_plateau_count": 0,
            "params": dict(AUTORESEARCH_DEFAULT_PARAMS, dedup_threshold=0.85),
        })
        before = cfg_file.read_bytes()
        with app.app_context():
            config, changed = RAGAutoresearchService()._read_config()
        assert changed is True
        assert config["params"]["dedup_threshold"] == 0.96
        assert config["baseline_score"] == 0.0
        assert cfg_file.read_bytes() == before

    def test_experiment_measures_the_model_value_as_baseline(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment", return_value={
                     "parameter": "top_k", "new_value": 8, "hypothesis": "t"}), \
                 patch.object(svc.eval_harness, "run_retrieval_eval",
                              return_value={"num_scored": 0}) as retr, \
                 patch.object(svc.eval_harness, "run_full_eval", return_value={
                     "composite_score": 0.0, "num_pairs": 10, "details": []}), \
                 patch.object(svc, "_log_experiment"):
                svc.run_single_experiment()
        base_params, test_params = (c.args[0] for c in retr.call_args_list)
        assert base_params["dedup_threshold"] == 0.96
        assert test_params["dedup_threshold"] == 0.96 and test_params["top_k"] == 8
