"""Autoresearch 2.0 tests: the active-config layer, honest eval scoring,
and the research-run engine (Phases A+B of the 2026-08-10 rebuild)."""
import hashlib
import time
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from backend.utils.clock import utcnow

try:
    from flask import Flask
    from backend.models import db, ResearchConfig, ResearchRun, EvalPair, Setting
    from backend.utils import experiment_context as ec
    from backend.services.rag_autoresearch_service import RAGAutoresearchService
    from backend.services.rag_eval_harness import RAGEvalHarness, LLMUnavailableError
    from backend.services.research_run_service import ResearchRunService
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
        ec.invalidate_active_params_cache()
        yield app
        ec.clear_experiment_config()
        ec.invalidate_active_params_cache()
        db.session.remove()
        db.drop_all()


class TestActiveParamsLayer:
    def test_empty_overlay_without_promotion_or_experiment(self, app):
        with app.app_context():
            assert ec.get_active_rag_params() == {}

    def test_promoted_config_feeds_overlay(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(
                params={"top_k": 8, "hybrid_search_alpha": 0.7},
                is_active=True, status="promoted",
            ))
            db.session.commit()
            ec.invalidate_active_params_cache()
            overlay = ec.get_active_rag_params()
            assert overlay["top_k"] == 8
            assert overlay["hybrid_search_alpha"] == 0.7

    def test_experiment_override_beats_promoted(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(params={"top_k": 8}, is_active=True))
            db.session.commit()
            ec.invalidate_active_params_cache()
            ec.set_experiment_config({"top_k": 2})
            assert ec.get_active_rag_params()["top_k"] == 2
            ec.clear_experiment_config()
            assert ec.get_active_rag_params()["top_k"] == 8

    def test_hostile_values_are_clamped(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(
                params={"top_k": 500, "hybrid_search_alpha": 9.0,
                        "dedup_threshold": "garbage"},
                is_active=True,
            ))
            db.session.commit()
            ec.invalidate_active_params_cache()
            overlay = ec.get_active_rag_params()
            assert overlay["top_k"] == 20          # clamped to max
            assert overlay["hybrid_search_alpha"] == 1.0
            assert "dedup_threshold" not in overlay  # non-numeric dropped

    def test_cache_invalidation_applies_immediately(self, app):
        with app.app_context():
            row = ResearchConfig(params={"top_k": 8}, is_active=True)
            db.session.add(row)
            db.session.commit()
            ec.invalidate_active_params_cache()
            assert ec.get_active_rag_params()["top_k"] == 8
            row.is_active = False
            db.session.commit()
            # cached until invalidated
            assert ec.get_active_rag_params()["top_k"] == 8
            ec.invalidate_active_params_cache()
            assert ec.get_active_rag_params() == {}


class TestHonestEval:
    def test_llm_unavailable_raises_not_floor(self, app):
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_get_llm", return_value=None):
                with pytest.raises(LLMUnavailableError):
                    harness._call_llm("prompt", role="judge")

    def test_llm_unavailable_mid_eval_becomes_crash(self, app):
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment",
                              return_value={"parameter": "top_k", "new_value": 9,
                                            "hypothesis": "t", "source": "llm"}), \
                 patch.object(svc.eval_harness, "run_full_eval",
                              side_effect=LLMUnavailableError("ollama down")), \
                 patch.object(svc, "_load_config", return_value={
                     "params": {}, "baseline_score": 3.0, "phase": 1,
                     "phase_plateau_count": 0}), \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"):
                result = svc.run_single_experiment()
            assert result["status"] == "crash"

    def test_chunk_hash_alignment_produces_hits(self, app):
        """A retrieved chunk whose text matches a stored chunk hash scores a hit
        — the legacy whole-document hash could never match anything."""
        with app.app_context():
            harness = RAGEvalHarness()
            chunk = "The mitochondria is the powerhouse of the cell."
            pair = {
                "source_chunk_hashes": [hashlib.sha256(chunk.encode()).hexdigest()],
                "source_doc_id": 1,
            }
            results = [{"text": "unrelated"}, {"text": chunk}]
            metrics = harness._score_retrieval(pair, results)
            assert metrics["hit_rate_at_k"] == 1.0
            assert metrics["mrr"] > 0

    def test_inactive_pairs_are_excluded(self, app):
        with app.app_context():
            db.session.add(EvalPair(question="q1", expected_answer="a1", is_active=True))
            db.session.add(EvalPair(question="q2", expected_answer="a2", is_active=False))
            db.session.commit()
            harness = RAGEvalHarness()
            pairs = harness._get_active_eval_pairs()
            assert [p["question"] for p in pairs] == ["q1"]

    def test_judge_parse_failure_is_labeled(self, app):
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_call_llm", return_value="not json"):
                score = harness.score_response("q", "a", "r", [])
            assert score["composite"] is None
            assert score.get("judge_parse_failed") is True


class TestResearchRunEngine:
    def _mk_service(self):
        return ResearchRunService()

    def test_ollama_down_is_failed_precondition(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-1", mode="rag_tuning",
                              wall_clock_budget_s=60)
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            with patch("requests.get", side_effect=ConnectionError("refused")), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "failed_precondition"
            assert "ollama" in (run.halt_reason or "").lower()
            assert "DID NOT RUN" in (run.report_md or "")

    def test_kill_flag_halts_run(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-2", mode="rag_tuning",
                              wall_clock_budget_s=3600)
            db.session.add(run)
            db.session.add(Setting(key="autoresearch_kill", value="true"))
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc._load_config.return_value = {
                "params": {}, "baseline_score": 3.0, "phase": 1,
                "phase_plateau_count": 0,
            }
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "killed"
            assert run.halt_reason == "killed"
            auto_svc.run_single_experiment.assert_not_called()

    def test_budget_exhaustion_completes_with_report(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-3", mode="rag_tuning",
                              wall_clock_budget_s=0)  # instantly exhausted
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc._load_config.return_value = {
                "params": {}, "baseline_score": 3.0, "phase": 1,
                "phase_plateau_count": 0,
            }
            auto_svc.eval_harness = MagicMock()
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch.object(svc_run, "_confirm_and_activate",
                              return_value="no candidate configs produced"), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "completed"
            assert run.halt_reason == "budget_exhausted"
            assert "Headline" in run.report_md

    def test_confirmation_rejects_weak_candidate(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-4", mode="rag_tuning")
            db.session.add(run)
            active = ResearchConfig(params={"top_k": 5}, is_active=True,
                                    status="promoted", composite_score=3.0)
            cand = ResearchConfig(params={"top_k": 9}, is_active=False,
                                  status="candidate", composite_score=3.2)
            db.session.add_all([active, cand])
            db.session.commit()
            auto_svc = MagicMock()
            # candidate barely better than active — below CONFIRMATION_MIN_DELTA
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.01}, {"composite_score": 3.0},
            ]
            note = svc_run._confirm_and_activate(auto_svc, run)
            assert "NOT confirmed" in note
            db.session.refresh(cand)
            db.session.refresh(active)
            assert cand.status == "rejected"
            assert active.is_active is True

    def test_confirmation_activates_clear_winner(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-5", mode="rag_tuning")
            db.session.add(run)
            active = ResearchConfig(params={"top_k": 5}, is_active=True,
                                    status="promoted", composite_score=3.0)
            cand = ResearchConfig(params={"top_k": 9}, is_active=False,
                                  status="candidate", composite_score=3.8)
            db.session.add_all([active, cand])
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.8}, {"composite_score": 3.0},
            ]
            note = svc_run._confirm_and_activate(auto_svc, run)
            assert "CONFIRMED" in note
            db.session.refresh(cand)
            db.session.refresh(active)
            assert cand.is_active is True and cand.status == "promoted"
            assert active.is_active is False and active.status == "superseded"
            assert run.promotions["promoted_ids"] == [cand.id]
            # Each confirmation eval starts on a fresh budget.
            assert auto_svc.eval_harness.begin_experiment_budget.call_count == 2

    def test_run_metadata_survives_promotion(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            cand = ResearchConfig(params={"top_k": 9}, is_active=False,
                                  status="candidate", composite_score=3.8)
            db.session.add(cand)
            db.session.commit()
            run = ResearchRun(run_tag="t-meta", mode="rag_tuning", promotions={
                "trigger": "nightly", "candidate_ids": [cand.id],
                "baseline": {"score": 3.0}, "latest_score": 3.8,
            })
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.8}, {"composite_score": 3.0},
            ]
            svc_run._confirm_and_activate(auto_svc, run)
            db.session.commit()
            db.session.refresh(run)
            assert run.promotions["promoted_ids"] == [cand.id]
            assert run.promotions["trigger"] == "nightly"
            assert run.promotions["candidate_ids"] == [cand.id]
            assert run.promotions["baseline"] == {"score": 3.0}
            assert run.promotions["latest_score"] == 3.8

    def _slice_service(self, stored_baseline=4.94):
        auto_svc = MagicMock()
        cfg = {"params": {"top_k": 5}, "baseline_score": stored_baseline, "phase": 1,
               "phase_plateau_count": 14, "tuned": []}
        auto_svc._load_config.return_value = cfg
        auto_svc.eval_harness.avg_pair_seconds = None
        auto_svc.eval_harness._get_active_eval_pairs.return_value = [
            {"eval_generation_id": "gen-b"}] * 11
        auto_svc.eval_harness.run_full_eval.return_value = {
            "composite_score": 2.39, "num_pairs": 11, "judged_pairs": 11,
            "details": [], "parse_fail_crash": False,
        }
        return auto_svc, cfg

    def test_every_run_measures_its_own_baseline(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-baseline", mode="rag_tuning", status="running",
                              wall_clock_budget_s=3600, started_at=utcnow())
            db.session.add(run)
            db.session.commit()
            auto_svc, cfg = self._slice_service(stored_baseline=4.94)
            order = []
            auto_svc.eval_harness.begin_experiment_budget.side_effect = \
                lambda **k: order.append("budget")
            auto_svc.eval_harness.run_full_eval.side_effect = \
                lambda *a, **k: order.append("eval") or {
                    "composite_score": 2.39, "num_pairs": 11, "judged_pairs": 11,
                    "details": [], "parse_fail_crash": False}

            def one_experiment(**kwargs):
                svc_run._set_kill(True)
                return {"experiment_id": "e1", "parameter": "top_k", "status": "discard",
                        "composite_score": 2.2, "baseline_score": 2.39, "delta": -0.19,
                        "fidelity": 1, "retrieval_metrics": {"judged_pairs": 11}}
            auto_svc.run_single_experiment.side_effect = one_experiment
            with patch("backend.services.research_run_service.time.sleep"), \
                 patch("backend.utils.gpu_check.gpu_busy", return_value=False):
                ledger, _ids, halt, status = svc_run._run_rag_slice(
                    run, auto_svc, time.time(), 3600)
            assert order[:2] == ["budget", "eval"]
            assert run.baseline_score == 2.39
            assert run.promotions["baseline"]["score"] == 2.39
            assert run.promotions["baseline"]["eval_generation"] == "gen-b"
            assert cfg["phase_plateau_count"] == 0 and cfg["baseline_score"] == 2.39
            assert halt == "killed" and len(ledger) == 1

    def test_a_baseline_that_measures_nothing_refuses_the_run(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-nobase", mode="rag_tuning", status="running",
                              wall_clock_budget_s=3600, started_at=utcnow())
            db.session.add(run)
            db.session.commit()
            auto_svc, _cfg = self._slice_service()
            auto_svc.eval_harness.run_full_eval.return_value = {
                "composite_score": 0.0, "num_pairs": 0, "details": []}
            _l, _i, halt, status = svc_run._run_rag_slice(run, auto_svc, time.time(), 3600)
            assert (halt, status) == ("baseline_eval_failed", "failed_precondition")
            db.session.refresh(run)
            assert run.status == "failed_precondition"
            assert run.halt_reason.startswith("baseline_eval_failed: no eval pairs")
            auto_svc.run_single_experiment.assert_not_called()

    def test_report_flags_single_model_judging(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-6", mode="rag_tuning",
                              baseline_score=3.0, best_score=3.1,
                              halt_reason="budget_exhausted")
            ledger = [
                {"parameter": "top_k", "old_value": "5", "new_value": "8",
                 "delta": 0.1, "status": "keep", "proposal_source": "llm",
                 "proposer_model": "gemma4", "judge_model": "gemma4",
                 "composite_score": 3.1},
            ]
            report = svc_run._write_report(run, ledger)
            assert "single-model judging" in report
            assert "100% LLM" in report

    def test_confirmation_ignores_foreign_candidates(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-scope", mode="rag_tuning")
            db.session.add(run)
            ours = ResearchConfig(
                params={"top_k": 9}, is_active=False,
                status="candidate", composite_score=3.8, source="local",
            )
            foreign = ResearchConfig(
                params={"top_k": 12}, is_active=False,
                status="candidate", composite_score=4.9,
                source="family_broadcast",
            )
            db.session.add_all([ours, foreign])
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.8}, {"composite_score": 3.0},
            ]
            active = ResearchConfig(
                params={"top_k": 5}, is_active=True,
                status="promoted", composite_score=3.0,
            )
            db.session.add(active)
            db.session.commit()
            note = svc_run._confirm_and_activate(
                auto_svc, run, candidate_ids=[ours.id],
            )
            assert "CONFIRMED" in note
            db.session.refresh(ours)
            db.session.refresh(foreign)
            assert ours.is_active is True
            assert foreign.is_active is False
            assert foreign.status == "candidate"

    def test_stale_running_row_recovered_on_kickoff(self, app):
        with app.app_context():
            stale = ResearchRun(
                run_tag="old-dead", mode="rag_tuning", status="running",
                started_at=utcnow() - timedelta(hours=3),
            )
            db.session.add(stale)
            db.session.commit()
            svc_run = self._mk_service()
            with patch.object(svc_run, "_celery_has_live_execute_run",
                              return_value=False), \
                 patch.object(svc_run, "_precondition_failures", return_value=([], [])), \
                 patch.object(svc_run, "_enqueue_execute_run"):
                result = svc_run.kickoff(budget_hours=1, trigger="manual")
            db.session.refresh(stale)
            assert stale.status == "halted"
            assert stale.halt_reason == "worker_crashed"
            assert result["status"] == "started"
            assert result["run"]["run_tag"] != "old-dead"

    def test_status_and_config_gets_leave_the_config_file_alone(self, app, tmp_path, monkeypatch):
        import json
        from backend.api.rag_autoresearch_api import autoresearch_bp
        from backend.config import AUTORESEARCH_DEFAULT_PARAMS
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        (tmp_path / "data").mkdir()
        cfg_file = tmp_path / "data" / "rag_experiment_config.json"
        # No "tuned" record: a load would infer it and rewrite the file.
        cfg_file.write_text(json.dumps({
            "version": 1, "baseline_score": 4.94, "phase": 1,
            "phase_plateau_count": 14,
            "params": dict(AUTORESEARCH_DEFAULT_PARAMS, dedup_threshold=0.85),
        }))
        before = cfg_file.read_bytes()
        if "autoresearch" not in app.blueprints:
            app.register_blueprint(autoresearch_bp)
        with app.test_client() as client:
            first = client.get("/api/autoresearch/status")
            second = client.get("/api/autoresearch/status")
            cfg = client.get("/api/autoresearch/config")
        assert first.status_code == second.status_code == cfg.status_code == 200
        assert first.get_json()["config_migration_pending"] is True
        assert cfg_file.read_bytes() == before

    def test_status_running_when_research_run_active(self, app):
        with app.app_context():
            db.session.add(ResearchRun(
                run_tag="t-status", mode="rag_tuning", status="running",
                started_at=utcnow(), wall_clock_budget_s=3600,
            ))
            db.session.commit()
            svc = RAGAutoresearchService()
            st = svc.get_status()
            assert st["running"] is True
            assert st["active_run"]["run_tag"] == "t-status"
            assert st["active_run"]["budget_remaining_s"] is not None


def _ollama_up():
    return patch("requests.get", return_value=MagicMock(status_code=200))


def _seed_corpus(eval_source_status="INDEXED", n_pairs=2):
    """Enough indexed text documents for the corpus gate, plus active eval
    pairs cut from a document in `eval_source_status`."""
    from backend.models import Document
    from backend.config import AUTORESEARCH_MIN_CORPUS_SIZE
    text = "A paragraph of real text about pumps and valves. " * 10
    for i in range(AUTORESEARCH_MIN_CORPUS_SIZE):
        db.session.add(Document(filename=f"d{i}.md", path=f"/x/d{i}.md",
                                content=text, index_status="INDEXED"))
    source = Document(filename="src.md", path="/x/src.md", content=text,
                      index_status=eval_source_status)
    db.session.add(source)
    db.session.flush()
    for i in range(n_pairs):
        db.session.add(EvalPair(question=f"q{i}", expected_answer="a",
                                source_doc_id=source.id, is_active=True))
    db.session.commit()


class TestKickoffRefusesWithReasons:
    def _kick(self, trigger="manual", active_model="gemma4:12b"):
        svc_run = ResearchRunService()
        enqueue = MagicMock()
        with _ollama_up(), \
             patch.object(svc_run, "_celery_has_live_execute_run", return_value=False), \
             patch.object(svc_run, "_enqueue_execute_run", enqueue), \
             patch("backend.utils.llm_service.get_saved_active_model_name",
                   return_value=active_model):
            result = svc_run.kickoff(budget_hours=1, trigger=trigger)
        return result, enqueue

    def test_unindexed_eval_sources_refuse_and_send_no_task(self, app):
        with app.app_context():
            _seed_corpus(eval_source_status="PENDING", n_pairs=3)
            db.session.add(Setting(key="autoresearch_judge_model", value="qwen3:14b"))
            db.session.commit()
            result, enqueue = self._kick()
            enqueue.assert_not_called()
            assert result["not_run"] is True
            assert len(result["reasons"]) == 1
            assert result["reasons"][0].startswith(
                "eval_sources_not_indexed — 3 of 3 active eval pairs come from "
                "documents that are not indexed (PENDING: 3)")
            run = db.session.get(ResearchRun, result["run"]["id"])
            assert run.status == "failed_precondition"
            assert run.halt_reason.startswith("eval_sources_not_indexed")
            assert len(run.halt_reason) <= 200
            assert run.ended_at is not None
            assert "DID NOT RUN" in run.report_md
            assert run.promotions["trigger"] == "manual"

    def test_every_reason_is_collected(self, app):
        with app.app_context():
            svc_run = ResearchRunService()
            with patch("requests.get", side_effect=ConnectionError("refused")), \
                 patch.object(svc_run, "_celery_has_live_execute_run", return_value=False), \
                 patch.object(svc_run, "_enqueue_execute_run") as enqueue, \
                 patch("backend.utils.llm_service.get_saved_active_model_name",
                       return_value="gemma4:12b"):
                result = svc_run.kickoff(budget_hours=1, trigger="nightly")
            enqueue.assert_not_called()
            codes = [r.split(" ")[0] for r in result["reasons"]]
            assert codes == ["ollama_unreachable", "insufficient_corpus",
                             "no_eval_pairs", "judge_unset"]
            run = db.session.get(ResearchRun, result["run"]["id"])
            assert all(f"`{r}`" in run.report_md for r in result["reasons"])

    def test_api_answers_422_with_the_reasons(self, app):
        from backend.api.rag_autoresearch_api import autoresearch_bp
        if "autoresearch" not in app.blueprints:
            app.register_blueprint(autoresearch_bp)
        with app.app_context():
            _seed_corpus(eval_source_status="PENDING")
        with app.test_client() as client, _ollama_up(), \
             patch("backend.services.research_run_service.ResearchRunService._celery_has_live_execute_run",
                   return_value=False), \
             patch("backend.services.research_run_service.ResearchRunService._enqueue_execute_run") as enqueue:
            res = client.post("/api/autoresearch/runs", json={"budget_hours": 1})
        assert res.status_code == 422
        body = res.get_json()
        assert body["not_run"] is True
        assert body["error"].startswith("eval_sources_not_indexed")
        assert body["run"]["status"] == "failed_precondition"
        enqueue.assert_not_called()

    def test_nightly_refuses_without_a_judge_while_manual_warns(self, app):
        with app.app_context():
            _seed_corpus(eval_source_status="INDEXED")
            nightly, enqueue = self._kick(trigger="nightly")
            enqueue.assert_not_called()
            assert nightly["not_run"] is True
            assert [r.split(" ")[0] for r in nightly["reasons"]] == ["judge_unset"]
            assert "gemma4:12b grades its own answers" in nightly["reasons"][0]

            manual, enqueue = self._kick(trigger="manual")
            enqueue.assert_called_once()
            assert manual["status"] == "started"
            assert manual["warnings"][0].startswith("judge_unset")
            run = db.session.get(ResearchRun, manual["run"]["id"])
            assert run.status == "pending"
            assert run.promotions["trigger"] == "manual"
            assert run.promotions["warnings"][0].startswith("judge_unset")

    def test_nightly_refuses_a_judge_that_is_the_answer_model(self, app):
        with app.app_context():
            _seed_corpus(eval_source_status="INDEXED")
            db.session.add(Setting(key="autoresearch_judge_model", value="gemma4:12b"))
            db.session.commit()
            result, enqueue = self._kick(trigger="nightly")
            enqueue.assert_not_called()
            assert result["reasons"][0].startswith("judge_same_as_answer_model")

    def test_nightly_starts_with_an_independent_judge(self, app):
        with app.app_context():
            _seed_corpus(eval_source_status="INDEXED")
            db.session.add(Setting(key="autoresearch_judge_model", value="qwen3:14b"))
            db.session.commit()
            result, enqueue = self._kick(trigger="nightly")
            enqueue.assert_called_once()
            assert result["status"] == "started" and result["warnings"] == []


class TestLedgerScores:
    def test_heal_rows_are_health_checks_not_keeps(self, app):
        from backend.models import ExperimentRun
        with app.app_context():
            run = ResearchRun(run_tag="t-heal", mode="unified")
            db.session.add(run)
            db.session.commit()
            svc_run = ResearchRunService()
            svc_run._log_heal_row(run, {"pytest": {"ok": True, "red": False}})
            svc_run._log_heal_row(run, {"tests_red": True, "pytest": {"ok": False, "red": True}})
            svc_run._log_heal_row(run, {"pytest": {"ok": False, "skipped": True}})
            statuses = [r.status for r in ExperimentRun.query.filter_by(run_tag="t-heal")
                        .order_by(ExperimentRun.created_at).all()]
            assert sorted(statuses) == ["fail", "pass", "skipped"]

    def test_best_and_latest_come_from_measured_experiments_only(self):
        from backend.services.research_run_service import summarize_ledger
        rows = [
            {"proposal_source": "heal", "status": "keep", "composite_score": 0.0},
            {"proposal_source": "tpe", "status": "discard", "composite_score": 2.6,
             "retrieval_metrics": {"fidelity": 1, "judged_pairs": 11}},
            # F0 screen: records the baseline without judging anything.
            {"proposal_source": "tpe", "status": "discard", "composite_score": 4.9,
             "retrieval_metrics": {"fidelity": 0, "judged_pairs": 0}},
            {"proposal_source": "llm", "status": "crash", "composite_score": 0.0},
            {"proposal_source": "code_arm", "status": "keep", "composite_score": 4.8,
             "retrieval_metrics": {"layer": "code"}},
            {"proposal_source": "tpe", "status": "discard", "composite_score": 2.1,
             "retrieval_metrics": {"fidelity": 1, "judged_pairs": 11}},
        ]
        s = summarize_ledger(rows)
        assert s["latest_score"] == 2.1
        assert s["best_tried_score"] == 2.6
        assert s["measured_experiments"] == 2
        assert s["experiments"] == 4
        assert s["health_checks"] == 1
        assert s["self_reported"] == 1

    def test_old_rows_without_fidelity_count_only_when_judged(self):
        from backend.services.research_run_service import summarize_ledger
        rows = [
            {"proposal_source": "tpe", "status": "discard", "composite_score": 1.9,
             "eval_details": [{"composite": 2}], "retrieval_metrics": {"layer": "params"}},
            {"proposal_source": "tpe", "status": "discard", "composite_score": 4.94,
             "eval_details": [], "retrieval_metrics": {"layer": "params"}},
        ]
        assert summarize_ledger(rows)["best_tried_score"] == 1.9

    def test_nothing_measured_leaves_best_score_empty(self, app):
        with app.app_context():
            run = ResearchRun(run_tag="t-nobest", mode="rag_tuning", status="running",
                              wall_clock_budget_s=3600, started_at=utcnow())
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc._load_config.return_value = {"params": {}, "phase": 1,
                                                  "phase_plateau_count": 0}
            auto_svc.eval_harness.avg_pair_seconds = None
            auto_svc.eval_harness.run_full_eval.return_value = {
                "composite_score": 4.0, "num_pairs": 11, "judged_pairs": 11,
                "parse_fail_crash": False}
            svc_run = ResearchRunService()

            def crash(**kwargs):
                svc_run._set_kill(True)
                return {"experiment_id": "c1", "parameter": "top_k", "status": "crash",
                        "composite_score": 0.0, "retrieval_metrics": {"judged_pairs": 0}}
            auto_svc.run_single_experiment.side_effect = crash
            with patch("backend.services.research_run_service.time.sleep"), \
                 patch("backend.utils.gpu_check.gpu_busy", return_value=False):
                svc_run._run_rag_slice(run, auto_svc, time.time(), 3600)
            assert run.baseline_score == 4.0
            assert run.best_score is None
            assert run.promotions["latest_score"] is None

    def test_runs_list_carries_measured_scores_and_health_checks(self, app):
        from backend.models import ExperimentRun
        from backend.api.rag_autoresearch_api import autoresearch_bp
        if "autoresearch" not in app.blueprints:
            app.register_blueprint(autoresearch_bp)
        with app.app_context():
            db.session.add(ResearchRun(run_tag="t-list", mode="unified", status="completed",
                                       baseline_score=3.0, best_score=3.0))
            t = utcnow()
            for i, (src, status, score) in enumerate(
                    [("heal", "keep", 0.0), ("tpe", "discard", 2.4), ("tpe", "discard", 2.2)]):
                db.session.add(ExperimentRun(
                    id=f"r{i}", run_tag="t-list", phase=1, parameter_changed="top_k",
                    new_value="4", status=status, proposal_source=src,
                    composite_score=score, eval_details=[{"composite": score}],
                    created_at=t + timedelta(seconds=i)))
            db.session.commit()
        with app.test_client() as client:
            run = client.get("/api/autoresearch/runs").get_json()["runs"][0]
        assert run["latest_score"] == 2.2
        assert run["best_tried_score"] == 2.4
        assert run["health_checks"] == 1
        assert run["best_score"] == 3.0  # the stored column is left as it was


class TestExecuteRunRunsOnce:
    def test_a_run_that_is_not_pending_is_left_alone(self, app):
        with app.app_context():
            run = ResearchRun(run_tag="t-redelivered", mode="unified",
                              status="completed", halt_reason="plateaued",
                              wall_clock_budget_s=3600)
            db.session.add(run)
            db.session.commit()
            svc_run = ResearchRunService()
            with patch.object(svc_run, "_check_preconditions") as pre, \
                 patch.object(svc_run, "_run_rag_slice") as rag, \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service"):
                svc_run.execute_run(run.id)
            pre.assert_not_called()
            rag.assert_not_called()
            db.session.refresh(run)
            assert run.status == "completed" and run.halt_reason == "plateaued"

    def test_preconditions_are_checked_again_with_the_stored_trigger(self, app):
        with app.app_context():
            run = ResearchRun(run_tag="t-nightly", mode="rag_tuning", status="pending",
                              wall_clock_budget_s=60, promotions={"trigger": "nightly"})
            db.session.add(run)
            db.session.commit()
            svc_run = ResearchRunService()
            with patch.object(svc_run, "_check_preconditions",
                              return_value=(False, "judge_unset — x")) as pre, \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service"):
                svc_run.execute_run(run.id)
            assert pre.call_args.kwargs["trigger"] == "nightly"
            db.session.refresh(run)
            assert run.status == "failed_precondition"
            assert run.halt_reason == "judge_unset — x"


class TestDirector:
    def test_allocate_plateaued_majority_code(self):
        split = ResearchRunService()._allocate(
            {"code_allowed": True, "rag_plateaued": True}, 1000)
        assert split["code_s"] >= 500
        assert split["code_s"] >= split["rag_s"]
        assert split["code_skip"] is None

    def test_allocate_skips_code_when_not_allowed(self):
        split = ResearchRunService()._allocate(
            {"code_allowed": False, "code_skip_reason": "codebase_locked",
             "rag_plateaued": True}, 1000)
        assert split["code_s"] == 0
        assert split["rag_s"] == 1000
        assert "codebase_locked" in split["code_skip"]

    def test_kickoff_default_mode_is_unified(self):
        import inspect
        assert inspect.signature(ResearchRunService.kickoff).parameters["mode"].default == "unified"

    def test_beat_kicks_unified(self):
        import inspect
        from backend.tasks import rag_autoresearch_tasks as tasks
        src = inspect.getsource(tasks.create_autoresearch_tasks)
        assert 'mode="unified"' in src

    def test_unified_skips_code_when_swarm_down(self, app):
        with app.app_context():
            run = ResearchRun(
                run_tag="t-unified-skip", mode="unified",
                wall_clock_budget_s=0, status="pending",
            )
            db.session.add(run)
            db.session.commit()
            svc_run = ResearchRunService()
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch.object(svc_run, "_diagnose", return_value={
                     "code_allowed": False, "code_skip_reason": "swarm_unreachable",
                     "rag_plateaued": False, "tests_red": False,
                 }), \
                 patch.object(svc_run, "_run_code_slice") as mock_code, \
                 patch.object(svc_run, "_confirm_and_activate",
                              return_value="no candidate configs produced"), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service"):
                svc_run.execute_run(run.id)
            mock_code.assert_not_called()
            db.session.refresh(run)
            assert run.status == "completed"
            assert "code half skipped" in (run.report_md or "")
            assert "swarm_unreachable" in (run.report_md or "")

    def test_code_keep_rejected_when_rag_drops(self, app):
        from backend.api.rag_autoresearch_api import autoresearch_bp
        if "autoresearch" not in app.blueprints:
            app.register_blueprint(autoresearch_bp)
        with app.test_client() as client:
            res = client.post("/api/autoresearch/experiments", json={
                "parameter": "chunker",
                "new_value": "smarter dedup",
                "status": "keep",
                "source": "code_arm",
                "composite_score": 2.0,
                "baseline_score": 3.0,
                "pytest_passed": True,
                "run_tag": "t-pne",
            })
            assert res.status_code == 201
            body = res.get_json()
            assert body["recorded_status"] == "discard"

    def test_snapshot_pytest_does_not_dispatch_fixes(self, app):
        with app.app_context():
            from backend.services.self_improvement_service import (
                get_self_improvement_service,
            )
            si = get_self_improvement_service()
            si._running = False
            with patch("backend.services.self_improvement_service._is_codebase_locked",
                       return_value=False), \
                 patch("backend.services.self_improvement_service._is_self_improvement_enabled",
                       return_value=True), \
                 patch("backend.services.self_improvement_service.subprocess.run") as mock_run, \
                 patch.object(si, "_attempt_fix") as mock_fix, \
                 patch.object(si, "run_self_check") as mock_check:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                out = si.snapshot_pytest()
            mock_fix.assert_not_called()
            mock_check.assert_not_called()
            assert out.get("ok") is True
            assert out.get("red") is False
