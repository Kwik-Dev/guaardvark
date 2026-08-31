"""A long page is cut around what the question asks about, not at its head."""

from backend.utils.text_cut import cut_on_whitespace
from backend.utils.text_focus import focus_window, query_terms

MENU = " ".join(f"Menu item {i} Products Support Downloads" for i in range(120))
SPEC = "The model K20 bracket kit fits frames up to 48 inches and needs four M6 bolts."
PAGE = f"{MENU} {SPEC} " + "Footer text " * 50


def test_a_passage_past_the_head_reaches_the_window():
    assert SPEC not in cut_on_whitespace(PAGE, 2000)
    window = focus_window(PAGE, "Which bolts does the K20 bracket kit need?", 600)
    assert SPEC in window and window.startswith("… ") and len(window) <= 600


def test_short_text_no_terms_or_no_match_keeps_the_head():
    assert focus_window("short page", "bolts", 100) == "short page"
    assert focus_window(PAGE, "what is the", 300) == cut_on_whitespace(PAGE, 300)
    assert focus_window(PAGE, "zeppelin", 300) == cut_on_whitespace(PAGE, 300)
    assert focus_window(None, "bolts", 300) == ""


def test_the_window_with_the_most_distinct_terms_wins_and_ties_go_to_the_earliest():
    text = "alpha " + "x " * 400 + "alpha beta " + "y " * 400 + "alpha beta gamma"
    assert "gamma" in focus_window(text, "alpha beta gamma", 60)
    tied = "alpha " + "z " * 600 + "alpha"
    assert focus_window(tied, "alpha", 50) == cut_on_whitespace(tied, 50)


def test_a_passage_beats_a_menu_that_repeats_the_same_words():
    menu = " ".join(f"Brackets Kits Frames Accessories {i}" for i in range(90))
    spec = "Which kit: the K20 bracket kit fits frames up to 48 inches and needs four M6 bolts."
    text = f"{menu} {spec} " + "Footer " * 40
    window = focus_window(text, "Which bolts does the K20 bracket kit need for these frames?", 700)
    assert spec in window
    assert window.index(spec) < 400


def test_anchors_count_like_query_terms():
    text = "x " * 500 + "part ELN-01 fits" + " y" * 500
    assert "ELN-01" in focus_window(text, "", 80, anchors=["ELN-01"])


def test_query_terms_drop_short_words_and_stopwords():
    assert query_terms("What is the K20 kit for?") == ["k20", "kit"]
