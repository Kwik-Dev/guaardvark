from backend.utils.text_cut import cut_on_whitespace


def test_short_text_is_untouched():
    assert cut_on_whitespace("slope 4:12 max", 100) == "slope 4:12 max"


def test_cut_backs_up_to_whitespace_so_a_value_survives_whole():
    text = "The roof pitch limit is 4:12 for this shingle"
    limit = text.index("4:12") + 3  # a hard slice would leave "4:1"
    out = cut_on_whitespace(text, limit)
    assert out.endswith("limit is")
    assert "4:1" not in out


def test_no_whitespace_falls_back_to_the_hard_cut():
    assert cut_on_whitespace("a" * 50, 10) == "a" * 10


def test_space_too_early_keeps_the_hard_cut():
    text = "ab " + "c" * 40
    assert cut_on_whitespace(text, 20) == text[:20]


def test_none_and_zero_limit():
    assert cut_on_whitespace(None, 5) == ""
    assert cut_on_whitespace("abc def", 0) == "abc def"
