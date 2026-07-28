"""Readiness checks shared by the interview preparation experience.

This module deliberately keeps its response operational and private: no model
credentials, resume content, question text, or local file paths are returned.
It is separate from the diagnostics route because the preparation desk needs a
small, actionable decision surface rather than a full diagnostic snapshot.
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.common import ConnectionStatus, ModelRole, ResumeParseStatus, utc_now
from app.db.models.company import Company, CompanyStylePack, EvidenceItem, RoundProfile
from app.db.models.question import Question, QuestionBank
from app.db.models.resume import Resume
from app.db.models.worker import WorkerHeartbeat
from app.db.session import database_healthcheck
from app.schemas.interview_readiness import (
    InterviewReadinessCheck,
    InterviewReadinessCompanyProfile,
    InterviewReadinessEvidenceSummary,
    InterviewReadinessPublic,
    QuickTrialDefaults,
)
from app.services.model_connections import (
    ensure_local_profile,
    list_bindings,
    model_readiness,
)

QUICK_TRIAL_DEFAULTS = QuickTrialDefaults()


def _required_check(
    key: str,
    *,
    ready: bool,
    label: str,
    ready_detail: str,
    blocked_detail: str,
    action: str | None = None,
) -> InterviewReadinessCheck:
    return InterviewReadinessCheck(
        key=key,
        status="ready" if ready else "blocked",
        label=label,
        detail=ready_detail if ready else blocked_detail,
        action=None if ready else action,
    )


def _empty_company_profile() -> InterviewReadinessCompanyProfile:
    return InterviewReadinessCompanyProfile()


async def _worker_available(session: AsyncSession) -> bool:
    """Return whether a worker can plausibly pick up a planning/evaluation job.

    A current degraded heartbeat intentionally remains blocking: a user should
    not be sent into a preparation flow that cannot generate a plan or report.
    """

    stale_before = utc_now() - timedelta(seconds=settings.worker_heartbeat_stale_after_seconds)
    rows = list(
        (
            await session.scalars(
                select(WorkerHeartbeat).where(
                    WorkerHeartbeat.deleted_at.is_(None),
                    WorkerHeartbeat.last_seen_at >= stale_before,
                    WorkerHeartbeat.state.not_in(("stopped", "degraded")),
                )
            )
        ).all()
    )
    return bool(rows)


async def _selected_company_profile(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    company_id: uuid.UUID | None,
    round_profile_id: uuid.UUID | None,
) -> tuple[InterviewReadinessCompanyProfile, bool, bool]:
    """Resolve selection safely, returning validity flags rather than leaking IDs.

    ``company_selected`` and ``round_selected`` distinguish a missing selection
    from a stale/inaccessible one while still producing the same safe public
    readiness payload.
    """

    if company_id is None:
        # A round has no usable meaning until its owning company is selected.
        return _empty_company_profile(), False, False

    company = await session.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.deleted_at.is_(None),
            or_(Company.profile_id.is_(None), Company.profile_id == profile_id),
        )
    )
    if company is None:
        return _empty_company_profile(), False, False

    base = InterviewReadinessCompanyProfile(
        company_id=company.id,
        company_name=company.name,
    )
    if round_profile_id is None:
        return base, True, False

    pair = await session.execute(
        select(RoundProfile, CompanyStylePack)
        .join(CompanyStylePack, CompanyStylePack.id == RoundProfile.style_pack_id)
        .where(
            RoundProfile.id == round_profile_id,
            RoundProfile.deleted_at.is_(None),
            CompanyStylePack.company_id == company.id,
            CompanyStylePack.deleted_at.is_(None),
        )
    )
    selected = pair.one_or_none()
    if selected is None:
        return base, True, False

    round_profile, style_pack = selected
    evidence = list(
        (
            await session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.style_pack_id == style_pack.id,
                    EvidenceItem.deleted_at.is_(None),
                )
                .order_by(EvidenceItem.fetched_at.desc(), EvidenceItem.created_at.desc())
            )
        ).all()
    )
    if evidence:
        trust_status = "source_backed"
        trust_label = "有来源支持"
    elif style_pack.status == "draft":
        trust_status = "draft"
        trust_label = "自定义草案 · 未提供来源"
    else:
        trust_status = "template"
        trust_label = "轮次骨架 · 非风格结论"

    return (
        InterviewReadinessCompanyProfile(
            company_id=company.id,
            company_name=company.name,
            round_profile_id=round_profile.id,
            round_name=round_profile.name,
            style_pack_id=style_pack.id,
            pack_version=style_pack.pack_version,
            trust_status=trust_status,
            trust_label=trust_label,
            evidence_count=len(evidence),
            latest_evidence_at=evidence[0].fetched_at if evidence else None,
            source_summaries=[
                InterviewReadinessEvidenceSummary(
                    title=item.source_title,
                    url=item.source_url,
                    excerpt=item.excerpt,
                    fetched_at=item.fetched_at,
                )
                for item in evidence[:3]
            ],
        ),
        True,
        True,
    )


async def _enhancements(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> list[InterviewReadinessCheck]:
    ready_resume_count = int(
        await session.scalar(
            select(func.count(Resume.id)).where(
                Resume.profile_id == profile_id,
                Resume.deleted_at.is_(None),
                Resume.parse_status == ResumeParseStatus.READY,
            )
        )
        or 0
    )
    resume_count = int(
        await session.scalar(
            select(func.count(Resume.id)).where(
                Resume.profile_id == profile_id,
                Resume.deleted_at.is_(None),
            )
        )
        or 0
    )
    if ready_resume_count:
        resume = InterviewReadinessCheck(
            key="resume",
            status="available",
            label="简历",
            detail="已就绪，可用于更贴近经历的追问。",
        )
    elif resume_count:
        resume = InterviewReadinessCheck(
            key="resume",
            status="processing",
            label="简历",
            detail="简历正在解析；也可以先开始试跑。",
            action="resume",
        )
    else:
        resume = InterviewReadinessCheck(
            key="resume",
            status="not_configured",
            label="简历",
            detail="未添加简历；10 分钟试跑仍可开始。",
            action="resume",
        )

    bank_count = int(
        await session.scalar(
            select(func.count(QuestionBank.id)).where(
                QuestionBank.profile_id == profile_id,
                QuestionBank.deleted_at.is_(None),
            )
        )
        or 0
    )
    question_count = int(
        await session.scalar(
            select(func.count(Question.id))
            .join(QuestionBank, Question.bank_id == QuestionBank.id)
            .where(
                QuestionBank.profile_id == profile_id,
                QuestionBank.deleted_at.is_(None),
                Question.deleted_at.is_(None),
            )
        )
        or 0
    )
    if question_count:
        question_bank = InterviewReadinessCheck(
            key="question_bank",
            status="available",
            label="题库",
            detail=f"已有 {question_count} 道可用题目，可混入本场面试。",
        )
    elif bank_count:
        question_bank = InterviewReadinessCheck(
            key="question_bank",
            status="not_configured",
            label="题库",
            detail="题库中还没有题目；系统会生成基础试跑题。",
            action="question_bank",
        )
    else:
        question_bank = InterviewReadinessCheck(
            key="question_bank",
            status="not_configured",
            label="题库",
            detail="未添加题库；10 分钟试跑仍可开始。",
            action="question_bank",
        )

    bindings = await list_bindings(session, profile_id)
    transcriber = next((item for item in bindings if item.role == ModelRole.TRANSCRIBER), None)
    if transcriber is None:
        transcription = InterviewReadinessCheck(
            key="transcription",
            status="not_configured",
            label="语音转写",
            detail="未配置语音转写；本场可直接输入文字。",
            action="transcription",
        )
    elif (
        transcriber.target_kind == "local_capability"
        or transcriber.connection_status == ConnectionStatus.HEALTHY
    ):
        transcription = InterviewReadinessCheck(
            key="transcription",
            status="available",
            label="语音转写",
            detail="已配置，可在支持语音输入时使用。",
        )
    else:
        transcription = InterviewReadinessCheck(
            key="transcription",
            status="unavailable",
            label="语音转写",
            detail="已绑定的转写服务当前不可用；可直接输入文字。",
            action="transcription",
        )
    return [resume, question_bank, transcription]


async def get_interview_readiness(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None = None,
    round_profile_id: uuid.UUID | None = None,
) -> InterviewReadinessPublic:
    """Collect one lightweight, actionable preparation snapshot.

    The database check deliberately happens before resolving the local profile;
    a broken database should render a useful blocked state, never create data or
    surface a raw driver error.
    """

    database_status = await database_healthcheck()
    database_ready = database_status == "connected"
    if not database_ready:
        blocked = [
            _required_check(
                "database",
                ready=False,
                label="本地数据库",
                ready_detail="数据库连接正常。",
                blocked_detail="数据库当前不可用，暂时无法创建或保存面试。",
                action="diagnostics",
            ),
            _required_check(
                "worker",
                ready=False,
                label="后台 Worker",
                ready_detail="后台任务服务已就绪。",
                blocked_detail="等待数据库恢复后检查 Worker。",
                action="worker",
            ),
            _required_check(
                "interviewer_model",
                ready=False,
                label="面试官模型",
                ready_detail="面试官模型已就绪。",
                blocked_detail="等待数据库恢复后检查模型配置。",
                action="models",
            ),
            _required_check(
                "evaluator_model",
                ready=False,
                label="评估模型",
                ready_detail="评估模型已就绪。",
                blocked_detail="等待数据库恢复后检查模型配置。",
                action="models",
            ),
            _required_check(
                "company",
                ready=False,
                label="公司",
                ready_detail="已选择公司。",
                blocked_detail="等待数据库恢复后检查公司选择。",
                action="companies",
            ),
            _required_check(
                "round_profile",
                ready=False,
                label="面试轮次",
                ready_detail="已选择面试轮次。",
                blocked_detail="等待数据库恢复后检查轮次选择。",
                action="companies",
            ),
        ]
        return InterviewReadinessPublic(
            ready=False,
            blocking=blocked,
            enhancements=[],
            defaults={"quick_trial": QUICK_TRIAL_DEFAULTS},
            company_profile=_empty_company_profile(),
        )

    profile = await ensure_local_profile(session)
    selected_profile, company_selected, round_selected = await _selected_company_profile(
        session,
        profile_id=profile.id,
        company_id=company_id,
        round_profile_id=round_profile_id,
    )
    worker_ready = await _worker_available(session)
    models = await model_readiness(session, profile.id)
    missing_or_degraded = {
        str(role) for role in [*models.missing_roles, *models.degraded_roles]
    }
    interviewer_ready = ModelRole.INTERVIEWER.value not in missing_or_degraded
    evaluator_ready = ModelRole.EVALUATOR.value not in missing_or_degraded

    blocking = [
        _required_check(
            "database",
            ready=True,
            label="本地数据库",
            ready_detail="数据库连接正常。",
            blocked_detail="数据库当前不可用，暂时无法创建或保存面试。",
            action="diagnostics",
        ),
        _required_check(
            "worker",
            ready=worker_ready,
            label="后台 Worker",
            ready_detail="后台任务服务已就绪。",
            blocked_detail="请启动后台 Worker，计划与评估依赖它完成。",
            action="worker",
        ),
        _required_check(
            "interviewer_model",
            ready=interviewer_ready,
            label="面试官模型",
            ready_detail="面试官模型已就绪。",
            blocked_detail="请在设置中绑定并测试面试官模型。",
            action="models",
        ),
        _required_check(
            "evaluator_model",
            ready=evaluator_ready,
            label="评估模型",
            ready_detail="评估模型已就绪。",
            blocked_detail="请在设置中绑定并测试评估模型。",
            action="models",
        ),
        _required_check(
            "company",
            ready=company_selected,
            label="公司",
            ready_detail="已选择公司。",
            blocked_detail=(
                "请选择一家可用公司。" if company_id is None else "所选公司已不可用，请重新选择。"
            ),
            action="companies",
        ),
        _required_check(
            "round_profile",
            ready=round_selected,
            label="面试轮次",
            ready_detail="已选择面试轮次。",
            blocked_detail=(
                "请选择一个面试轮次。"
                if round_profile_id is None
                else "所选轮次不属于当前公司或已不可用，请重新选择。"
            ),
            action="companies",
        ),
    ]
    return InterviewReadinessPublic(
        ready=all(item.status == "ready" for item in blocking),
        blocking=blocking,
        enhancements=await _enhancements(session, profile.id),
        defaults={"quick_trial": QUICK_TRIAL_DEFAULTS},
        company_profile=selected_profile,
    )
