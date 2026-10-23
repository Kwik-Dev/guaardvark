"""Chat session ids must fit the column they are saved in.

LLMSession.id is a 36-character column (a UUID). A longer id used to be
answered but never saved: the insert failed, the chat carried on with a
throwaway session, and every message was lost. Intake routes refuse it
instead, so the caller hears why.
"""
from typing import Optional


def max_session_id_length() -> int:
    from backend.models import LLMSession

    return LLMSession.id.type.length or 36


def session_id_refusal(session_id) -> Optional[str]:
    """Why ``session_id`` cannot be used for a saved conversation, or None."""
    if session_id is None or session_id == "":
        return None
    limit = max_session_id_length()
    if len(str(session_id)) > limit:
        return (f"session_id is {len(str(session_id))} characters; a conversation is saved only "
                f"with a session_id of at most {limit}")
    return None
