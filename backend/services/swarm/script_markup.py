"""Deterministic script markup for casting control.

The Screenwriter (Gemma) extracts every named character, location, and prop as a
Subject and *guesses* its ``kind``. That guess drives whether the thing becomes
an identity-locked, LoRA-trained cast member or is generated inline from its
description. Gemma over- and under-extracts (e.g. it turned "she grabs the
microphone. She is wearing black lipstick" into three trainable subjects).

Markup lets the operator override that guess **deterministically** — parsed by
regex here, not interpreted by the LLM, so it is reliable regardless of model
compliance:

    [[Name]]            Pin Name as a trainable cast member (cast_required=True),
                        even if it is a prop/location. Use for a hero prop or a
                        recurring set you want visually consistent across shots.
    [[Name:kind]]       Pin with an explicit kind (character|environment|prop).
    {{Name:kind}}       Force Name's kind WITHOUT pinning — cast_required then
                        follows the kind default (character→True, else False).
                        Use to correct a misclassification, e.g.
                        "{{Black Lipstick:prop}}" so it is generated inline
                        rather than trained as a character.

The brackets give an explicit name boundary, which is why this is robust where
parsing a bare "Name (character)" parenthetical out of free prose is not.

Pipeline: ``parse_markup(script_text)`` → ``(cleaned_text, intents)``. Gemma
runs on ``cleaned_text`` (markup syntax stripped, names retained, natural prose).
``intents`` is then applied to Gemma's extracted subjects by name match via
``apply_intents``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

VALID_KINDS = {"character", "environment", "prop"}

# [[ Name ]] or [[ Name : kind ]]  — pin as cast member.
_PIN_RE = re.compile(
    r"\[\[\s*(?P<name>[^\[\]:]+?)\s*(?::\s*(?P<kind>character|environment|prop)\s*)?\]\]",
    re.IGNORECASE,
)
# {{ Name : kind }}  — kind override only (kind is mandatory in this form).
_KIND_RE = re.compile(
    r"\{\{\s*(?P<name>[^\{\}:]+?)\s*:\s*(?P<kind>character|environment|prop)\s*\}\}",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """Match key: lowercase, trimmed, internal whitespace collapsed."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def default_cast_required(kind: str | None) -> bool:
    """Kind-based default when no explicit markup applies: only characters are
    identity-locked cast members by default; props/environments generate inline."""
    return (kind or "").lower() == "character"


def effective_cast_required(cast_required: bool | None, kind: str | None) -> bool:
    """Resolve a Subject's cast requirement. ``None`` (legacy rows predating the
    column, or no markup) falls back to the kind-based default."""
    if cast_required is None:
        return default_cast_required(kind)
    return bool(cast_required)


@dataclass
class CastIntent:
    """An operator's explicit casting intent for one named subject."""
    display_name: str
    kind: str | None = None        # explicit kind override, or None
    pinned: bool = False           # [[...]] forces a trainable cast member

    def resolved_cast_required(self) -> bool:
        """True if this subject must have a trained LoRA. A pin always requires
        one; otherwise it follows the kind default."""
        if self.pinned:
            return True
        return default_cast_required(self.kind)


@dataclass
class MarkupResult:
    cleaned_text: str
    intents: dict[str, CastIntent] = field(default_factory=dict)


def parse_markup(script_text: str) -> MarkupResult:
    """Extract casting intents and return prose with the markup syntax stripped
    (names retained) so the LLM never sees the brackets."""
    if not script_text:
        return MarkupResult(cleaned_text=script_text or "")

    intents: dict[str, CastIntent] = {}

    def _merge(name: str, *, kind: str | None, pinned: bool) -> None:
        key = normalize_name(name)
        if not key:
            return
        cur = intents.get(key)
        if cur is None:
            intents[key] = CastIntent(display_name=name.strip(), kind=kind, pinned=pinned)
        else:
            # Pins are sticky; a later kind tag can still refine the kind.
            cur.pinned = cur.pinned or pinned
            if kind:
                cur.kind = kind.lower()

    for m in _PIN_RE.finditer(script_text):
        k = m.group("kind")
        _merge(m.group("name"), kind=(k.lower() if k else None), pinned=True)
    for m in _KIND_RE.finditer(script_text):
        _merge(m.group("name"), kind=m.group("kind").lower(), pinned=False)

    # Strip syntax, keep the display name, so Gemma reads natural prose.
    cleaned = _PIN_RE.sub(lambda m: m.group("name").strip(), script_text)
    cleaned = _KIND_RE.sub(lambda m: m.group("name").strip(), cleaned)

    return MarkupResult(cleaned_text=cleaned, intents=intents)


def apply_intents(subjects: list[dict], intents: dict[str, CastIntent]) -> list[dict]:
    """Reconcile Gemma's extracted subjects with operator intents.

    ``subjects`` is a list of ``{"name", "kind", "description"}`` dicts (the
    Screenwriter output). Returns a new list of dicts, each additionally
    carrying a resolved ``"cast_required"`` bool. Intents matched by name
    override ``kind``; pinned intents that match nothing are injected as new
    subjects so an operator can pin something Gemma missed.

    Pure function — no DB access — so it is unit-testable in isolation.
    """
    out: list[dict] = []
    matched: set[str] = set()

    for s in subjects:
        name = s.get("name", "")
        kind = (s.get("kind") or "").lower()
        key = normalize_name(name)
        intent = intents.get(key)
        if intent is not None:
            matched.add(key)
            if intent.kind:
                kind = intent.kind
            cast_required = intent.resolved_cast_required()
        else:
            cast_required = default_cast_required(kind)
        out.append({
            "name": name,
            "kind": kind,
            "description": s.get("description"),
            "cast_required": cast_required,
        })

    # Inject pinned-but-unextracted subjects (operator pinned a name Gemma
    # didn't surface). Kind-only overrides for unextracted names are ignored —
    # there is nothing to render without a description.
    for key, intent in intents.items():
        if key in matched or not intent.pinned:
            continue
        out.append({
            "name": intent.display_name,
            "kind": intent.kind or "prop",
            "description": None,
            "cast_required": True,
        })

    return out
