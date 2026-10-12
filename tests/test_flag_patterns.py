from scraper.flag_patterns import flag_comment


def test_flag_comment_detects_pattern():
    flagged, reason, categories, severity = flag_comment("This includes 1488 code")
    assert flagged is True
    assert "white_nationalist" in categories
    assert severity == "high"
    assert reason


def test_flag_comment_handles_clean_text():
    flagged, reason, categories, severity = flag_comment("ordinary synthetic sample text")
    assert flagged is False
    assert reason == ""
    assert categories == ""
    assert severity == "low"
