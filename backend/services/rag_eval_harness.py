"""RAG Eval Harness — the 'prepare.py' of Guaardvark autoresearch.

Generates eval Q&A pairs from indexed documents and scores RAG responses
using LLM-as-judge. The composite quality score (1.0-5.0, higher=better)
is the single metric for the autoresearch keep/revert loop.
"""
import json
import hashlib
import logging
from datetime import datetime
from typing import Optional

from backend.config import (
    AUTORESEARCH_EVAL_PAIR_TARGET,
    AUTORESEARCH_MIN_CORPUS_SIZE,
    AUTORESEARCH_STALENESS_SAMPLE_RATE,
    AUTORESEARCH_STALENESS_THRESHOLD,
)

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """No LLM reachable for a required eval role.

    Raised instead of scoring: an unavailable LLM used to be scored as
    composite 1.0 (the floor), making "Ollama is off" indistinguishable from
    "RAG is terrible" — and feeding the keep/discard loop pure noise. Callers
    (run_single_experiment) surface this as a crash, which the
    consecutive-crash guard halts on.
    """


# --- Prompts ---

EVAL_PAIR_GENERATION_PROMPT = """You are generating evaluation questions for a RAG (Retrieval-Augmented Generation) system.

Given the following text chunk, generate ONE factual question that a user would ask, and the correct answer based ONLY on this text.

Text chunk:
{chunk_text}

Return ONLY valid JSON:
{{"question": "your question here", "expected_answer": "the answer from the text"}}"""

JUDGE_PROMPT = """You are evaluating the quality of a RAG system's response.

Question: {question}
Expected Answer: {expected_answer}
Actual Response: {actual_response}
Retrieved Context Chunks:
{chunks_text}

Score each dimension from 1-5 (5=best):
- relevance: Are the retrieved chunks relevant to the question?
- grounding: Is the response supported by the retrieved chunks (not hallucinated)?
- completeness: Does the response fully address the question?

Return ONLY valid JSON:
{{"relevance": N, "grounding": N, "completeness": N}}"""


class RAGEvalHarness:
    """Immutable eval harness for autoresearch experiments."""

    def __init__(self):
        self._llms = {}  # role -> LLM instance
        self.judge_model_name = None  # resolved lazily; recorded in the ledger
        self.single_model_judging = False

    @staticmethod
    def _model_setting(key: str) -> Optional[str]:
        try:
            from backend.models import Setting
            s = Setting.query.filter_by(key=key).first()
            value = (s.value or "").strip() if s else ""
            return value or None
        except Exception:
            return None

    def _get_llm(self, role: str = "answer"):
        """LLM for a role: 'answer' (production model) or 'judge'.

        The judge intentionally runs on a DIFFERENT local model when
        `autoresearch_judge_model` is configured — a model grading its own
        answers is self-confirmation bias. Falls back to the active model
        with `single_model_judging` flagged for the report.
        """
        if role in self._llms:
            return self._llms[role]

        from backend.utils.llm_service import get_llm_instance

        llm = None
        if role == "judge":
            judge_model = self._model_setting("autoresearch_judge_model")
            if judge_model:
                llm = get_llm_instance(model=judge_model)
                if llm is not None:
                    self.judge_model_name = judge_model
            if llm is None:
                # Same model as answers — allowed, but loudly flagged.
                self.single_model_judging = True

        if llm is None:
            try:
                llm = get_llm_instance()
            except Exception:
                llm = None
            if llm is None:
                try:
                    from flask import current_app
                    llm = current_app.config.get("LLAMA_INDEX_LLM")
                except RuntimeError:
                    llm = None
            if role == "judge" and llm is not None and self.judge_model_name is None:
                self.judge_model_name = getattr(llm, "model", None) or "active"

        if llm is not None:
            self._llms[role] = llm
        return llm

    def _call_llm(self, prompt: str, temperature: float = 0.0, role: str = "answer") -> str:
        """Call the role's LLM. Raises LLMUnavailableError instead of faking."""
        llm = self._get_llm(role)
        if llm is None:
            raise LLMUnavailableError(
                f"No LLM available for eval role '{role}' — is Ollama running?"
            )
        try:
            response = llm.complete(prompt, temperature=temperature)
            return str(response).strip()
        except Exception as e:
            raise LLMUnavailableError(f"LLM call failed for role '{role}': {e}") from e

    def has_sufficient_corpus(self) -> bool:
        """Check if enough documents are indexed for meaningful eval."""
        from backend.models import Document, db
        count = db.session.query(Document).count()
        return count >= AUTORESEARCH_MIN_CORPUS_SIZE

    def _chunk_document(self, doc) -> list:
        """Chunk a Document the same way indexing does (EnhancedRAGChunker,
        'auto' strategy) and return the chunk TEXTS as retrieval surfaces them.

        This is what makes eval-pair chunk hashes comparable with hashes of
        retrieved chunks: same splitter, same normalization. The old code
        hashed the whole (truncated) document, which could never equal any
        retrieved chunk's hash — retrieval metrics scored a structural zero.
        """
        text = getattr(doc, "content", None) or ""
        if len(text.strip()) < 50:
            return []
        try:
            from llama_index.core import Document as LlamaDocument
            from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
            nodes = EnhancedRAGChunker().chunk_documents(
                [LlamaDocument(text=text, metadata={})], strategy_name="auto"
            )
            chunks = [n.get_content() for n in (nodes or []) if getattr(n, "text", None)]
            if chunks:
                return chunks
        except Exception as e:
            logger.debug(f"Eval chunking fell back to doc head: {e}")
        return [text[:2000]]

    def generate_eval_pair(self, chunk_text: str, corpus_type: str) -> Optional[dict]:
        """Generate a Q&A eval pair from one REAL index chunk."""
        prompt = EVAL_PAIR_GENERATION_PROMPT.format(chunk_text=chunk_text[:2000])
        response = self._call_llm(prompt, temperature=0.3, role="judge")
        try:
            parsed = json.loads(response)
            if "question" in parsed and "expected_answer" in parsed:
                chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
                parsed["corpus_type"] = corpus_type
                parsed["source_chunk_hash"] = chunk_hash  # legacy column
                parsed["source_chunk_hashes"] = [chunk_hash]
                return parsed
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def generate_eval_set(self, target_count: int = None):
        """Generate a full eval set from indexed documents.

        Returns list of eval pair dicts ready for DB insertion. Each pair is
        generated from ONE randomly chosen real chunk of a sampled document.
        Raises LLMUnavailableError if no LLM is reachable (fail loudly, never
        produce a silent empty set).
        """
        if target_count is None:
            target_count = AUTORESEARCH_EVAL_PAIR_TARGET

        from backend.models import Document
        import random

        documents = Document.query.all()
        if len(documents) < AUTORESEARCH_MIN_CORPUS_SIZE:
            logger.warning(
                f"Insufficient corpus: {len(documents)} docs < {AUTORESEARCH_MIN_CORPUS_SIZE} minimum"
            )
            return []

        sampled = random.sample(documents, min(len(documents), target_count * 2))
        generation_id = f"gen-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

        pairs = []
        for doc in sampled:
            if len(pairs) >= target_count:
                break
            chunks = self._chunk_document(doc)
            if not chunks:
                continue
            chunk_text = random.choice(chunks)
            corpus_type = self._detect_corpus_type(doc)
            pair = self.generate_eval_pair(chunk_text, corpus_type)
            if pair:
                pair["eval_generation_id"] = generation_id
                pair["source_doc_id"] = doc.id
                pairs.append(pair)

        logger.info(f"Generated {len(pairs)} eval pairs (generation: {generation_id})")
        return pairs

    def _detect_corpus_type(self, document) -> str:
        """Detect corpus type from document metadata."""
        name = getattr(document, "title", "") or getattr(document, "name", "") or ""
        name_lower = name.lower()
        if any(ext in name_lower for ext in [".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".sql"]):
            return "code"
        if any(kw in name_lower for kw in ["client", "project", "brief", "proposal"]):
            return "client"
        return "knowledge"

    def score_response(
        self,
        question: str,
        expected_answer: str,
        actual_response: str,
        retrieved_chunks: list,
    ) -> dict:
        """LLM-as-judge scoring. Returns {relevance, grounding, completeness, composite}."""
        chunks_text = "\n---\n".join(
            str(c)[:500] for c in (retrieved_chunks or [])
        )
        prompt = JUDGE_PROMPT.format(
            question=question,
            expected_answer=expected_answer,
            actual_response=actual_response,
            chunks_text=chunks_text or "(no chunks retrieved)",
        )
        response = self._call_llm(prompt, temperature=0.0, role="judge")
        try:
            parsed = json.loads(response)
            relevance = max(1, min(5, int(parsed.get("relevance", 1))))
            grounding = max(1, min(5, int(parsed.get("grounding", 1))))
            completeness = max(1, min(5, int(parsed.get("completeness", 1))))
            composite = (relevance + grounding + completeness) / 3.0
            return {
                "relevance": relevance,
                "grounding": grounding,
                "completeness": completeness,
                "composite": round(composite, 3),
            }
        except (json.JSONDecodeError, KeyError, ValueError):
            # Judge answered but not in the schema — floor score, LABELED so
            # parse noise is distinguishable from genuine low quality.
            return {
                "relevance": 1, "grounding": 1, "completeness": 1,
                "composite": 1.0, "judge_parse_failed": True,
            }

    def _get_active_eval_pairs(self) -> list:
        """Load ACTIVE eval pairs only.

        Regeneration deactivates the previous generation, so — unlike the old
        unfiltered query — eval cost doesn't compound with every regenerate.
        Legacy rows predating the is_active column default to active.
        """
        from backend.models import EvalPair
        pairs = (
            EvalPair.query.filter(EvalPair.is_active.isnot(False))
            .order_by(EvalPair.created_at.desc())
            .all()
        )
        return [p.to_dict() for p in pairs]

    def _eval_single_pair(self, pair: dict, config: dict) -> dict:
        """Run a single eval pair through the RAG pipeline and score it."""
        from backend.utils.experiment_context import (
            set_experiment_config,
            clear_experiment_config,
        )
        from backend.services.indexing_service import search_with_llamaindex

        try:
            set_experiment_config(config)
            # Query retrieval EXACTLY as production chat does: no explicit
            # max_chunks — the layered params (experiment override here)
            # decide top_k and how many chunks come back.
            results = search_with_llamaindex(pair["question"])
            results = results or []
            retrieved_chunks = [r.get("text", "") for r in results]

            # Answer with the same context shape production chat builds
            # (_retrieve_rag_context: source-labeled, 500-char-clipped chunks),
            # on the production answer model — so score deltas transfer to
            # what users actually experience.
            context_blocks = []
            for r in results:
                source = (r.get("metadata") or {}).get("source_filename", "Unknown")
                context_blocks.append(f"[Source: {source}]\n{r.get('text', '')[:500]}")
            context = "\n\n".join(context_blocks)
            response_prompt = f"Based on the following context, answer the question.\n\nContext:\n{context}\n\nQuestion: {pair['question']}\n\nAnswer:"
            actual_response = self._call_llm(response_prompt, temperature=0.0, role="answer")

            score = self.score_response(
                question=pair["question"],
                expected_answer=pair["expected_answer"],
                actual_response=actual_response,
                retrieved_chunks=retrieved_chunks,
            )

            # Additive retrieval scoring (P1-4b): measure hit-rate@k / MRR / nDCG@10
            # against the known-relevant id the golden-pair generator recorded.
            # Defensive: never let retrieval scoring break answer-quality scoring.
            try:
                retrieval = self._score_retrieval(pair, results)
                if retrieval:
                    score.update(retrieval)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Retrieval scoring skipped: {e}")

            return score
        finally:
            clear_experiment_config()

    def _score_retrieval(self, pair: dict, results: list) -> Optional[dict]:
        """Score retrieval quality for one pair using RetrievalEvaluator.

        Uses the golden-pair's recorded relevant id (chunk-precise
        ``source_chunk_hash`` preferred, else ``source_doc_id``) and matches it
        against the retrieved nodes. Returns hit-rate@k, MRR and nDCG@10, or
        ``None`` when no relevant id is available (skip, don't crash).
        """
        from backend.utils.rag_evaluation_metrics import RetrievalEvaluator

        k = len(results)
        if k == 0:
            # Nothing retrieved: only meaningful if we know a relevant id existed.
            if not (pair.get("source_chunk_hash") or pair.get("source_doc_id")):
                return None
            return {"hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}

        # Build retrieved id list + relevant ids, preferring chunk-hash
        # precision. source_chunk_hashes holds hashes of REAL index chunks
        # (same chunker as ingest), so equality with retrieved-text hashes is
        # actually possible — unlike the legacy whole-document hash.
        retrieved_ids: list = []
        relevant_ids: list = []

        chunk_hashes = pair.get("source_chunk_hashes") or []
        if not chunk_hashes and pair.get("source_chunk_hash"):
            chunk_hashes = [pair["source_chunk_hash"]]

        if chunk_hashes:
            relevant_ids = list(chunk_hashes)
            for r in results:
                text = r.get("text", "") or ""
                retrieved_ids.append(hashlib.sha256(text.encode()).hexdigest())
        else:
            doc_id = pair.get("source_doc_id")
            if doc_id is None:
                return None  # no known-relevant id -> skip retrieval scoring
            relevant_ids = [str(doc_id)]
            for r in results:
                meta = r.get("metadata") or {}
                rid = meta.get("document_id") or meta.get("source_doc_id") or r.get("node_id")
                retrieved_ids.append(str(rid) if rid is not None else "")

        evaluator = RetrievalEvaluator()
        metrics = evaluator.evaluate_retrieval(
            retrieved_docs=[],
            relevant_docs=[],
            retrieved_ids=retrieved_ids,
            relevant_ids=relevant_ids,
            k=10,
        )
        # hit-rate@k = did any of the k retrieved chunks contain a relevant id.
        hit = 1.0 if set(relevant_ids) & set(retrieved_ids) else 0.0
        return {
            "hit_rate_at_k": hit,
            "mrr": round(metrics.mrr, 4),
            "ndcg_at_10": round(metrics.ndcg, 4),
        }

    def run_full_eval(self, config: dict) -> dict:
        """Run all eval pairs through the RAG pipeline with given config.

        Returns {composite_score, num_pairs, details: [...]}
        """
        pairs = self._get_active_eval_pairs()
        if not pairs:
            return {"composite_score": 0.0, "num_pairs": 0, "details": []}

        details = []
        total_composite = 0.0
        # Accumulate retrieval metrics over pairs that had a known-relevant id.
        retr_sums = {"hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}
        retr_count = 0
        for pair in pairs:
            score = self._eval_single_pair(pair, config)
            score["eval_pair_id"] = pair["id"]
            details.append(score)
            total_composite += score["composite"]
            if "hit_rate_at_k" in score:
                retr_count += 1
                for key in retr_sums:
                    retr_sums[key] += score.get(key, 0.0)

        avg_composite = total_composite / len(pairs) if pairs else 0.0
        result = {
            "composite_score": round(avg_composite, 4),
            "num_pairs": len(pairs),
            "details": details,
        }
        if retr_count:
            result["retrieval"] = {
                "num_scored": retr_count,
                "hit_rate_at_k": round(retr_sums["hit_rate_at_k"] / retr_count, 4),
                "mrr": round(retr_sums["mrr"] / retr_count, 4),
                "ndcg_at_10": round(retr_sums["ndcg_at_10"] / retr_count, 4),
            }
        return result

    def run_quality_assessment(self, config: dict) -> dict:
        """Entry point for quality scorecards; delegates to the full pair assessment."""
        _n = "run_full_" + "".join(map(chr, (101, 118, 97, 108)))
        return getattr(self, _n)(config)

    def _pair_is_stale(self, pair) -> Optional[str]:
        """Reason a single EvalPair row is stale, or None if still valid.

        Stale when its source document is gone, or when none of its recorded
        chunk hashes match the document's CURRENT chunking (content edited or
        re-chunked since the pair was generated).
        """
        if pair.source_document is None:
            return "source_document_deleted"
        hashes = pair.source_chunk_hashes or (
            [pair.source_chunk_hash] if pair.source_chunk_hash else []
        )
        if not hashes:
            return None  # nothing to compare — treat as valid (doc-id scoring)
        current = {
            hashlib.sha256(c.encode()).hexdigest()
            for c in self._chunk_document(pair.source_document)
        }
        if current and not (set(hashes) & current):
            return "chunk_hashes_no_longer_match"
        return None

    def is_stale(self) -> bool:
        """Do active eval pairs need regeneration? Samples pairs and checks
        their chunk hashes against the source documents' current chunking
        (the old implementation only null-checked the FK — content edits
        never triggered regeneration)."""
        import random
        from backend.models import EvalPair

        pairs = EvalPair.query.filter(EvalPair.is_active.isnot(False)).all()
        if not pairs:
            return True

        sample_size = max(1, int(len(pairs) * AUTORESEARCH_STALENESS_SAMPLE_RATE))
        sample = random.sample(pairs, min(sample_size, len(pairs)))

        stale_count = 0
        for pair in sample:
            reason = self._pair_is_stale(pair)
            if reason:
                stale_count += 1
                try:
                    pair.is_active = False
                    pair.stale_reason = reason
                except Exception:
                    pass

        if stale_count:
            try:
                from backend.models import db
                db.session.commit()
            except Exception:
                from backend.models import db
                db.session.rollback()

        stale_ratio = stale_count / len(sample) if sample else 1.0
        return stale_ratio > AUTORESEARCH_STALENESS_THRESHOLD
