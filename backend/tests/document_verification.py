from dataclasses import dataclass

from app.document_transform.verification import BrandVerification


@dataclass(frozen=True, slots=True)
class VerificationFacts:
    content_preserved: bool = True
    structure_preserved: bool = True
    brand: BrandVerification = BrandVerification.FULL
