from typing import NewType


# Nominal IDs catch accidental cross-resource calls during static analysis while
# remaining exactly ``str`` at runtime.  Keep validation at API/provider
# boundaries: Google IDs are opaque and have no documented format to reimplement.
# Folders deliberately use FileId because Drive models folders as files.  The
# app's sole UUID identity domain is User.id, so UUID already brands it strongly.
FileId = NewType("FileId", str)
DriveId = NewType("DriveId", str)
GoogleUserId = NewType("GoogleUserId", str)
