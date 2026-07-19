import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.common import PlanStatus, SessionStatus
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.interview import (
    AnswerAttachment,
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
)
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.schemas.attachments import CodeAttachmentInput
from app.services.interview_orchestrator import save_user_answer


async def clear_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(AnswerAttachment))
        await session.execute(delete(InterviewMessage))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_answer_attachments():
    await clear_data()
    yield
    await clear_data()
    await engine.dispose()


@pytest.mark.asyncio
async def test_answer_and_code_attachment_persist_atomically_as_text_only() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="代码候选人")
        session.add(profile)
        await session.flush()
        company = Company(profile_id=profile.id, name="测试公司", slug="attachment-company")
        session.add(company)
        await session.flush()
        style = CompanyStylePack(company_id=company.id, name="测试风格")
        session.add(style)
        await session.flush()
        round_profile = RoundProfile(style_pack_id=style.id, round_key="round_1", name="一面")
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="后端工程师",
        )
        session.add(config)
        await session.flush()
        plan = InterviewPlan(
            config_id=config.id,
            style_pack_id=style.id,
            status=PlanStatus.FROZEN,
            total_minutes=45,
        )
        session.add(plan)
        await session.flush()
        interview = InterviewSession(
            profile_id=profile.id,
            plan_id=plan.id,
            status=SessionStatus.INTERVIEWING,
        )
        session.add(interview)
        await session.commit()

        message = await save_user_answer(
            session,
            interview,
            "复杂度是 O(n)。",
            attachments=[
                CodeAttachmentInput(
                    language="python",
                    filename="solution.py",
                    content="def solve(items):\n    return len(items)",
                )
            ],
        )
        stored = await session.scalar(
            select(AnswerAttachment).where(AnswerAttachment.message_id == message.id)
        )

    assert stored is not None
    assert stored.language == "python"
    assert stored.content == "def solve(items):\n    return len(items)"
    assert stored.size_bytes == len(stored.content.encode("utf-8"))
    assert stored.storage_path is None
    assert stored.attachment_metadata == {"execution_allowed": False}
