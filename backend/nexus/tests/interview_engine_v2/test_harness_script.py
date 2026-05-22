"""Pure helpers behind the M3 harness: the bank script + v2 keyterm assembler."""

from app.modules.interview_engine_v2.agent import (
    BankScript,
    assemble_v2_keyterms,
)


def test_bank_script_advances_then_finishes():
    script = BankScript(intro="Hi, I'm Sam.", questions=["Q1?", "Q2?"], closing="Thanks!")
    assert script.next_line() == "Hi, I'm Sam."   # intro first
    assert script.next_line() == "Q1?"
    assert script.next_line() == "Q2?"
    assert script.next_line() == "Thanks!"        # closing
    assert script.is_terminal_line is True
    assert script.next_line() is None              # nothing after close


def test_bank_script_empty_bank_goes_intro_then_close():
    script = BankScript(intro="Hi.", questions=[], closing="Bye.")
    assert script.next_line() == "Hi."
    assert script.next_line() == "Bye."
    assert script.is_terminal_line is True


def test_assemble_v2_keyterms_dedup_and_cap():
    terms = assemble_v2_keyterms(candidate_first_name="Ravi", bank_keyterms=["Workato", "ravi", "iPaaS"])
    assert terms[0] == "Ravi"                 # candidate name first
    assert "Workato" in terms and "iPaaS" in terms
    # case-insensitive dedup: "ravi" collides with "Ravi"
    assert sum(1 for t in terms if t.lower() == "ravi") == 1
    assert len(terms) <= 50
