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
