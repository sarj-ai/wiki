from enum import StrEnum
from typing import Literal, assert_never


class BrandVerification(StrEnum):
    NOT_REQUIRED = "not_required"
    FULL = "full"
    SUPPORTED = "supported"
    MISMATCH = "mismatch"

    def acceptable(self, *, required: bool) -> bool:
        match self:
            case BrandVerification.FULL | BrandVerification.SUPPORTED:
                return True
            case BrandVerification.NOT_REQUIRED:
                return not required
            case BrandVerification.MISMATCH:
                return False
            case _:
                assert_never(self)


type RequestedBrandVerification = Literal[
    BrandVerification.NOT_REQUIRED, BrandVerification.FULL, BrandVerification.MISMATCH
]
type ExistingBrandVerification = Literal[
    BrandVerification.FULL, BrandVerification.SUPPORTED, BrandVerification.MISMATCH
]
