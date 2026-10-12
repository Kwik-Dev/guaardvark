"""Question and answer pairs cut from a person's own notes, for fine-tuning.

The question kinds and prompts come from the RAG eval harness
(rag_eval_harness: specific, reasoning, multi-hop), turned from scoring
retrieval to teaching: the answer is written from the text alone, and each
chunk also gets one "summarise this section" pair. Generation goes through the
local chat model like any chat call; nothing leaves the machine.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_LINE = "You are an assistant who knows these notes."
QUESTION_KINDS = ("specific", "reasoning", "multi_hop")

PAIR_PROMPT = """You are writing training examples from a person's own notes.

Given the following text, write ONE {question_kind} question someone who works with these notes might ask, and the answer based ONLY on this text.

Question kind:
- specific: a factual question whose answer is a span of this text
- reasoning: a question that requires combining two facts in this text, not copying a single sentence

Text:
{chunk_text}

Return ONLY valid JSON:
{{"question": "your question here", "answer": "the answer from the text"}}"""

MULTI_HOP_PROMPT = """You are writing training examples from a person's own notes.

Given TWO passages of the same notes, write ONE question that needs information from BOTH passages to answer, and the answer grounded in those passages.

Passage A:
{chunk_a}

Passage B:
{chunk_b}

Return ONLY valid JSON:
{{"question": "your question here", "answer": "the answer from both passages"}}"""

SUMMARY_PROMPT = """Summarise the following notes in three to five sentences, using only what they say.

Notes:
{chunk_text}

Return ONLY valid JSON:
{{"summary": "your summary here"}}"""


class GenerationUnavailable(RuntimeError):
    """No local chat model answered; the build stops rather than writing a guess."""


def local_llm():
    """The local chat model the rest of the app uses (the saved active model)."""
    from backend.utils.llm_service import get_llm_instance

    llm = None
    try:
        from backend.utils.llm_service import get_saved_active_model_name

        active = get_saved_active_model_name()
        if active:
            llm = get_llm_instance(model=active)
    except Exception as e:
        logger.debug(f"Saved active model not usable: {e}")
    if llm is None:
        llm = get_llm_instance()
    if llm is None:
        raise GenerationUnavailable("No local chat model is available. Is Ollama running?")
    return llm


def _heading(chunk_text: str) -> Optional[str]:
    match = re.search(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", chunk_text, re.M)
    return match.group(1).strip()[:80] if match else None


class PairGenerator:
    """Asks the local model for pairs, one call per question."""

    def __init__(self, llm=None, temperature: float = 0.2):
        self._llm = llm
        self.temperature = temperature
        self.calls = 0
        self.seconds = 0.0
        self.unparsed = 0

    def _complete(self, prompt: str) -> Optional[dict]:
        if self._llm is None:
            self._llm = local_llm()
        from backend.services.rag_experiment_agent import _extract_json

        started = time.monotonic()
        try:
            text = str(self._llm.complete(prompt, temperature=self.temperature)).strip()
        except Exception as e:
            raise GenerationUnavailable(f"The local model did not answer: {e}") from e
        finally:
            self.calls += 1
            self.seconds += time.monotonic() - started
        try:
            parsed = json.loads(_extract_json(text))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict):
            self.unparsed += 1
            return None
        return parsed

    def question(self, chunk_text: str, kind: str, other_chunk: Optional[str] = None) -> Optional[dict]:
        if kind == "multi_hop" and other_chunk:
            prompt = MULTI_HOP_PROMPT.format(chunk_a=chunk_text[:1500], chunk_b=other_chunk[:1500])
        else:
            kind = "reasoning" if kind == "multi_hop" else kind
            prompt = PAIR_PROMPT.format(chunk_text=chunk_text[:2000], question_kind=kind)
        parsed = self._complete(prompt)
        if not parsed:
            return None
        question, answer = parsed.get("question"), parsed.get("answer") or parsed.get("expected_answer")
        if not isinstance(question, str) or not isinstance(answer, str):
            self.unparsed += 1
            return None
        return {"question": question.strip(), "answer": answer.strip(), "kind": kind}

    def summary(self, chunk_text: str, source_name: str) -> Optional[dict]:
        parsed = self._complete(SUMMARY_PROMPT.format(chunk_text=chunk_text[:2000]))
        summary = (parsed or {}).get("summary")
        if not isinstance(summary, str):
            if parsed is not None:
                self.unparsed += 1
            return None
        topic = _heading(chunk_text)
        question = (f"Summarise the notes on {topic}." if topic
                    else f"Summarise what the notes in {source_name} say.")
        return {"question": question, "answer": summary.strip(), "kind": "summary"}


def as_training_row(pair: dict, source_path: str, chunk_hash: str) -> dict:
    """A `messages` row; the trainer reads only `messages`, the rest is provenance."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_LINE},
            {"role": "user", "content": pair["question"]},
            {"role": "assistant", "content": pair["answer"]},
        ],
        "metadata": {"source_path": source_path, "chunk_hash": chunk_hash, "kind": pair["kind"]},
    }
