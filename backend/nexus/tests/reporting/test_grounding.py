from app.modules.reporting.scoring.grounding import ground_quotes, is_grounded

TRANSCRIPT = "I would take this up at Java. Already given you the answer."

def test_exact_substring_is_grounded():
    assert is_grounded("take this up at Java", TRANSCRIPT) is True

def test_whitespace_and_case_normalized():
    assert is_grounded("ALREADY   given you  the answer", TRANSCRIPT) is True

def test_hallucinated_quote_is_not_grounded():
    assert is_grounded("I have deep Kubernetes expertise", TRANSCRIPT) is False

def test_ground_quotes_partitions():
    grounded, ungrounded = ground_quotes(
        ["take this up at Java", "I led a 200-person team"], TRANSCRIPT)
    assert grounded == ["take this up at Java"]
    assert ungrounded == ["I led a 200-person team"]
