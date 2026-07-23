from datetime import datetime

from app.schemas.common import ApiModel


class DiagnosticSnapshot(ApiModel):
    generated_at: datetime
    application: dict
    database: dict
    worker: dict
    models: dict
    files: dict
    privacy: dict


class DiagnosticBundle(ApiModel):
    request_id: str
    snapshot: DiagnosticSnapshot
