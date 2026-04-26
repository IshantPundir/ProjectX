"""Custom exceptions for the pipelines module."""


class StarterKeyNotFoundError(Exception):
    """Raised when a request references an unknown starter_key."""

    def __init__(self, starter_key: str) -> None:
        self.starter_key = starter_key
        super().__init__(f"Unknown starter_key: {starter_key}")


class CannotDeleteDefaultError(Exception):
    """Raised when attempting to delete the default template."""

    def __init__(self) -> None:
        super().__init__(
            "Cannot delete the default template. Set another template as "
            "default first, then delete this one."
        )


class NoSourceTemplateError(Exception):
    """Raised when attempting to reset/update-source with no source."""

    def __init__(self) -> None:
        super().__init__(
            "This pipeline has no source template (built from scratch). "
            "Nothing to reset or update."
        )


class JobNotInConfirmedStateError(Exception):
    """Raised when attempting to create a pipeline for a job not in signals_confirmed."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Pipelines can only be created for jobs in signals_confirmed state. "
            f"This job is in '{status}'."
        )


class PipelineAlreadyExistsError(Exception):
    """Raised when trying to POST /pipeline for a job that already has one."""

    def __init__(self) -> None:
        super().__init__(
            "This job already has a pipeline instance. Use PATCH to update it."
        )


class StagePauseForbiddenError(Exception):
    """Raised when attempting to pause an intake or debrief stage."""

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"Cannot pause stage of type '{stage_type}'")
