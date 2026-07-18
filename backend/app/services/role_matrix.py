from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.config import REPOSITORY_ROOT


class CapabilityDefinition(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    weight: float = Field(gt=0, le=1)


class ScenarioTemplate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    capability: str = Field(min_length=1, max_length=80)
    question_type: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1, max_length=50_000)


class RoleMatrix(BaseModel):
    schema_version: str
    role_key: str
    display_name: str
    capabilities: list[CapabilityDefinition]
    scenario_templates: list[ScenarioTemplate]


@lru_cache(maxsize=16)
def load_role_matrix(role_key: str) -> RoleMatrix:
    safe_key = role_key.strip().casefold().replace("-", "_")
    path = REPOSITORY_ROOT / "seed" / "role-matrices" / f"{safe_key.replace('_', '-')}.yaml"
    if not path.is_file():
        path = Path(REPOSITORY_ROOT / "seed" / "role-matrices" / "llm-application-engineer.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RoleMatrix.model_validate(data)
