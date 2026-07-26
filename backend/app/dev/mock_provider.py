import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


class MockChatRequest(BaseModel):
    model: str = "mock-interview"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    response_format: dict[str, Any] | None = None


app = FastAPI(
    title="Interview Helper deterministic mock provider",
    version="0.1.0",
)


def _message_text(message: dict[str, Any]) -> str:
    """Extract text from either legacy string content or multimodal content blocks."""

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _last_json_message(payload: MockChatRequest) -> dict[str, Any]:
    for message in reversed(payload.messages):
        if message.get("role") != "user":
            continue
        try:
            value = json.loads(_message_text(message))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _schema_properties(payload: MockChatRequest) -> set[str]:
    schema = ((payload.response_format or {}).get("json_schema") or {}).get("schema") or {}
    return set((schema.get("properties") or {}).keys())


def _evaluation_result(source: dict[str, Any]) -> dict[str, Any]:
    questions = source.get("plan_questions") or []
    messages = source.get("interview_messages") or []
    expected_dimensions = (source.get("contract") or {}).get("expected_dimensions") or []

    answers_by_question: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        question_id = message.get("plan_question_id")
        if question_id and message.get("role") == "user" and message.get("confirmed") is True:
            answers_by_question.setdefault(str(question_id), []).append(message)

    question_results: list[dict[str, Any]] = []
    all_evidence: list[dict[str, str]] = []
    for question in questions:
        question_id = str(question.get("plan_question_id"))
        answers = answers_by_question.get(question_id, [])
        evidence = [
            {
                "message_id": str(answer["message_id"]),
                "claim": "回答包含可核验的方案、取舍或边界说明。",
            }
            for answer in answers[:1]
        ]
        all_evidence.extend(evidence)
        question_results.append(
            {
                "plan_question_id": question_id,
                "anchor": "partial" if evidence else "evidence_insufficient",
                "summary": (
                    "回答给出了基本技术路径；真实模型接入后可获得更细致的判断。"
                    if evidence
                    else "本题没有已确认的原始回答证据。"
                ),
                "evidence": evidence,
                "gaps": ["补充指标、失败场景和方案取舍。"] if evidence else ["完成作答。"],
                "actions": ["使用“结论—依据—取舍—边界”结构重答。"],
                "confidence": 0.65 if evidence else 0.2,
            }
        )

    dimension_results = [
        {
            "dimension": dimension,
            "anchor": "partial" if all_evidence else "evidence_insufficient",
            "evidence": all_evidence[:1],
            "gaps": ["进一步量化效果并说明异常路径。"],
            "action": "选择一道题，在五分钟内补充指标、失败处理与权衡。",
            "confidence": 0.6 if all_evidence else 0.2,
        }
        for dimension in expected_dimensions
    ]
    return {
        "overall_anchor": "partial" if all_evidence else "evidence_insufficient",
        "overview": ("这是本地模拟 Provider 生成的确定性报告，仅用于验证完整产品流程。"),
        "strengths": ["能够给出基本技术方案。"] if all_evidence else [],
        "gaps": ["需要补充量化指标、失败模式与技术取舍。"],
        "action_plan": [
            {
                "title": "结构化重答",
                "instruction": "选一道题按结论、依据、取舍、边界四段重新回答。",
                "success_criteria": "回答至少包含一个指标和一个失败处理方案。",
                "priority": 1,
            }
        ],
        "questions": question_results,
        "dimensions": dimension_results,
    }


def _coach_result(source: dict[str, Any]) -> dict[str, Any]:
    request = source.get("request") or {}
    mode = str(request.get("mode") or "explain")
    answers = source.get("original_answers") or []
    original = str(answers[0].get("content")) if answers else None
    source_ids = [str(item["message_id"]) for item in answers if item.get("message_id")]
    return {
        "mode": mode,
        "title": "本地确定性复盘",
        "explanation": "先明确结论，再用指标、取舍和失败边界支撑结论。",
        "original_answer": original,
        "suggested_answer": (
            "我的结论是先采用可观测、可回退的最小方案；随后用离线评估与线上指标"
            "验证效果，并为超时、限流和模型异常准备降级路径。"
            if mode == "rewrite"
            else None
        ),
        "practice_prompts": (
            ["请在两分钟内说明该方案的核心指标、最大风险和回退策略。"] if mode == "practice" else []
        ),
        "source_message_ids": source_ids,
    }


def _resume_structure_result(source: dict[str, Any]) -> dict[str, Any]:
    """Return only source lines so local demos exercise grounding validation too."""
    sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    heading_types = {
        "education": "education",
        "experience": "experience",
        "employment": "experience",
        "work": "experience",
        "project": "projects",
        "skill": "skills",
        "summary": "summary",
        "profile": "summary",
    }
    for source_section in source.get("source_sections") or []:
        sequence = int(source_section.get("sequence") or 1)
        heading = source_section.get("heading")
        heading_text = str(heading or "").casefold()
        section_type = next(
            (value for key, value in heading_types.items() if key in heading_text),
            "general",
        )
        sections.append(
            {
                "source_sequence": sequence,
                "section_type": section_type,
                "heading": heading,
            }
        )
        for line in source_section.get("lines") or []:
            content = str(line.get("content") or "").strip()
            if not content:
                continue
            claims.append(
                {
                    "source_sequence": sequence,
                    "line": int(line.get("line") or 1),
                    "claim_type": section_type,
                    "content": content,
                    "confidence": 0.8,
                }
            )
            if len(claims) >= 200:
                return {"sections": sections, "claims": claims}
    return {"sections": sections, "claims": claims}


def _planner_result(source: dict[str, Any]) -> dict[str, Any]:
    """Create a source-bounded plan for local Planner-role integration tests."""

    candidates = list(source.get("candidate_pool") or [])
    if not candidates:
        return {
            "questions": [],
            "rationale": "No candidate questions were supplied.",
            "capability_coverage": [],
        }

    contract = source.get("contract") or {}
    duration_seconds = int(contract.get("duration_seconds") or len(candidates) * 30)
    base_seconds, remainder = divmod(duration_seconds, len(candidates))
    questions: list[dict[str, Any]] = []
    capability_coverage: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        for tag in candidate.get("capability_tags") or []:
            normalized_tag = str(tag).strip()
            if normalized_tag and normalized_tag not in capability_coverage:
                capability_coverage.append(normalized_tag)
        questions.append(
            {
                "candidate_key": str(candidate.get("candidate_key") or ""),
                "sequence": index,
                "allocated_seconds": base_seconds + (1 if index <= remainder else 0),
                "follow_up_budget": int(candidate.get("max_follow_up_budget") or 0),
                "selection_reason": (
                    "Keep the supplied candidate order while preserving its source-bound "
                    "follow-up allowance."
                ),
            }
        )
    return {
        "questions": questions,
        "rationale": "This deterministic local planner preserves the bounded candidate pool.",
        "capability_coverage": capability_coverage,
    }


def _completion_text(payload: MockChatRequest) -> str:
    properties = _schema_properties(payload)
    source = _last_json_message(payload)
    if {"questions", "dimensions", "overall_anchor"} <= properties:
        return json.dumps(_evaluation_result(source), ensure_ascii=False)
    if {"mode", "practice_prompts", "source_message_ids"} <= properties:
        return json.dumps(_coach_result(source), ensure_ascii=False)
    if {"sections", "claims"} <= properties:
        return json.dumps(_resume_structure_result(source), ensure_ascii=False)
    if {"questions", "rationale", "capability_coverage"} <= properties:
        return json.dumps(_planner_result(source), ensure_ascii=False)

    last_content = _message_text(payload.messages[-1]) if payload.messages else ""
    if last_content.strip().casefold() == "ping":
        return "pong"
    return "请先给出你的结论，再说明关键指标、技术取舍、失败处理与上线后的验证方法。"


def _response_chunk(
    request_id: str,
    model: str,
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [],
    }
    if content is not None or finish_reason is not None:
        payload["choices"] = [
            {
                "index": 0,
                "delta": {"content": content} if content is not None else {},
                "finish_reason": finish_reason,
            }
        ]
    if usage:
        payload["usage"] = usage
    return payload


async def _stream_completion(
    request_id: str,
    model: str,
    content: str,
) -> AsyncIterator[str]:
    midpoint = max(1, len(content) // 2)
    for text in (content[:midpoint], content[midpoint:]):
        if text:
            chunk = _response_chunk(request_id, model, content=text)
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    usage = {"prompt_tokens": 64, "completion_tokens": 32, "total_tokens": 96}
    yield f"data: {json.dumps(_response_chunk(request_id, model, usage=usage))}\n\n"
    final = _response_chunk(request_id, model, finish_reason="stop")
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": "deterministic-local-only"}


@app.post("/v1/chat/completions")
async def chat_completions(payload: MockChatRequest):
    request_id = f"chatcmpl-mock-{uuid.uuid4().hex[:12]}"
    content = _completion_text(payload)
    if payload.stream:
        return StreamingResponse(
            _stream_completion(request_id, payload.model, content),
            media_type="text/event-stream",
        )
    return {
        "id": request_id,
        "object": "chat.completion",
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 64,
            "completion_tokens": 32,
            "total_tokens": 96,
        },
    }


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    await file.read()
    await file.close()
    return {
        "id": f"transcription-mock-{uuid.uuid4().hex[:12]}",
        "text": "这是本地模拟语音转写，请确认后再提交。",
        "language": "zh",
        "duration": 1.0,
    }


def main() -> None:
    uvicorn.run(
        "app.dev.mock_provider:app",
        host="127.0.0.1",
        port=8010,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
