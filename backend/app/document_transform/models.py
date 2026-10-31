from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
import hashlib
import json
from typing import Literal, override

from google import genai
from google.genai import errors as genai_errors, types as genai_types

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import DocumentIR, TextBlock
from app.document_transform.schemas import (
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TransformPhase,
)
from app.document_transform.unslop import (
    UNSLOP_REVIEW_INSTRUCTION,
    UNSLOP_REWRITE_INSTRUCTION,
    model_editable_blocks,
)


_MAX_EDITOR_BATCH_CHARACTERS = 24_000
_MAX_EDITOR_BATCH_BLOCKS = 80


@dataclass(frozen=True, slots=True)
class _ReviewPair:
    block_id: str
    expected_sha256: str
    replacement_sha256: str
    source: str
    proposed: str


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
    supports_semantic_rewrite: bool = False

    @property
    @abstractmethod
    def model_ids(self) -> tuple[str, ...]: ...

    @abstractmethod
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]: ...

    async def verify(self, _document: DocumentIR, _patches: list[TextPatch], /) -> frozenset[str]:
        return frozenset()


class VertexGeminiPatchGenerator(PatchGenerator):
    supports_semantic_rewrite: bool = True

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
        if phase is TransformPhase.UNSLOP:
            patches: list[TextPatch] = []
            for batch in _editor_batches(document):
                patches.extend(await self._generate_batch(batch, phase=phase))
            return patches
        return await self._generate_batch(document, phase=phase)

    async def _generate_batch(
        self, document: DocumentIR, *, phase: TransformPhase
    ) -> list[TextPatch]:
        prompt = _prompt(document, phase=phase)
        primary_model = (
            self._policy.unslop_model
            if phase is TransformPhase.UNSLOP
            else self._policy.restyle_model
        )
        attempts = tuple(
            dict.fromkeys(
                (
                    primary_model,
                    self._policy.fallback_model,
                    self._policy.restyle_model,
                )
            )
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
                    system_instruction=UNSLOP_REWRITE_INSTRUCTION
                    if phase is TransformPhase.UNSLOP
                    else "Formatting must preserve the author's text exactly.",
                    response_mime_type="application/json",
                    response_schema=PatchEnvelope,
                    max_output_tokens=16_384,
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

    @override
    async def verify(self, document: DocumentIR, patches: list[TextPatch]) -> frozenset[str]:
        by_id = {block.id: block for block in document.blocks}
        pending = {patch.block_id: patch for patch in patches}
        if len(pending) != len(patches) or any(
            patch.block_id not in by_id or patch.expected_sha256 != by_id[patch.block_id].sha256
            for patch in patches
        ):
            msg = "Semantic review requires unique, current source-bound proposals"
            raise ValueError(msg)
        approved: set[str] = set()
        for batch in _editor_batches(document):
            pairs = [
                _ReviewPair(
                    block_id=block.id,
                    expected_sha256=block.sha256,
                    replacement_sha256=hashlib.sha256(
                        pending[block.id].replacement.encode()
                    ).hexdigest(),
                    source=block.text,
                    proposed=pending[block.id].replacement,
                )
                for block in batch.blocks
                if block.id in pending
            ]
            if not pairs:
                continue
            last_error: Exception | None = None
            for model in dict.fromkeys(
                (self._policy.restyle_model, self._policy.unslop_model, self._policy.fallback_model)
            ):
                try:
                    reviews = await self._review_once(model=model, pairs=pairs)
                    approved.update(_validated_reviews(pairs, reviews))
                    break
                except (TimeoutError, ValueError, genai_errors.APIError) as exc:
                    last_error = exc
            else:
                raise ModelUnavailableError from last_error
        return frozenset(approved)

    async def _review_once(self, *, model: str, pairs: list[_ReviewPair]) -> PatchReviewEnvelope:
        seconds = self._policy.timeout.total_seconds()
        async with asyncio.timeout(seconds):
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=json.dumps(
                    {"document_proposals": [asdict(pair) for pair in pairs]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                config=genai_types.GenerateContentConfig(
                    system_instruction=UNSLOP_REVIEW_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=PatchReviewEnvelope,
                    temperature=0,
                    max_output_tokens=16_384,
                    thinking_config=None
                    if model == self._policy.fallback_model
                    else genai_types.ThinkingConfig(
                        thinking_level=genai_types.ThinkingLevel.MEDIUM
                    ),
                    http_options=genai_types.HttpOptions(timeout=int(seconds * 1000)),
                ),
            )
        if isinstance(response.parsed, PatchReviewEnvelope):
            return response.parsed
        if response.text:
            return PatchReviewEnvelope.model_validate_json(response.text)
        msg = "Gemini returned no semantic review"
        raise ValueError(msg)


def _validated_reviews(pairs: list[_ReviewPair], reviews: PatchReviewEnvelope) -> frozenset[str]:
    expected = {pair.block_id: pair for pair in pairs}
    seen: set[str] = set()
    for review in reviews.reviews:
        pair = expected.get(review.block_id)
        if (
            pair is None
            or review.block_id in seen
            or review.expected_sha256 != pair.expected_sha256
            or review.replacement_sha256 != pair.replacement_sha256
        ):
            msg = "Semantic verdict is not bound to its exact proposal"
            raise ValueError(msg)
        seen.add(review.block_id)
    if seen != expected.keys():
        msg = "Semantic verdicts are incomplete"
        raise ValueError(msg)
    return frozenset(review.block_id for review in reviews.reviews if review.equivalent)


def _thinking_level(
    phase: TransformPhase,
    *,
    escalated: bool,
) -> genai_types.ThinkingLevel:
    if escalated:
        return genai_types.ThinkingLevel.HIGH
    if phase is TransformPhase.UNSLOP:
        return genai_types.ThinkingLevel.LOW
    return genai_types.ThinkingLevel.MEDIUM


def _prompt(document: DocumentIR, *, phase: TransformPhase) -> str:
    selected = (
        model_editable_blocks(document) if phase is TransformPhase.UNSLOP else document.blocks
    )
    editable = [
        {
            "block_id": block.id,
            "sha256": block.sha256,
            "kind": block.kind,
            "text": block.text,
            "protected_suffix": block.protected_suffix,
        }
        for block in selected
        if block.editable
    ]
    instruction = (
        UNSLOP_REWRITE_INSTRUCTION
        if phase is TransformPhase.UNSLOP
        else "Formatting preserves every character of the author's text, including whitespace "
        'and punctuation. Do not propose text replacements. Return {"patches":[]}; '
        "native formatting and structural changes are handled separately."
    )
    return "\n".join(
        (
            "You are a precise content editor.",
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
            json.dumps(editable, ensure_ascii=False, separators=(",", ":")),
        )
    )


def _editor_batches(document: DocumentIR) -> tuple[DocumentIR, ...]:
    batches: list[DocumentIR] = []
    current: list[TextBlock] = []
    characters = 0
    for block in model_editable_blocks(document):
        if current and (
            characters + len(block.text) > _MAX_EDITOR_BATCH_CHARACTERS
            or len(current) >= _MAX_EDITOR_BATCH_BLOCKS
        ):
            batches.append(replace(document, blocks=tuple(current)))
            current = []
            characters = 0
        current.append(block)
        characters += len(block.text)
    if current:
        batches.append(replace(document, blocks=tuple(current)))
    return tuple(batches)
