from backend.utils.text_cut import cut_on_whitespace


def test_short_text_is_untouched():
    assert cut_on_whitespace("aspect 16:9 max", 100) == "aspect 16:9 max"


def test_cut_backs_up_to_whitespace_so_a_value_survives_whole():
    text = "The render preset is 16:9 for this clip"
    limit = text.index("16:9") + 3  # a hard slice would leave "16:"
    out = cut_on_whitespace(text, limit)
    assert out.endswith("preset is")
    assert "16:" not in out


def test_no_whitespace_falls_back_to_the_hard_cut():
    assert cut_on_whitespace("a" * 50, 10) == "a" * 10


def test_space_too_early_keeps_the_hard_cut():
    text = "ab " + "c" * 40
    assert cut_on_whitespace(text, 20) == text[:20]


def test_none_and_zero_limit():
    assert cut_on_whitespace(None, 5) == ""
    assert cut_on_whitespace("abc def", 0) == "abc def"
