from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from typing import Literal, override

from google import genai
from google.genai import errors as genai_errors, types as genai_types

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import DocumentIR
from app.document_transform.schemas import (
    PatchEnvelope,
    TextPatch,
    TransformPhase,
)
from app.document_transform.unslop import scan_unslop_candidates


@dataclass(frozen=True, slots=True)
class GeminiModelPolicy:
    project: str
    location: Literal["global"]
    unslop_model: str
    fallback_model: str
    restyle_model: str
    timeout: timedelta = timedelta(seconds=45)

    def __post_init__(self) -> None:
        for model in (self.unslop_model, self.fallback_model, self.restyle_model):
            if not model or model.endswith("-latest") or model == "latest":
                msg = "Exact Gemini model IDs are required"
                raise ValueError(msg)


class PatchGenerator(ABC):
    @property
    @abstractmethod
    def model_ids(self) -> tuple[str, ...]: ...

    @abstractmethod
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]: ...


class VertexGeminiPatchGenerator(PatchGenerator):
    def __init__(
        self,
        *,
        policy: GeminiModelPolicy,
        client: genai.Client | None = None,
    ) -> None:
        self._policy = policy
        self._client = client or genai.Client(
            vertexai=True,
            project=policy.project,
            location=policy.location,
        )

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return (
            self._policy.unslop_model,
            self._policy.fallback_model,
            self._policy.restyle_model,
        )

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        prompt = _prompt(document, phase=phase)
        primary_model = (
            self._policy.unslop_model
            if phase is TransformPhase.UNSLOP
            else self._policy.restyle_model
        )
        attempts = (
            primary_model,
            self._policy.fallback_model,
            self._policy.restyle_model,
        )
        last_error: Exception | None = None
        for model in attempts:
            try:
                return await self._generate_once(
                    model=model,
                    prompt=prompt,
                    phase=phase,
                    escalated=model != primary_model,
                )
            except (TimeoutError, ValueError, genai_errors.APIError) as exc:
                last_error = exc
        raise ModelUnavailableError from last_error

    async def _generate_once(
        self,
        *,
        model: str,
        prompt: str,
        phase: TransformPhase,
        escalated: bool,
    ) -> list[TextPatch]:
        timeout_seconds = self._policy.timeout.total_seconds()
        async with asyncio.timeout(timeout_seconds):
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    thinking_config=(
                        None
                        if model == self._policy.fallback_model
                        else genai_types.ThinkingConfig(
                            thinking_level=_thinking_level(phase, escalated=escalated)
                        )
                    ),
                    http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
                ),
            )
        if isinstance(response.parsed, PatchEnvelope):
            return response.parsed.patches
        if response.text:
            return PatchEnvelope.model_validate_json(response.text).patches
        msg = "Gemini returned no patch proposal"
        raise ValueError(msg)


def _thinking_level(
    phase: TransformPhase,
    *,
    escalated: bool,
) -> genai_types.ThinkingLevel:
    if escalated:
        return genai_types.ThinkingLevel.HIGH
    if phase is TransformPhase.UNSLOP:
        return genai_types.ThinkingLevel.MINIMAL
    return genai_types.ThinkingLevel.MEDIUM


def _prompt(document: DocumentIR, *, phase: TransformPhase) -> str:
    candidates = scan_unslop_candidates(document) if phase is TransformPhase.UNSLOP else ()
    candidate_ids = {candidate.block_id for candidate in candidates}
    editable = [
        {"block_id": block.id, "sha256": block.sha256, "kind": block.kind, "text": block.text}
        for block in document.blocks
        if block.editable and (phase is TransformPhase.FORMAT or block.id in candidate_ids)
    ]
    instruction = (
        "Remove only obvious AI-generated filler, canned transitions, redundant summaries, "
        "and inflated wording. Keep the author's register and structure. For this safety profile, "
        "delete only the supplied matched phrase and adjacent punctuation or whitespace; preserve "
        "all other words in their original order and do not introduce synonyms."
        if phase is TransformPhase.UNSLOP
        else "Formatting preserves every character of the author's text, including whitespace "
        'and punctuation. Do not propose text replacements. Return {"patches":[]}; '
        "native formatting and structural changes are handled separately."
    )
    return "\n".join(
        (
            "You are a conservative content editor.",
            instruction,
            (
                "Return only JSON patches. Do not alter names, facts, numbers, dates, citations, "
                "URLs, quotes, code, negation, modal words, or force-bearing language."
            ),
            (
                'The exact JSON shape is {"patches":[{"block_id":"b0",'
                '"expected_sha256":"<supplied sha256>","replacement":"<complete replacement '
                'block>","rationale":"<brief reason>"}]}. Return {"patches":[]} for no edits. '
                "Do not use JSON Patch op/path/value fields."
            ),
            "Omit unchanged blocks. expected_sha256 must exactly echo the supplied sha256.",
            "Scanner matches are candidates only; do not edit unless the full sentence clearly improves.",
            json.dumps(
                [asdict(candidate) for candidate in candidates],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            json.dumps(editable, ensure_ascii=False, separators=(",", ":")),
        )
    )
