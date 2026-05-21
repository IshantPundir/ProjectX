"""Directive contract — implemented in Task 6/7."""
from enum import Enum


class DirectiveAct(str, Enum):  # filled in Task 6
    INTRO = "INTRO"


class DirectiveTone(str, Enum):  # filled in Task 6
    WARM = "WARM"


class Directive:  # replaced by a Pydantic model in Task 6
    pass
