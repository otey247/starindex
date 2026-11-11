"""Format-aware chunking with line ranges for citations.

Each chunk keeps the 1-based line range it came from in the original document, a heading
path (Markdown) or symbol (code), and a lightly cleaned text used for retrieval. Chunk
identity is derived from content so unchanged text keeps its id (and its embedding) even
when lines shift.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

TARGET_CHARS = 1100
MAX_CHARS = 1800
MIN_CHARS = 160
CODE_TARGET_CHARS = 1400
CODE_MAX_CHARS = 2400
_UUID_NS = uuid.UUID("6f2c1d1e-5b7a-4c0e-9d53-51a41de3c0a1")


@dataclass
class Chunk:
    ordinal: int
    heading: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    injection_flag: bool = False
    uid: str = ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:40]


def assign_uids(repo_id: int, path: str, chunks: list[Chunk]) -> None:
    """Stable ids: UUIDv5 over (repo id, path, content hash, n-th duplicate in the file)."""
    seen: dict[str, int] = {}
    for ch in chunks:
        n = seen.get(ch.content_hash, 0)
        seen[ch.content_hash] = n + 1
        ch.uid = str(uuid.uuid5(_UUID_NS, f"{repo_id}\x1f{path}\x1f{ch.content_hash}\x1f{n}"))


# ---------------------------------------------------------------------------------- cleaning

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMG_MD = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_MD = re.compile(r"\[([^\]]+)\]\((?:[^()\s]|\([^()]*\))+(?:\s+\"[^\"]*\")?\)")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^<>]{0,400}>")
_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s+\S+.*$")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def clean_markdown_line(line: str) -> str:
    """Remove markup noise (badges, raw HTML, link targets) while keeping the words."""
    if _REF_DEF.match(line):
        return ""
    line = _IMG_MD.sub(lambda m: m.group(1), line)
    line = _LINK_MD.sub(lambda m: m.group(1), line)
    line = _HTML_TAG.sub(" ", line)
    line = line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _MULTI_SPACE.sub(" ", line).rstrip()


# ---------------------------------------------------------------------------------- injection heuristics

_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|preceding|your)\s+"
    r"(instructions?|prompts?|rules?|context|directions?)"
    r"|you\s+are\s+now\s+(a|an|in)\b"
    r"|new\s+(system\s+)?instructions?\s*:"
    r"|(reveal|print|output|repeat)\s+(your\s+|the\s+)?(system\s+prompt|hidden\s+instructions?)"
    r"|do\s+not\s+(tell|inform|mention\s+(this\s+)?to)\s+the\s+user"
    r"|<\|im_start\|>|<\|system\|>|\[/?INST\]|<<SYS>>"
    r"|^\s*(system|assistant)\s*:\s*(you\s+(must|are|will)|ignore|always)"
    r"|attention\s+(ai|llm|assistant|agent|model)s?\b.{0,40}\b(must|should|instead)"
    r"|(ai|llm|language\s+model)\s+(assistants?|agents?)\s*(reading|processing|summari[sz]ing)\s+this",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


# ---------------------------------------------------------------------------------- markdown

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```+|~~~+)")
_SETEXT = re.compile(r"^(=+|-+)\s*$")


@dataclass
class _Block:
    heading_path: str
    start: int      # 1-based
    end: int
    lines: list[str]

    @property
    def size(self) -> int:
        return sum(len(line) + 1 for line in self.lines)


def _markdown_sections(lines: list[str]) -> list[_Block]:
    stack: list[tuple[int, str]] = []
    blocks: list[_Block] = []
    cur: list[str] = []
    cur_start = 1
    in_fence = False
    fence_marker = ""

    def path() -> str:
        return " > ".join(t for _, t in stack)

    def flush(end_line: int) -> None:
        nonlocal cur
        if any(s.strip() for s in cur):
            blocks.append(_Block(path(), cur_start, end_line, cur))
        cur = []

    for i, raw in enumerate(lines, start=1):
        fence = _FENCE.match(raw)
        if fence:
            marker = fence.group(1)[0] * 3
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif raw.strip().startswith(fence_marker):
                in_fence = False
            cur.append(raw)
            continue
        if in_fence:
            cur.append(raw)
            continue
        m = _ATX.match(raw)
        title: str | None = None
        level = 0
        if m:
            level, title = len(m.group(1)), clean_markdown_line(m.group(2)).strip()
        elif _SETEXT.match(raw) and cur and cur[-1].strip() and not cur[-1].lstrip().startswith(("-", "*", "|", ">")):
            # Setext heading: the previous line is the title.
            title = clean_markdown_line(cur.pop()).strip()
            level = 1 if raw.startswith("=") else 2
            flush(i - 2)
            cur_start = i - 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title[:120]))
            cur = [title]
            continue
        if title is not None:
            flush(i - 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title[:120]))
            cur_start = i
            cur = [raw]
        else:
            if not cur:
                cur_start = i
            cur.append(raw)
    flush(len(lines))
    return blocks


def _split_block(block: _Block, target: int, hard_max: int) -> list[_Block]:
    """Split an oversized section at blank lines (outside code fences), else at the size limit."""
    if block.size <= hard_max:
        return [block]
    out: list[_Block] = []
    cur: list[str] = []
    cur_start = block.start
    size = 0
    in_fence = False
    for offset, line in enumerate(block.lines):
        lineno = block.start + offset
        if _FENCE.match(line):
            in_fence = not in_fence
        cur.append(line)
        size += len(line) + 1
        boundary = (not in_fence and not line.strip() and size >= target) or size >= hard_max
        if boundary:
            out.append(_Block(block.heading_path, cur_start, lineno, cur))
            cur, size, cur_start = [], 0, lineno + 1
    if any(s.strip() for s in cur):
        out.append(_Block(block.heading_path, cur_start, block.end, cur))
    return out


def chunk_markdown(text: str) -> list[Chunk]:
    text = _HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    lines = text.split("\n")
    pieces: list[_Block] = []
    for section in _markdown_sections(lines):
        pieces.extend(_split_block(section, TARGET_CHARS, MAX_CHARS))

    # Merge tiny neighbours that share a parent heading so chunks carry enough context.
    merged: list[_Block] = []
    for piece in pieces:
        if merged:
            prev = merged[-1]
            same_family = piece.heading_path.startswith(prev.heading_path) or prev.heading_path.startswith(piece.heading_path)
            if prev.size < MIN_CHARS and prev.size + piece.size <= MAX_CHARS and (same_family or prev.size < 60):
                merged[-1] = _Block(prev.heading_path or piece.heading_path, prev.start, piece.end, prev.lines + piece.lines)
                continue
        merged.append(piece)

    chunks: list[Chunk] = []
    for block in merged:
        cleaned = [clean_markdown_line(line) for line in block.lines]
        body = _collapse_blank("\n".join(cleaned))
        if len(re.sub(r"\W+", "", body)) < 12:
            continue
        chunks.append(
            Chunk(len(chunks), block.heading_path, block.start, block.end, body, content_hash(body), looks_like_injection(body))
        )
    return chunks


def _collapse_blank(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


# ---------------------------------------------------------------------------------- plain text / rst


def chunk_text(text: str) -> list[Chunk]:
    """Paragraph-based windows for plain text, reStructuredText and AsciiDoc."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_start = 1
    size = 0
    heading = ""
    for i, line in enumerate(lines, start=1):
        if cur and re.fullmatch(r"\s*([=\-~^\"#*+])\1{3,}\s*", line) and cur[-1].strip():
            heading = cur[-1].strip()[:120]      # RST-style underlined title
        if not cur:
            cur_start = i
        cur.append(line)
        size += len(line) + 1
        if (not line.strip() and size >= TARGET_CHARS) or size >= MAX_CHARS:
            _emit_text(chunks, cur, cur_start, i, heading)
            cur, size = [], 0
    if cur:
        _emit_text(chunks, cur, cur_start, len(lines), heading)
    return chunks


def _emit_text(chunks: list[Chunk], lines: list[str], start: int, end: int, heading: str) -> None:
    body = _collapse_blank("\n".join(line.rstrip() for line in lines))
    if len(re.sub(r"\W+", "", body)) < 12:
        return
    chunks.append(Chunk(len(chunks), heading, start, end, body, content_hash(body), looks_like_injection(body)))


# ---------------------------------------------------------------------------------- code

_DEF = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|abstract\s+|final\s+|async\s+|pub(?:\([a-z]+\))?\s+|unsafe\s+|extern\s+)*"
    r"(?:def|class|func|fn|function\*?|interface|struct|enum|trait|impl|module|mod|namespace|type|object|protocol|extension|macro_rules!|package|service|message)\s+"
    r"([A-Za-z_$][\w$.:<>]*)"
)
_ASSIGN_DEF = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function|<)")


def _symbol(line: str) -> str | None:
    if not line or line[0] in " \t":
        return None
    m = _DEF.match(line) or _ASSIGN_DEF.match(line)
    return m.group(1)[:100] if m else None


def chunk_code(text: str) -> list[Chunk]:
    """Cut at top-level definitions when possible; otherwise at blank lines or the size cap."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_start = 1
    size = 0
    heading = ""
    pending_symbol: str | None = None

    def emit(end: int) -> None:
        nonlocal cur, size
        body = "\n".join(line.rstrip() for line in cur).strip("\n")
        if len(re.sub(r"\s+", "", body)) >= 20:
            chunks.append(Chunk(len(chunks), heading, cur_start, end, body, content_hash(body), looks_like_injection(body)))
        cur, size = [], 0

    for i, line in enumerate(lines, start=1):
        sym = _symbol(line)
        if sym and cur and size >= CODE_TARGET_CHARS // 3:
            emit(i - 1)
        if not cur:
            cur_start = i
            heading = sym or pending_symbol or ""
        elif sym and not heading:
            heading = sym
        if sym:
            pending_symbol = sym
        cur.append(line)
        size += len(line) + 1
        if size >= CODE_MAX_CHARS or (size >= CODE_TARGET_CHARS and not line.strip()):
            emit(i)
    if cur:
        emit(len(lines))
    return chunks


# ---------------------------------------------------------------------------------- structured / manifests


def chunk_structured(text: str) -> list[Chunk]:
    """YAML/JSON/TOML and similar: windows that start at top-level keys."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_start = 1
    size = 0
    heading = ""
    top_key = re.compile(r"^(?:\[+([^\]]+)\]+|\"?([A-Za-z_][\w.\-/]*)\"?\s*[:=])")

    def emit(end: int) -> None:
        nonlocal cur, size
        body = "\n".join(line.rstrip() for line in cur).strip("\n")
        if len(re.sub(r"\s+", "", body)) >= 12:
            chunks.append(Chunk(len(chunks), heading, cur_start, end, body, content_hash(body), looks_like_injection(body)))
        cur, size = [], 0

    for i, line in enumerate(lines, start=1):
        m = top_key.match(line)
        key = (m.group(1) or m.group(2)) if m else None
        if key and cur and size >= TARGET_CHARS:
            emit(i - 1)
        if not cur:
            cur_start = i
            heading = (key or heading)[:100] if (key or heading) else ""
        cur.append(line)
        size += len(line) + 1
        if size >= CODE_MAX_CHARS:
            emit(i)
    if cur:
        emit(len(lines))
    return chunks


MARKDOWN_EXT = {".md", ".mdx", ".markdown"}
TEXT_EXT = {".rst", ".txt", ".adoc", ".asciidoc", ".org", ""}
STRUCTURED_EXT = {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".gradle", ".mod", ".lock", ".csproj"}


def chunk_document(path: str, kind: str, text: str) -> list[Chunk]:
    """Pick the chunker for a document by kind and file extension."""
    name = path.rsplit("/", 1)[-1].lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in MARKDOWN_EXT or (kind == "readme" and ext not in {".rst", ".txt", ".adoc"}):
        return chunk_markdown(text)
    if kind in ("readme", "doc", "example") and ext in TEXT_EXT:
        return chunk_text(text)
    if kind in ("manifest", "config", "api") or ext in STRUCTURED_EXT:
        if kind == "manifest" and ext in {".txt", ".py", ".rb", ""}:
            return chunk_text(text) if ext != ".py" else chunk_code(text)
        return chunk_structured(text)
    if kind == "code":
        return chunk_code(text)
    return chunk_text(text)
