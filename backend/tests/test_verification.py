import pytest

from app.document_transform.verification import BrandVerification


@pytest.mark.parametrize(
    ("brand", "required", "accepted"),
    [
        (BrandVerification.NOT_REQUIRED, False, True),
        (BrandVerification.NOT_REQUIRED, True, False),
        (BrandVerification.FULL, False, True),
        (BrandVerification.FULL, True, True),
        (BrandVerification.SUPPORTED, False, True),
        (BrandVerification.SUPPORTED, True, True),
        (BrandVerification.MISMATCH, False, False),
        (BrandVerification.MISMATCH, True, False),
    ],
)
def test_brand_admission_requires_evidence_when_requested(
    brand: BrandVerification, *, required: bool, accepted: bool
) -> None:
    assert brand.acceptable(required=required) is accepted
