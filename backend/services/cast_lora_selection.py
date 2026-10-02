"""Which LoRA each Cast member renders with, when a member can hold one per base model.

A member's default LoRA is ``Subject.lora_path``. Members can also hold LoRAs for
other bases as ``SubjectLora`` rows (trained here or imported). This module is the
single place that decides which one a render uses:

1. Model on Auto (or a caller that names no model): each member renders with its
   default LoRA. Several members render on a base they all hold: the first
   member's default base when every member has it, otherwise another base they
   share. No shared base is a refusal.
2. A specific image model: every member needs a LoRA whose base matches that
   model's family (``lora_compatible_with_inference``). A member without one is a
   refusal that names what the member does have. The render never falls back to
   another base or drops the likeness. This holds for every member, including
   one whose only LoRA is its default.
3. Members with no ``SubjectLora`` rows are left exactly as they are whenever the
   render goes ahead: on Auto, or on a model their default LoRA matches, they
   take the same decision they took before rows existed.

The caller gets back member objects to render with. For members with rows these
are views whose ``lora_path``, ``trigger_word`` and ``training_settings_json``
describe the chosen LoRA, so ``cast_lock.subjects_to_lock`` and the route logic in
``character_still_pipeline`` need no knowledge of the table.

Writes go through ``record_subject_lora`` (training, imports) and
``set_default_lora`` (choosing a member's default). Neither touches
``training_settings_json.base_model_id``: that stays the base the member trains
on, while the default LoRA's base is read from its row or its sidecar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

log = logging.getLogger(__name__)

# Model values that name no model. "comfyui" selects a backend, not a model, so a
# cast render under it behaves like Auto.
AUTO_MODELS = frozenset({"", "auto", "comfyui"})


class CastLoraRefusal(ValueError):
    """The selected model or the cast combination has no LoRA to render with."""


@dataclass(frozen=True)
class LoraOption:
    """One LoRA a member can render with."""
    base_model_id: str
    lora_path: str
    # None: the member's own trigger_word applies.
    trigger_word: Optional[str] = None
    version: Optional[int] = None
    source: str = "default"


@dataclass
class CastLoraSelection:
    """Members to render with and the base they share.

    ``base_model_id`` is None when no member has rows (``legacy``): ``subjects``
    are then the caller's own objects, untouched.
    """
    subjects: list
    base_model_id: Optional[str]
    legacy: bool
    # Every LoRA path the members hold (default and rows), so a caller can drop
    # paths it passed as a fallback for these same members.
    member_paths: set = field(default_factory=set)


class _MemberLoraView:
    """A Cast member presented as if the chosen LoRA were its own.

    Everything except the three overridden attributes reads through to the
    member. Used for rendering only; never add it to a session or write to it.
    """

    def __init__(self, subject: Any, option: LoraOption):
        self._subject = subject
        self.lora_path = option.lora_path
        self.trigger_word = option.trigger_word or getattr(subject, "trigger_word", None)
        raw = getattr(subject, "training_settings_json", None)
        settings = dict(raw) if isinstance(raw, dict) else {}
        settings["base_model_id"] = option.base_model_id
        self.training_settings_json = settings

    def __getattr__(self, name: str) -> Any:
        return getattr(self._subject, name)

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} subject={getattr(self._subject, 'id', None)} "
            f"base={self.training_settings_json.get('base_model_id')}>"
        )


def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _profile_id(base_model_id: Optional[str]) -> Optional[str]:
    from backend.services.media_model_registry import get_profile

    p = get_profile(base_model_id) if base_model_id else None
    return p["id"] if p else (base_model_id or None)


def _label(model_or_base: Optional[str]) -> str:
    from backend.services.media_model_registry import get_profile

    p = get_profile(model_or_base) if model_or_base else None
    return (p or {}).get("name") or (model_or_base or "this model")


def _member_name(subject: Any) -> str:
    return (
        getattr(subject, "name", None)
        or getattr(subject, "trigger_word", None)
        or f"Cast member {getattr(subject, 'id', '?')}"
    )


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def current_rows_by_base(rows: Iterable[Any]) -> dict[str, Any]:
    """The highest-version row per base, keyed by registry base id."""
    best: dict[str, Any] = {}
    for row in rows or []:
        base = _profile_id(_field(row, "base_model_id"))
        path = (_field(row, "lora_path") or "").strip()
        if not base or not path:
            continue
        version = int(_field(row, "version") or 0)
        held = best.get(base)
        if held is None or version > int(_field(held, "version") or 0):
            best[base] = row
    return best


def default_base(subject: Any, rows: Iterable[Any] = ()) -> Optional[str]:
    """Base of the member's default LoRA (``Subject.lora_path``), or None without one.

    Its row says first, then the file's sidecar, then the member's train base.
    """
    default_path = (getattr(subject, "lora_path", None) or "").strip()
    if not default_path:
        return None
    for row in rows or []:
        if (_field(row, "lora_path") or "").strip() == default_path:
            base = _profile_id(_field(row, "base_model_id"))
            if base:
                return base
    return _lora_file_base(default_path, subject)


def member_options(subject: Any, rows: Iterable[Any] = ()) -> dict[str, LoraOption]:
    """Every LoRA the member can render with, one per base.

    The default LoRA stands for its own base; rows supply the other bases.
    """
    rows = list(rows or [])
    options: dict[str, LoraOption] = {}
    for base, row in current_rows_by_base(rows).items():
        options[base] = LoraOption(
            base_model_id=base,
            lora_path=(_field(row, "lora_path") or "").strip(),
            trigger_word=_field(row, "trigger_word") or None,
            version=_field(row, "version"),
            source=_field(row, "source") or "trained",
        )
    default_path = (getattr(subject, "lora_path", None) or "").strip()
    base = default_base(subject, rows)
    if default_path and base:
        same = next(
            (r for r in rows if (_field(r, "lora_path") or "").strip() == default_path),
            None,
        )
        options[base] = LoraOption(
            base_model_id=base,
            lora_path=default_path,
            trigger_word=(_field(same, "trigger_word") or None) if same is not None else None,
            version=_field(same, "version") if same is not None else None,
            source=(_field(same, "source") or "trained") if same is not None else "default",
        )
    return options


def _describe_bases(options: dict[str, LoraOption]) -> str:
    return _join([_label(b) for b in options])


def select_cast_loras(
    subjects: Iterable[Any],
    image_model: Optional[str] = None,
    *,
    pin_paths: Sequence[str] = (),
    rows_by_subject: Optional[dict[int, list]] = None,
) -> CastLoraSelection:
    """Pick the LoRA each member renders with. Raises ``CastLoraRefusal``.

    ``image_model``: the model the person picked; Auto/None means none.
    ``pin_paths``: LoRA paths a caller asked for by name (the post-train check of a
    fresh LoRA, or paths an earlier selection returned). With no specific model, a
    pinned path that is one of a member's non-default LoRAs fixes the base.
    ``rows_by_subject``: ``SubjectLora`` rows per subject id; loaded from the
    database when not given.
    """
    from backend.services.media_model_registry import lora_compatible_with_inference

    members = list(subjects or [])
    if rows_by_subject is None:
        # Only database rows can hold SubjectLora rows; stand-ins stay as they are.
        rows_by_subject = load_subject_lora_rows(
            [s.id for s in members if hasattr(s, "_sa_instance_state") and s.id is not None]
        )
    trained = [
        s for s in members
        if (getattr(s, "lora_path", None) or "").strip()
        or rows_by_subject.get(getattr(s, "id", None))
    ]

    model = (image_model or "").strip().lower()
    has_rows = any(rows_by_subject.get(getattr(s, "id", None)) for s in trained)
    if not has_rows and model in AUTO_MODELS:
        return CastLoraSelection(subjects=members, base_model_id=None, legacy=True)

    options = {
        id(s): member_options(s, rows_by_subject.get(getattr(s, "id", None)) or [])
        for s in trained
    }
    member_paths = {o.lora_path for opts in options.values() for o in opts.values()}

    chosen: dict[int, LoraOption] = {}
    if model not in AUTO_MODELS:
        lacking = []
        for s in trained:
            match = next(
                (o for b, o in options[id(s)].items() if lora_compatible_with_inference(b, model)),
                None,
            )
            if match is None:
                lacking.append(s)
            else:
                chosen[id(s)] = match
        if lacking:
            raise CastLoraRefusal(_explicit_refusal(lacking, options, model))
        if not has_rows:
            # Every member's default matches the picked model: the render takes
            # the same route it always has.
            return CastLoraSelection(
                subjects=members, base_model_id=None, legacy=True, member_paths=member_paths
            )
    else:
        target = _pinned_base(trained, options, pin_paths) or _shared_base(trained, options)
        missing = [s for s in trained if target not in options[id(s)]]
        if missing:
            raise CastLoraRefusal(_mixed_refusal(trained, options))
        chosen = {id(s): options[id(s)][target] for s in trained}

    first = chosen[id(trained[0])] if trained else None
    out = [_MemberLoraView(s, chosen[id(s)]) if id(s) in chosen else s for s in members]
    return CastLoraSelection(
        subjects=out,
        base_model_id=first.base_model_id if first else None,
        legacy=False,
        member_paths=member_paths,
    )


def _pinned_base(trained: list, options: dict, pin_paths: Sequence[str]) -> Optional[str]:
    pins = {(p or "").strip() for p in pin_paths or [] if (p or "").strip()}
    if not pins:
        return None
    bases = []
    for s in trained:
        default_path = (getattr(s, "lora_path", None) or "").strip()
        for base, opt in options[id(s)].items():
            if opt.lora_path in pins and opt.lora_path != default_path and base not in bases:
                bases.append(base)
    if len(bases) > 1:
        raise CastLoraRefusal(
            "The LoRAs asked for were trained on different base models ("
            + _join([_label(b) for b in bases])
            + "), so they can't be used in one image."
        )
    return bases[0] if bases else None


def _shared_base(trained: list, options: dict) -> Optional[str]:
    if not trained:
        return None
    first = trained[0]
    first_opts = options[id(first)]
    default_path = (getattr(first, "lora_path", None) or "").strip()
    order = [b for b, o in first_opts.items() if o.lora_path == default_path]
    order.extend(sorted(b for b in first_opts if b not in order))
    for base in order:
        if all(base in options[id(s)] for s in trained):
            return base
    return order[0] if order else None


def _explicit_refusal(lacking: list, options: dict, model: str) -> str:
    wanted = _label(model)
    parts = []
    for s in lacking:
        opts = options[id(s)]
        if len(opts) == 1:
            have = f"a LoRA for {_describe_bases(opts)} only"
        else:
            have = f"LoRAs for {_describe_bases(opts)} only"
        parts.append(f"{_member_name(s)} has {have}, not for {wanted}.")
    return " ".join(parts) + " Pick a model each character has a LoRA for, or Auto."


def _mixed_refusal(trained: list, options: dict) -> str:
    listed = [f"{_member_name(s)} ({_describe_bases(options[id(s)])})" for s in trained]
    return (
        f"{_join(listed)} have no LoRA for the same base model, so they can't be "
        "rendered in one image. Render them separately, or give one of them a LoRA "
        "for a base the others have."
    )


def selected_lora_paths(selection: CastLoraSelection) -> list[str]:
    """LoRA paths of the chosen members, in member order, without repeats."""
    out: list[str] = []
    for s in selection.subjects:
        p = (getattr(s, "lora_path", None) or "").strip()
        if p and p not in out:
            out.append(p)
    return out


def load_subject_lora_rows(subject_ids: Sequence[Any]) -> dict[int, list[dict]]:
    """``SubjectLora`` rows per subject id as plain dicts; {} when none or on error.

    A failed read leaves members on their default LoRA, the behaviour before rows
    existed. The read uses its own connection, so it neither flushes the caller's
    pending changes nor aborts the caller's transaction when it fails; rows
    written in the caller's uncommitted transaction are not seen.
    """
    ids = []
    for sid in subject_ids or []:
        try:
            ids.append(int(sid))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    from sqlalchemy import select

    from backend.models import SubjectLora, db

    table = SubjectLora.__table__

    def _load() -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        with db.engine.connect() as conn:
            rows = conn.execute(select(table).where(table.c.subject_id.in_(ids))).mappings().all()
        for r in rows:
            out.setdefault(r["subject_id"], []).append(dict(r))
        return out

    try:
        from flask import has_app_context
        if has_app_context():
            return _load()
        from backend.app import get_or_create_app
        app = get_or_create_app()
        with app.app_context():
            try:
                return _load()
            finally:
                db.session.remove()
    except Exception as e:
        log.warning("subject_loras read failed for %s; using default LoRAs: %s", ids, e)
        return {}


def _lora_file_base(path: Optional[str], subject: Any) -> Optional[str]:
    """Base a LoRA file was made for: its sidecar first, then the member's base."""
    from backend.services.media_model_registry import read_lora_sidecar, subject_base_model_id

    meta = read_lora_sidecar(path) or {}
    if meta.get("base_model_id"):
        return _profile_id(str(meta["base_model_id"]))
    return _profile_id(subject_base_model_id(subject))


def record_subject_lora(
    subject: Any,
    base_model_id: str,
    lora_path: str,
    *,
    source: str = "trained",
    trigger_word: Optional[str] = None,
    make_default: Optional[bool] = None,
):
    """Add the next version of ``subject``'s LoRA for ``base_model_id``. Caller commits.

    ``make_default``: True makes it the member's default; False never does; None
    (imports) does only when the member has no default yet or the default is a
    LoRA for this same base. A default being replaced that predates the table is
    recorded as a row first, so its base stays available.
    """
    from backend.models import SubjectLora, db

    base = _profile_id(base_model_id) or base_model_id
    existing = SubjectLora.query.filter_by(subject_id=subject.id).all()
    old_default = (getattr(subject, "lora_path", None) or "").strip()
    old_base = default_base(subject, existing) if old_default else None

    if make_default is None:
        make_default = not old_default or old_base == base
    if make_default and old_default and old_default != lora_path and not any(
        (r.lora_path or "").strip() == old_default for r in existing
    ):
        prior_base = _lora_file_base(old_default, subject) or old_base
        if prior_base:
            prior_version = 1 + max(
                (r.version or 0 for r in existing if _profile_id(r.base_model_id) == prior_base),
                default=0,
            )
            prior = SubjectLora(
                subject_id=subject.id,
                base_model_id=prior_base,
                lora_path=old_default,
                version=prior_version,
                trigger_word=getattr(subject, "trigger_word", None),
                source="trained",
            )
            db.session.add(prior)
            existing.append(prior)

    version = 1 + max(
        (r.version or 0 for r in existing if _profile_id(r.base_model_id) == base),
        default=0,
    )
    row = SubjectLora(
        subject_id=subject.id,
        base_model_id=base,
        lora_path=str(lora_path),
        version=version,
        trigger_word=trigger_word or getattr(subject, "trigger_word", None),
        source=source,
    )
    db.session.add(row)
    if make_default:
        subject.lora_path = str(lora_path)
    return row


def set_default_lora(subject: Any, base_model_id: str):
    """Make the member's current LoRA for ``base_model_id`` its default. Caller commits.

    Raises ``CastLoraRefusal`` when the member has no LoRA for that base.
    """
    from backend.models import SubjectLora

    base = _profile_id(base_model_id) or base_model_id
    rows = SubjectLora.query.filter_by(subject_id=subject.id).all()
    row = current_rows_by_base(rows).get(base)
    if row is None:
        raise CastLoraRefusal(f"{_member_name(subject)} has no LoRA for {_label(base)}.")
    subject.lora_path = row.lora_path
    return row
