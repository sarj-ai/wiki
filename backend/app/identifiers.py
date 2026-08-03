from typing import NewType
import uuid


# NewType prevents cross-resource swaps without changing runtime values; opaque Google IDs remain validated at boundaries.
FileId = NewType("FileId", str)
DriveId = NewType("DriveId", str)
GoogleUserId = NewType("GoogleUserId", str)
TaskId = NewType("TaskId", uuid.UUID)
IdempotencyKey = NewType("IdempotencyKey", uuid.UUID)
