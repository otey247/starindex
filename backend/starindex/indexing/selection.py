"""Which files of a repository are worth indexing at each level, and how they are read safely.

Nothing here executes repository content. Archives are read member-by-member in memory and
are never extracted to disk, so path traversal, symlinks and device files cannot affect the host.
"""

from __future__ import annotations

import fnmatch
import re
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..settings_store import IndexingSettings

README_RE = re.compile(r"^readme(\.[a-z0-9_-]+)*$", re.IGNORECASE)
DOC_EXT = {".md", ".mdx", ".markdown", ".rst", ".adoc", ".txt"}
DOC_DIRS = {"docs", "doc", "documentation", "guide", "guides", "wiki", "website", "book", "handbook", "manual", "tutorials", "tutorial"}
EXAMPLE_DIRS = {"examples", "example", "samples", "sample", "demo", "demos", "cookbook", "recipes"}
SKIP_DOC_NAMES = {
    "changelog", "changes", "history", "code_of_conduct", "security", "license", "licence", "contributors",
    "authors", "notice", "copying", "patents", "codeowners", "release_notes", "releases", "news", "todo",
    "pull_request_template", "issue_template", "maintainers", "governance", "support", "funding", "thanks",
}
ROOT_DOC_NAMES = {
    "architecture", "design", "usage", "faq", "getting_started", "getting-started", "quickstart", "install",
    "installation", "overview", "concepts", "api", "roadmap", "features", "tutorial", "guide", "development",
    "contributing", "agents", "claude", "llms",
}
MANIFESTS = {
    "package.json": "npm", "pyproject.toml": "pypi", "setup.cfg": "pypi", "setup.py": "pypi", "pipfile": "pypi",
    "environment.yml": "conda", "cargo.toml": "cargo", "go.mod": "go", "pom.xml": "maven",
    "build.gradle": "maven", "build.gradle.kts": "maven", "gemfile": "rubygems", "composer.json": "composer",
    "pubspec.yaml": "pub", "package.swift": "swift", "mix.exs": "hex", "deno.json": "deno", "deno.jsonc": "deno",
    "cmakelists.txt": "cmake", "directory.packages.props": "nuget",
}
CONFIG_NAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "makefile",
    "justfile", "taskfile.yml", ".env.example", "wrangler.toml", "vercel.json", "netlify.toml", "fly.toml",
    "render.yaml", "smithery.yaml", "server.json", "mcp.json", ".mcp.json", "langgraph.json", "turbo.json",
    "pnpm-workspace.yaml", "nx.json", "helmfile.yaml", "chart.yaml", "skaffold.yaml", "tsconfig.json",
}
API_RE = re.compile(r"(openapi|swagger|asyncapi)[^/]*\.(ya?ml|json)$|\.(proto|graphql|gql|thrift|avsc)$", re.IGNORECASE)
LOCALE_RE = re.compile(
    r"(^|[._\-/])(zh|zh[-_]?cn|zh[-_]?tw|zh[-_]?hans|zh[-_]?hant|cn|ja|jp|ko|kr|ru|es|fr|de|pt|pt[-_]?br|it|tr|vi|ar|hi|id|pl|uk|fa|th|nl|bn|he)([._\-/]|$)",
    re.IGNORECASE,
)
I18N_DIRS = {"i18n", "locales", "locale", "translations", "translated", "l10n", "lang", "langs"}
LFS_MAGIC = b"version https://git-lfs.github.com/spec/"
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".psd", ".pdf", ".zip", ".gz", ".tar", ".tgz",
    ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".class", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a",
    ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".flac",
    ".glb", ".gltf", ".fbx", ".obj", ".blend", ".stl", ".onnx", ".pt", ".pth", ".safetensors", ".ckpt", ".h5",
    ".npy", ".npz", ".parquet", ".arrow", ".db", ".sqlite", ".pkl", ".pickle", ".dat", ".pb", ".tflite", ".gguf",
}
_LANG_BY_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".kts": "Kotlin", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".c": "C", ".h": "C",
    ".hpp": "C++", ".swift": "Swift", ".rb": "Ruby", ".php": "PHP", ".scala": "Scala", ".lua": "Lua",
    ".zig": "Zig", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".clj": "Clojure", ".hs": "Haskell",
    ".ml": "OCaml", ".r": "R", ".jl": "Julia", ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".proto": "Protocol Buffers", ".graphql": "GraphQL", ".gql": "GraphQL", ".vue": "Vue", ".svelte": "Svelte",
    ".glsl": "GLSL", ".wgsl": "WGSL", ".gd": "GDScript", ".tf": "HCL", ".nix": "Nix", ".md": "Markdown",
    ".mdx": "MDX", ".rst": "reStructuredText", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
}


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str            # readme | doc | example | manifest | config | api | code
    size: int
    blob_sha: str | None
    priority: int        # lower = more important

    @property
    def language(self) -> str | None:
        return language_for(self.path)


def language_for(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    if name == "dockerfile":
        return "Dockerfile"
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return _LANG_BY_EXT.get(ext)


def safe_repo_path(path: str) -> str | None:
    """Normalise a path from a tree or archive; None if it is absolute or escapes the root."""
    if not path or "\x00" in path or "\\" in path:
        return None
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or (p.parts and re.match(r"^[A-Za-z]:", p.parts[0])):
        return None
    return str(p)


def _excluded(path: str, settings: IndexingSettings) -> bool:
    parts = path.split("/")
    lowered = [p.lower() for p in parts[:-1]]
    exclude_dirs = {d.lower() for d in settings.exclude_dirs}
    if any(d in exclude_dirs for d in lowered):
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in settings.exclude_globs)


def _is_translation(path: str) -> bool:
    lowered = path.lower()
    parts = lowered.split("/")
    if any(p in I18N_DIRS for p in parts[:-1]):
        return True
    stem = parts[-1].rsplit(".", 1)[0]
    # README.zh-CN.md, README_ja.md, docs/zh/guide.md
    if LOCALE_RE.search(stem) and stem.split(".")[0].split("_")[0].split("-")[0] in {"readme", "index", "intro", "guide"}:
        return True
    return any(LOCALE_RE.fullmatch(p) for p in parts[:-1])


def classify_path(path: str, size: int, settings: IndexingSettings, *, deep: bool) -> tuple[str, int] | None:
    """Return (kind, priority) when the file should be indexed at the given level."""
    parts = path.split("/")
    depth = len(parts) - 1
    name = parts[-1]
    lname = name.lower()
    stem, _, ext = lname.rpartition(".")
    ext = f".{ext}" if stem else ""
    stem = stem or lname
    if ext in BINARY_EXT or size > settings.max_file_bytes or size == 0:
        return None
    dirs = [p.lower() for p in parts[:-1]]

    if README_RE.match(lname) and ext in DOC_EXT | {""}:
        if _is_translation(path):
            return None
        if depth == 0:
            return "readme", 0
        if dirs and dirs[0] in DOC_DIRS and depth == 1:
            return "doc", 12
        if any(d in EXAMPLE_DIRS for d in dirs):
            return ("example", 40 + depth) if not _excluded_non_example(path, settings) else None
        if depth <= 2 and not _excluded(path, settings):
            return "doc", 30 + depth      # package READMEs in monorepos
        return None

    if lname in MANIFESTS or lname.startswith("requirements") and ext == ".txt" or ext == ".csproj":
        if _excluded(path, settings) or depth > 2:
            return None
        return "manifest", 5 + depth * 6

    if _excluded(path, settings):
        return None

    if API_RE.search(lname) and (deep or depth <= 2):
        return "api", 20 + depth
    if lname in CONFIG_NAMES and depth <= 1:
        return "config", 25 + depth

    if ext in DOC_EXT - {".txt"}:
        if _is_translation(path) or stem.replace("-", "_") in SKIP_DOC_NAMES:
            return None
        if depth == 0:
            return ("doc", 8) if stem.replace("-", "_") in ROOT_DOC_NAMES else ("doc", 22)
        if dirs[0] in DOC_DIRS or (len(dirs) > 1 and dirs[1] in DOC_DIRS):
            return "doc", 14 + depth * 2
        if any(d in EXAMPLE_DIRS for d in dirs):
            return "example", 42 + depth
        if deep:
            return "doc", 50 + depth
        return None

    if deep and ext in {e.lower() for e in settings.code_extensions}:
        test_like = any(d in {"test", "tests", "__tests__", "spec", "specs", "e2e", "benchmarks", "bench"} for d in dirs) or re.search(
            r"(_test|\.test|\.spec|_spec)\.[a-z]+$", lname
        )
        example = any(d in EXAMPLE_DIRS for d in dirs)
        base = 100 + depth * 3
        if example:
            return "example", base + 10
        return "code", base + (60 if test_like else 0)
    return None


def _excluded_non_example(path: str, settings: IndexingSettings) -> bool:
    return _excluded(path, settings)


def select_from_tree(tree: list[dict], settings: IndexingSettings, *, level: str) -> list[Candidate]:
    """Choose files from a git tree listing. Bounded by the per-level file limits."""
    deep = level == "deep"
    found: list[Candidate] = []
    for entry in tree:
        if entry.get("type") != "blob":      # skips trees and submodule commits
            continue
        path = safe_repo_path(entry.get("path") or "")
        if not path:
            continue
        size = int(entry.get("size") or 0)
        verdict = classify_path(path, size, settings, deep=deep)
        if verdict:
            found.append(Candidate(path, verdict[0], size, entry.get("sha"), verdict[1]))
    found.sort(key=lambda c: (c.priority, len(c.path), c.path))
    docs_like = [c for c in found if c.kind != "code" and c.priority < 100]
    limit_docs = settings.max_docs_files
    chosen = docs_like[:limit_docs]
    if deep:
        taken = {c.path for c in chosen}
        rest = [c for c in found if c.path not in taken]
        chosen += rest[: max(0, settings.max_deep_files - len(chosen))]
    return chosen


def decode_text(data: bytes) -> str | None:
    """Decode a file as text; None for binary content or Git LFS pointers."""
    if not data or data.startswith(LFS_MAGIC):
        return None
    head = data[:8192]
    if b"\x00" in head:
        return None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    text = data.decode("utf-8", errors="replace")
    if text.count("�") > max(8, len(text) // 200):
        return None
    return text


@dataclass
class ArchiveFile:
    path: str
    data: bytes


def iter_archive(
    archive: Path,
    *,
    wanted: set[str] | None,
    settings: IndexingSettings,
    deep: bool = True,
    max_members: int = 250_000,
    max_total_bytes: int = 600 * 1024 * 1024,
) -> Iterator[ArchiveFile]:
    """Yield selected regular files from a GitHub tarball without extracting anything to disk.

    ``wanted`` restricts to known paths (from the tree listing); when None (tree was truncated)
    files are admitted by the same classification rules until the limits are reached.
    Symlinks, hard links, devices and anything with an unsafe path are ignored.
    """
    total = 0
    admitted = 0
    with tarfile.open(archive, mode="r:gz") as tar:
        for count, member in enumerate(tar):
            if count >= max_members:
                break
            if not member.isreg():
                continue
            # GitHub archives nest everything under "<owner>-<repo>-<sha>/".
            _, _, rel = member.name.partition("/")
            path = safe_repo_path(rel)
            if not path:
                continue
            if wanted is not None:
                if path not in wanted:
                    continue
            else:
                if classify_path(path, member.size, settings, deep=deep) is None:
                    continue
                if admitted >= settings.max_deep_files:
                    break
            if member.size > settings.max_file_bytes:
                continue
            total += member.size
            if total > max_total_bytes:
                break
            fh = tar.extractfile(member)
            if fh is None:
                continue
            data = fh.read(settings.max_file_bytes + 1)
            if len(data) > settings.max_file_bytes:
                continue
            admitted += 1
            yield ArchiveFile(path, data)
