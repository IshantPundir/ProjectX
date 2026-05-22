"""Unit tests for service.order_mandatory_first.

Pure, deterministic ordering helper: mandatory (knockout) questions sort to the
front, preserving each group's existing relative position order. This is the
stored-position reorder applied in both _generate_one_bank and
regenerate_kind_actor Phase C so the recruiter UI matches the runtime
mandatory-first ask order (build_session_config orders is_mandatory DESC,
position ASC at runtime — unchanged).
"""

from types import SimpleNamespace

from app.modules.question_bank.service import order_mandatory_first


def _q(position: int, is_mandatory: bool) -> SimpleNamespace:
    """Lightweight stand-in carrying just the two attributes the helper reads."""
    return SimpleNamespace(position=position, is_mandatory=is_mandatory)


def test_mandatory_questions_sort_to_front_preserving_relative_order():
    """A non-knockout-first input lands mandatory-first, each group's order intact."""
    # Input order: [optional@0, mandatory@1, optional@2, mandatory@3]
    optional_a = _q(0, is_mandatory=False)
    mandatory_a = _q(1, is_mandatory=True)
    optional_b = _q(2, is_mandatory=False)
    mandatory_b = _q(3, is_mandatory=True)
    questions = [optional_a, mandatory_a, optional_b, mandatory_b]

    result = order_mandatory_first(questions)

    # The two mandatory questions come first, in their original relative order
    # (mandatory_a was at position 1, mandatory_b at position 3 → a before b).
    assert result[0] is mandatory_a
    assert result[1] is mandatory_b
    # Then the two optionals, in their original relative order.
    assert result[2] is optional_a
    assert result[3] is optional_b


def test_stable_within_mandatory_group():
    """Multiple mandatory questions keep their existing position order (stable)."""
    m0 = _q(0, is_mandatory=True)
    m5 = _q(5, is_mandatory=True)
    m2 = _q(2, is_mandatory=True)
    # Passed out of position order; helper sorts by position within the group.
    result = order_mandatory_first([m5, m0, m2])
    assert result == [m0, m2, m5]


def test_stable_within_optional_group():
    """Multiple optional questions keep their existing position order (stable)."""
    o3 = _q(3, is_mandatory=False)
    o1 = _q(1, is_mandatory=False)
    o7 = _q(7, is_mandatory=False)
    result = order_mandatory_first([o7, o3, o1])
    assert result == [o1, o3, o7]


def test_all_mandatory_is_identity_by_position():
    """All-mandatory input just sorts by position; relative order preserved."""
    a = _q(0, is_mandatory=True)
    b = _q(1, is_mandatory=True)
    c = _q(2, is_mandatory=True)
    assert order_mandatory_first([a, b, c]) == [a, b, c]


def test_all_optional_is_identity_by_position():
    """All-optional input just sorts by position; relative order preserved."""
    a = _q(0, is_mandatory=False)
    b = _q(1, is_mandatory=False)
    c = _q(2, is_mandatory=False)
    assert order_mandatory_first([a, b, c]) == [a, b, c]


def test_empty_list():
    assert order_mandatory_first([]) == []


def test_does_not_mutate_input():
    """sorted() returns a new list; the input order is untouched."""
    optional = _q(0, is_mandatory=False)
    mandatory = _q(1, is_mandatory=True)
    questions = [optional, mandatory]
    result = order_mandatory_first(questions)
    # Input list order is unchanged (helper is non-mutating).
    assert questions == [optional, mandatory]
    # Result is reordered mandatory-first.
    assert result == [mandatory, optional]
