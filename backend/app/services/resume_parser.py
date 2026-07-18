import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader


@dataclass(frozen=True)
class ParsedSection:
    section_type: str
    heading: str | None
    content: str
    sequence: int


@dataclass(frozen=True)
class ParsedClaim:
    section_sequence: int
    claim_type: str
    content: str
    confidence: float
    source_span: dict


class ResumeParseError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


HEADING_TYPES = {
    "education": ("教育", "教育经历", "education"),
    "experience": ("工作", "工作经历", "实习", "实习经历", "experience", "employment"),
    "projects": ("项目", "项目经历", "projects", "project experience"),
    "skills": ("技能", "专业技能", "skills", "technical skills"),
    "summary": ("简介", "个人简介", "概述", "summary", "profile"),
}


def _extract_pdf(path: Path) -> str:
    try:
        reader = PdfReader(path)
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ResumeParseError("pdf_parse_failed", "PDF 内容无法解析") from exc


def _extract_docx(path: Path) -> str:
    try:
        document = Document(path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:
        raise ResumeParseError("docx_parse_failed", "DOCX 内容无法解析") from exc


def extract_resume_text(path: Path, mime_type: str) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    elif suffix in {".md", ".txt"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ResumeParseError("text_encoding_invalid", "文本简历必须使用 UTF-8 编码") from exc
    else:  # The upload service should prevent this branch.
        raise ResumeParseError("resume_type_unsupported", f"不支持的简历类型：{mime_type}")
    text = text.replace("\x00", "").replace("\r\n", "\n").strip()
    if not text:
        raise ResumeParseError("resume_text_empty", "简历中没有提取到可用文本")
    return text


def _heading_type(line: str) -> str | None:
    candidate = re.sub(r"^[#\s]+|[:：\s]+$", "", line).strip().casefold()
    if len(candidate) > 40:
        return None
    for section_type, aliases in HEADING_TYPES.items():
        if candidate in aliases:
            return section_type
    return None


def split_resume_sections(text: str) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    current_type = "general"
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(
                ParsedSection(
                    section_type=current_type,
                    heading=current_heading,
                    content=content,
                    sequence=len(sections) + 1,
                )
            )

    for line in text.splitlines():
        section_type = _heading_type(line)
        if section_type:
            flush()
            current_type = section_type
            current_heading = re.sub(r"^[#\s]+", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    if not sections:
        sections.append(
            ParsedSection(
                section_type="general",
                heading=None,
                content=text,
                sequence=1,
            )
        )
    return sections


def extract_resume_claims(sections: list[ParsedSection]) -> list[ParsedClaim]:
    claims: list[ParsedClaim] = []
    for section in sections:
        candidates = [
            re.sub(r"^[\s•·*\-–—\d.）)]+", "", line).strip()
            for line in section.content.splitlines()
        ]
        for line_number, content in enumerate(candidates, start=1):
            if len(content) < 4:
                continue
            claims.append(
                ParsedClaim(
                    section_sequence=section.sequence,
                    claim_type=section.section_type,
                    content=content,
                    confidence=0.7 if section.section_type != "general" else 0.5,
                    source_span={"line": line_number},
                )
            )
            if len(claims) >= 200:
                return claims
    return claims
