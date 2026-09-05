class TaskError(Exception):
    code = "task_error"
    http_status = 500


class TaskNotFoundError(TaskError):
    code = "task_not_found"
    http_status = 404

    def __init__(self) -> None:
        super().__init__("Task not found")


class TaskIdempotencyConflictError(TaskError):
    code = "task_idempotency_conflict"
    http_status = 409

    def __init__(self) -> None:
        super().__init__("Idempotency key was already used for different input")


class TaskTargetBusyError(TaskError):
    code = "document_transform_target_busy"
    http_status = 409

    def __init__(self) -> None:
        super().__init__("This document already has an active transformation")


class TaskAdmissionLimitError(TaskError):
    code = "document_transform_admission_limit"
    http_status = 429

    def __init__(self) -> None:
        super().__init__("Too many active document transformations")


class TaskNotClaimableError(TaskError):
    code = "task_not_claimable"
    http_status = 409

    def __init__(self) -> None:
        super().__init__("Task is already running or terminal")


class TaskLeaseLostError(TaskError):
    code = "task_lease_lost"
    http_status = 409

    def __init__(self) -> None:
        super().__init__("Task execution lease is no longer owned by this executor")


class TaskEnqueueError(TaskError):
    code = "task_enqueue_failed"
    http_status = 503

    def __init__(self) -> None:
        super().__init__("Task could not be queued; retry with the same idempotency key")


class TaskExecutionDeferredError(TaskError):
    code = "task_execution_deferred"
    http_status = 503

    def __init__(self) -> None:
        super().__init__("Task execution will be retried")
