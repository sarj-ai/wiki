from pydantic import JsonValue, ValidationError
import pytest

from app.drive.google_schemas import (
    GoogleFileCapabilities,
    GoogleFilePermissionMetadata,
    GooglePermission,
)


@pytest.mark.parametrize("invalid", [0, 1, "true", "false", "yes", "no", None])
@pytest.mark.parametrize("capability", ["canEdit", "canAddChildren"])
def test_provider_capability_requires_an_actual_boolean(
    capability: str, invalid: JsonValue
) -> None:
    with pytest.raises(ValidationError):
        GoogleFileCapabilities.model_validate({capability: invalid})


@pytest.mark.parametrize("flag", ["ownedByMe", "shared"])
@pytest.mark.parametrize("invalid", [0, 1, "true", "false", None])
def test_permission_metadata_never_coerces_ownership_or_sharing(
    flag: str, invalid: JsonValue
) -> None:
    payload = {
        "id": "file",
        "mimeType": "application/vnd.google-apps.document",
        "version": "17",
        "ownedByMe": True,
        "shared": False,
        "capabilities": {"canAddChildren": False},
    }
    assert GoogleFilePermissionMetadata.model_validate(payload).version == "17"
    with pytest.raises(ValidationError):
        GoogleFilePermissionMetadata.model_validate({**payload, flag: invalid})


@pytest.mark.parametrize("invalid", [0, 1, "true", "false", None])
def test_effective_permission_details_require_boolean_inheritance(invalid: JsonValue) -> None:
    with pytest.raises(ValidationError):
        GooglePermission.model_validate(
            {
                "id": "permission",
                "type": "user",
                "role": "owner",
                "permissionDetails": [
                    {"permissionType": "file", "role": "owner", "inherited": invalid}
                ],
            }
        )
