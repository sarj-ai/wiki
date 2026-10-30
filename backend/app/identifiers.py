from typing import NewType
import uuid


# NewType prevents cross-resource swaps without changing runtime values; opaque Google IDs remain validated at boundaries.
FileId = NewType("FileId", str)
DriveId = NewType("DriveId", str)
GoogleUserId = NewType("GoogleUserId", str)
UserId = NewType("UserId", uuid.UUID)
AuthSessionId = NewType("AuthSessionId", uuid.UUID)
AuthSessionFamilyId = NewType("AuthSessionFamilyId", uuid.UUID)
TokenId = NewType("TokenId", uuid.UUID)
TaskId = NewType("TaskId", uuid.UUID)
IdempotencyKey = NewType("IdempotencyKey", uuid.UUID)
QaPlanId = NewType("QaPlanId", uuid.UUID)
QaRunId = NewType("QaRunId", uuid.UUID)
DocsRevisionId = NewType("DocsRevisionId", str)
DocsTabId = NewType("DocsTabId", str)
DocsSegmentId = NewType("DocsSegmentId", str)
BrandProfileId = NewType("BrandProfileId", str)
ModelId = NewType("ModelId", str)
