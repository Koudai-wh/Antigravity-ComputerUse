"""Extract downloadable code/file artifacts from LLM markdown responses."""

from __future__ import annotations

import re
from dataclasses import dataclass

LANG_TO_EXT: dict[str, str] = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "tsx": ".tsx",
    "jsx": ".jsx",
    "json": ".json",
    "csv": ".csv",
    "markdown": ".md",
    "md": ".md",
    "txt": ".txt",
    "text": ".txt",
    "plaintext": ".txt",
    "bash": ".sh",
    "sh": ".sh",
    "zsh": ".sh",
    "html": ".html",
    "css": ".css",
    "sql": ".sql",
    "yaml": ".yaml",
    "yml": ".yaml",
    "toml": ".toml",
    "xml": ".xml",
    "swift": ".swift",
    "rust": ".rs",
    "go": ".go",
}

_FENCE_RE = re.compile(
    r"(?:^|\n)(?P<hint>[^\n]{0,140})\n?```(?P<info>[^\n]*)\n(?P<body>.*?)```",
    re.DOTALL,
)
_XML_FILE_RE = re.compile(
    r'<file\s+[^>]*?(?:name|path)=["\'](?P<name>[^"\']+)["\'][^>]*>\n?(?P<body>.*?)</file>',
    re.DOTALL | re.IGNORECASE,
)
_FILENAME_IN_HINT_RE = re.compile(
    r"([A-Za-z0-9_.\-/]+\.(?:py|md|txt|csv|json|js|ts|tsx|jsx|html|css|sh|yaml|yml|toml|sql|swift|rs|go))"
)


@dataclass
class ExtractedArtifact:
    filename: str
    language: str
    content: str
    line_count: int
    byte_size: int


def _infer_filename(info: str, hint: str, index: int) -> tuple[str, str]:
    info_clean = info.strip()
    # Check info string like "python:hello.py" or "python filename=hello.py"
    if ":" in info_clean:
        lang_part, name_part = info_clean.split(":", 1)
        lang = lang_part.strip().lower() or "txt"
        name = name_part.strip().strip("\"'")
        if name:
            return name, lang

    kv_match = re.search(r'(?:filename|file|name)=["\']?([^"\'\s]+)["\']?', info_clean)
    lang_token = info_clean.split()[0].lower() if info_clean else "txt"
    if kv_match:
        return kv_match.group(1), lang_token

    # Check if info itself looks like a filename
    fn_info = _FILENAME_IN_HINT_RE.search(info_clean)
    if fn_info:
        ext_lang = fn_info.group(1).rsplit(".", 1)[-1].lower()
        return fn_info.group(1).split("/")[-1], ext_lang

    # Check preceding hint line (e.g. "**`export_data.csv`**" or "# script.py")
    fn_hint = _FILENAME_IN_HINT_RE.search(hint)
    if fn_hint:
        return fn_hint.group(1).split("/")[-1], lang_token

    ext = LANG_TO_EXT.get(lang_token, ".txt")
    return f"artifact_{index}{ext}", lang_token


def extract_artifacts(text: str) -> list[ExtractedArtifact]:
    """Extract downloadable file artifacts from fenced code blocks and <file> tags."""
    artifacts: list[ExtractedArtifact] = []
    seen_bodies: set[str] = set()

    for m in _XML_FILE_RE.finditer(text):
        body = m.group("body").rstrip("\n")
        if not body.strip():
            continue
        fname = m.group("name").strip().split("/")[-1]
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "txt"
        seen_bodies.add(body)
        artifacts.append(
            ExtractedArtifact(
                filename=fname,
                language=ext,
                content=body,
                line_count=body.count("\n") + 1,
                byte_size=len(body.encode("utf-8")),
            )
        )

    for idx, m in enumerate(_FENCE_RE.finditer(text), start=1):
        body = m.group("body").rstrip("\n")
        if not body.strip() or body in seen_bodies:
            continue
        info = m.group("info") or ""
        hint = m.group("hint") or ""
        # Skip tool_call JSON blocks emitted internally
        if info.strip().lower() == "tool_call" or '"arguments"' in body[:120] and '"name"' in body[:80]:
            continue
        fname, lang = _infer_filename(info, hint, len(artifacts) + 1)
        seen_bodies.add(body)
        artifacts.append(
            ExtractedArtifact(
                filename=fname,
                language=lang,
                content=body,
                line_count=body.count("\n") + 1,
                byte_size=len(body.encode("utf-8")),
            )
        )

    return artifacts
