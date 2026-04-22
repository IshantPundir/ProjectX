"""Starter pack — hand-written pipeline templates shipped with the product.

These are NOT stored in the database. When a recruiter clicks "Use this starter"
they get a COPY in their org unit's template library (which IS persisted).

The system fallback is used by auto_apply_pipeline_on_confirmation when neither
last-used nor org-unit-default exist."""

from typing import Any, Final

STARTER_TEMPLATES: Final[dict[str, dict[str, Any]]] = {
    "standard_technical": {
        "name": "Standard Technical",
        "description": "Default pipeline for engineering and technical roles: phone screen, AI deep interview, panel review.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 10,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 1,
                "name": "AI Technical Interview",
                "stage_type": "ai_screening",
                "duration_minutes": 45,
                "difficulty": "hard",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "score_threshold", "threshold": 70},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 2,
                "name": "Hiring Manager Panel",
                "stage_type": "human_interview",
                "duration_minutes": 60,
                "difficulty": "medium",
                "signal_filter": {
                    "include_types": ["competency", "experience", "behavioral"],
                },
                "pass_criteria": {"type": "manual_review"},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "fast_track": {
        "name": "Fast Track",
        "description": "Accelerated 2-stage pipeline for urgent backfills — phone screen then AI interview.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 10,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 1,
                "name": "AI Interview",
                "stage_type": "ai_screening",
                "duration_minutes": 30,
                "difficulty": "medium",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "score_threshold", "threshold": 65},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "screening_only": {
        "name": "Screening Only",
        "description": "Phone screen only — client takes over after qualifier.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 15,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "senior_leadership": {
        "name": "Senior Leadership",
        "description": "Extended 4-stage pipeline for Staff+, Principal, and Director roles.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 15,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 1,
                "name": "AI Technical Interview",
                "stage_type": "ai_screening",
                "duration_minutes": 60,
                "difficulty": "hard",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "score_threshold", "threshold": 75},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 2,
                "name": "Hiring Manager Panel",
                "stage_type": "human_interview",
                "duration_minutes": 60,
                "difficulty": "medium",
                "signal_filter": {
                    "include_types": ["competency", "experience", "behavioral"],
                },
                "pass_criteria": {"type": "manual_review"},
                "advance_behavior": "manual_review",
            },
            {
                "position": 3,
                "name": "Executive Interview",
                "stage_type": "human_interview",
                "duration_minutes": 45,
                "difficulty": "medium",
                "signal_filter": {
                    "include_types": ["competency", "experience", "behavioral"],
                },
                "pass_criteria": {"type": "manual_review"},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "sales_commercial": {
        "name": "Sales & Commercial",
        "description": "2-stage pipeline for sales, BD, and commercial roles.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 10,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 1,
                "name": "Human Interview",
                "stage_type": "human_interview",
                "duration_minutes": 45,
                "difficulty": "medium",
                "signal_filter": {
                    "include_types": ["competency", "experience", "behavioral"],
                },
                "pass_criteria": {"type": "manual_review"},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "volume_hiring": {
        "name": "Volume Hiring",
        "description": "Single-stage phone screen for high-volume roles (ops, customer service, retail).",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 8,
                "difficulty": "easy",
                "signal_filter": {
                    "include_types": ["competency", "experience"],
                },
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
        ],
    },
}

SYSTEM_FALLBACK_STARTER: Final[str] = "standard_technical"
