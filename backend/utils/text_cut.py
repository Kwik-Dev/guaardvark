"""Character budgets that end on whitespace."""


def cut_on_whitespace(text: str, limit: int) -> str:
    """The first ``limit`` characters of ``text``, ending on whitespace.

    A cut inside a token hands the model half a value: "16:9" clipped to
    "16:" was copied into an answer as a ratio. The cut backs up to
    the last space when that keeps at least half the budget; otherwise
    (no spaces, one huge token) the plain slice stands.
    """
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return head[:space] if space > limit // 2 else head
