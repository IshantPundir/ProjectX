"""Disallowed-phrase regex check for Speech Agent output.

Every string the Orchestrator hands to ``session.say()`` passes through
``check_safety`` first. Violations cause the Speech Agent (Phase C) to
either retry with a stricter instruction or fall back to a static safe
utterance — but the safety check itself is here in A.5 so the surface
is testable in isolation before any LLM is wired.

Categories (per design doc §11.5 + impl prompt §A.5):

* **Outcome words** — anything implying the candidate has been judged.
  ``passed``, ``failed``, ``rejected``, ``advanced``, ``best of luck``,
  ``unfortunately``, ``thanks for your interest``. The agent collects;
  the Report Builder decides. Outcome-implying language at the live
  agent layer is a hard violation.

* **Salary commitments** — specific numbers that imply a confirmed
  offer (``$50,000``, ``$80k``, ``80,000 USD``). The recruiting team
  handles compensation; the agent's deflection template must not
  signal a number.

* **Scheduling / hiring-manager promises** — specific commitments that
  imply downstream-process knowledge the agent doesn't have
  (``I'll schedule``, ``next interview is on``, ``the hiring
  manager``). Same reason: those are recruiter-team concerns.

False-positive policy: word-boundary regex catches every use, even
incidental ones (``the file passed through the validator``). Rationale
in §11.5: false-negatives leak outcome to the candidate (catastrophic);
false-positives produce odd word choices in 1-in-N sessions
(easily fixed by template iteration). Catch all and tune.

Public API:

* ``check_safety(text) -> SafetyResult`` — pure function, no I/O.
* ``SafetyResult.is_safe: bool`` and ``SafetyResult.violations``.
* ``SafetyViolation`` carries a category name + the matched span so
  the audit envelope can record both without retaining the full
  rendered output (PII discipline).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ViolationCategory = Literal["outcome", "salary", "scheduling"]


@dataclass(frozen=True)
class SafetyViolation:
    """A single disallowed-phrase hit in a rendered utterance."""

    category: ViolationCategory
    pattern_name: str  # short identifier for the specific rule
    matched_text: str  # the actual span that matched (logged hashed only at INFO)


@dataclass(frozen=True)
class SafetyResult:
    is_safe: bool
    violations: tuple[SafetyViolation, ...]


# ---------------------------------------------------------------------------
# Outcome words — any use is a violation. Word boundaries keep us out of
# verb-tense weirdness with prefixes/suffixes ("repassed", "antifailure",
# etc.) but accept incidental usage (e.g. "the file passed through the
# validator") on purpose — see false-positive policy in module docstring.
# ---------------------------------------------------------------------------

_OUTCOME_RULES: tuple[tuple[str, str], ...] = (
    ("outcome.passed", r"\bpassed\b"),
    ("outcome.failed", r"\bfailed\b"),
    ("outcome.rejected", r"\brejected\b"),
    ("outcome.advanced", r"\badvanced\b"),
    ("outcome.unfortunately", r"\bunfortunately\b"),
    ("outcome.best_of_luck", r"\bbest of luck\b"),
    ("outcome.thanks_for_interest", r"\bthanks for your interest\b"),
)

# ---------------------------------------------------------------------------
# Salary numbers — currency + comma-separated thousands or k-suffix.
# ---------------------------------------------------------------------------

_SALARY_RULES: tuple[tuple[str, str], ...] = (
    # $50,000 / $100,000 / £45,000 / €40,000
    ("salary.currency_with_thousands", r"[\$£€]\s?\d{2,3}(,\d{3})+\b"),
    # $80k / £75k
    ("salary.currency_with_k_suffix", r"[\$£€]\s?\d{2,3}\s?k\b"),
    # 80,000 USD / 100,000 GBP / 60000 dollars
    ("salary.bare_number_with_currency_word",
     r"\b\d{2,3}(,?\d{3})\s?(USD|EUR|GBP|INR|CAD|AUD|dollars|pounds|euros)\b"),
)

# ---------------------------------------------------------------------------
# Scheduling / hiring-manager commitments — first-person promises about
# downstream process. Catches both ``I'll`` and ``we'll`` flavours.
# ---------------------------------------------------------------------------

_SCHEDULING_RULES: tuple[tuple[str, str], ...] = (
    ("scheduling.commit_to_schedule",
     r"\b(I'?ll|we'?ll|I will|we will)\s+(schedule|book|set up|arrange)\b"),
    ("scheduling.next_round_specifics",
     r"\bnext\s+(interview|round|stage)\s+(is|will be)\s+(on|at|scheduled for)\b"),
    ("scheduling.hiring_manager_reference",
     r"\bthe (hiring manager|interviewer|recruiter)\s+(is|will|said|told|wants)\b"),
)


def _compile_category(
    category: ViolationCategory,
    rules: tuple[tuple[str, str], ...],
) -> tuple[tuple[ViolationCategory, str, re.Pattern[str]], ...]:
    return tuple(
        (category, name, re.compile(pattern, re.IGNORECASE))
        for name, pattern in rules
    )


_ALL_RULES: tuple[tuple[ViolationCategory, str, re.Pattern[str]], ...] = (
    *_compile_category("outcome", _OUTCOME_RULES),
    *_compile_category("salary", _SALARY_RULES),
    *_compile_category("scheduling", _SCHEDULING_RULES),
)


def check_safety(text: str) -> SafetyResult:
    """Scan ``text`` for disallowed phrases.

    Returns a ``SafetyResult`` with every match (not just the first) so
    the caller can decide retry vs. fallback based on the violation
    profile. Each pattern fires at most once per call (`re.search`,
    not `re.findall`) — repeated occurrences of the same phrase are
    one violation, not many.
    """
    violations: list[SafetyViolation] = []
    for category, name, pattern in _ALL_RULES:
        match = pattern.search(text)
        if match:
            violations.append(
                SafetyViolation(
                    category=category,
                    pattern_name=name,
                    matched_text=match.group(0),
                )
            )
    return SafetyResult(
        is_safe=not violations,
        violations=tuple(violations),
    )
