import pytest

from app.providers.factory import _openai_structured_output_mode


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "json_object"),
        ("https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "json_object"),
        ("https://api.openai.com/v1", "json_schema"),
        ("http://127.0.0.1:8010/v1", "json_schema"),
    ],
)
def test_openai_structured_output_mode_uses_model_studio_json_mode_only_for_its_domains(
    base_url: str,
    expected: str,
) -> None:
    assert _openai_structured_output_mode(base_url) == expected
