from app.agents.interviewer import SYSTEM_PROMPT


def test_interviewer_role_never_scores_or_reveals_answers() -> None:
    assert "不要评分" in SYSTEM_PROMPT
    assert "不要给出参考答案" in SYSTEM_PROMPT
    assert "只输出面试官下一句话" in SYSTEM_PROMPT
