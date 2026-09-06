import traceback
from typing import override

from cryptography.fernet import Fernet
from pydantic import ValidationError
import pytest

from app.mcp.protection import InvalidOAuthStateError, OAuthStateProtector
from app.mcp.schemas import DocumentExcerpt, SearchInput


_SENTINEL = "synthetic-private-value"


class _PrivateValue:
    @override
    def __repr__(self) -> str:
        return _SENTINEL


@pytest.mark.parametrize("model", [SearchInput, DocumentExcerpt])
def test_model_error_string_hides_input(model: type[SearchInput | DocumentExcerpt]) -> None:
    with pytest.raises(ValidationError) as caught:
        model.model_validate({"unexpected": _SENTINEL})
    assert _SENTINEL not in str(caught.value)
    # Pydantic's structured errors remain sensitive: transport must sanitize them.
    assert _SENTINEL in str(caught.value.errors())


def test_protect_error_hides_secret_repr_and_traceback_chain() -> None:
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    with pytest.raises(InvalidOAuthStateError, match="Invalid OAuth state") as caught:
        protector.protect("namespace", "key", {"access_token": _PrivateValue()})
    assert _SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_unprotect_error_hides_invalid_plaintext_and_traceback_chain() -> None:
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    malformed = (
        protector._fernets[0]
        .encrypt(
            b'{"version":1,"collection":"namespace","key":"key",'
            b'"value":{"private":"synthetic-private-value"}'
        )
        .decode()
    )
    with pytest.raises(InvalidOAuthStateError, match="Invalid OAuth state") as caught:
        protector.unprotect("namespace", "key", malformed)
    assert _SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
