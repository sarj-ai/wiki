from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from google.genai import types as genai_types
from pydantic import BaseModel, ConfigDict, JsonValue, PositiveInt, model_validator

from app.identifiers import ModelCallId, ModelId, ModelRequestHash


type ModelOperation = Literal["unslop", "review", "readability"]
type ModelThinkingLevel = Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"]

# Every field the document pipeline may set. Anything else would silently be
# dropped from a batch line, so an unknown field is rejected at the boundary.
_SUPPORTED_CONFIG_FIELDS = frozenset(
    {
        "system_instruction",
        "response_mime_type",
        "response_json_schema",
        "max_output_tokens",
        "thinking_config",
        "http_options",
    }
)
_CALL_LABEL = "wiki_call"
# One JSONL line of Vertex AI batch prediction input or output (open provider shape).
type VertexBatchLine = dict[str, JsonValue]


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation: ModelOperation
    model: ModelId
    contents: str
    system_instruction: str
    response_json_schema: dict[str, JsonValue]
    max_output_tokens: PositiveInt
    thinking_level: ModelThinkingLevel | None

    @model_validator(mode="after")
    def model_is_exact(self) -> Self:
        if not self.model or self.model == "latest" or self.model.endswith("-latest"):
            msg = "Exact Gemini model IDs are required"
            raise ValueError(msg)
        return self

    @property
    def request_sha256(self) -> ModelRequestHash:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ModelRequestHash(hashlib.sha256(canonical.encode()).hexdigest())

    def vertex_batch_line(self, call_id: ModelCallId) -> VertexBatchLine:
        generation_config: dict[str, JsonValue] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": self.response_json_schema,
            "maxOutputTokens": self.max_output_tokens,
        }
        if self.thinking_level is not None:
            generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        return {
            "request": {
                "contents": [{"role": "user", "parts": [{"text": self.contents}]}],
                "systemInstruction": {"parts": [{"text": self.system_instruction}]},
                "generationConfig": generation_config,
                "labels": {_CALL_LABEL: call_id.hex},
            }
        }


def model_request(
    *,
    operation: ModelOperation,
    model: str,
    contents: str,
    config: genai_types.GenerateContentConfig,
) -> ModelRequest:
    unsupported = config.model_dump(exclude_none=True).keys() - _SUPPORTED_CONFIG_FIELDS
    if unsupported:
        msg = f"Unsupported Gemini request settings: {sorted(unsupported)}"
        raise ValueError(msg)
    if not isinstance(config.system_instruction, str):
        msg = "Document model requests require a text system instruction"
        raise TypeError(msg)
    if config.response_mime_type != "application/json" or not isinstance(
        config.response_json_schema, dict
    ):
        msg = "Document model requests require a JSON response schema"
        raise ValueError(msg)
    if config.max_output_tokens is None:
        msg = "Document model requests require an output token limit"
        raise ValueError(msg)
    level = (
        None
        if config.thinking_config is None or config.thinking_config.thinking_level is None
        else config.thinking_config.thinking_level.value
    )
    return ModelRequest.model_validate(
        {
            "operation": operation,
            "model": model,
            "contents": contents,
            "system_instruction": config.system_instruction,
            "response_json_schema": config.response_json_schema,
            "max_output_tokens": config.max_output_tokens,
            "thinking_level": level,
        },
        strict=False,
    )


def batch_line_call_id(line: VertexBatchLine) -> str | None:
    request = line.get("request")
    if not isinstance(request, dict):
        return None
    labels = request.get("labels")
    if not isinstance(labels, dict):
        return None
    value = labels.get(_CALL_LABEL)
    return value if isinstance(value, str) else None
