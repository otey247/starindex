"""Dependency extraction from project manifests. Parsing only -- nothing is installed or run."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Dep:
    ecosystem: str
    name: str
    version_spec: str = ""
    scope: str = "runtime"


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _pep508(spec: str, scope: str = "runtime") -> Dep | None:
    spec = spec.split("#", 1)[0].split(";", 1)[0].strip()
    if not spec or spec.startswith(("-", "git+", "http:", "https:", ".", "/")):
        return None
    m = _REQ_NAME.match(spec)
    if not m:
        return None
    return Dep("pypi", m.group(1).lower().replace("_", "-"), m.group(3).strip()[:60], scope)


def _npm(text: str) -> list[Dep]:
    data = json.loads(text)
    out: list[Dep] = []
    if not isinstance(data, dict):
        return out
    for key, scope in (
        ("dependencies", "runtime"), ("devDependencies", "dev"),
        ("peerDependencies", "peer"), ("optionalDependencies", "optional"),
    ):
        block = data.get(key)
        if isinstance(block, dict):
            out += [Dep("npm", str(n), str(v)[:60], scope) for n, v in block.items()]
    return out


def _pyproject(text: str) -> list[Dep]:
    data = tomllib.loads(text)
    out: list[Dep] = []
    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        if isinstance(spec, str) and (d := _pep508(spec)):
            out.append(d)
    for group, specs in (project.get("optional-dependencies") or {}).items():
        scope = "dev" if group in {"dev", "test", "tests", "lint", "docs", "typing"} else "optional"
        for spec in specs or []:
            if isinstance(spec, str) and (d := _pep508(spec, scope)):
                out.append(d)
    for _group, specs in (data.get("dependency-groups") or {}).items():
        for spec in specs or []:
            if isinstance(spec, str) and (d := _pep508(spec, "dev")):
                out.append(d)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, ver in (poetry.get("dependencies") or {}).items():
        if name.lower() != "python":
            out.append(Dep("pypi", name.lower().replace("_", "-"), _ver(ver)))
    for group in (poetry.get("group") or {}).values():
        for name, ver in (group.get("dependencies") or {}).items():
            out.append(Dep("pypi", name.lower().replace("_", "-"), _ver(ver), "dev"))
    for name, ver in (poetry.get("dev-dependencies") or {}).items():
        out.append(Dep("pypi", name.lower().replace("_", "-"), _ver(ver), "dev"))
    return out


def _ver(value: object) -> str:
    if isinstance(value, str):
        return value[:60]
    if isinstance(value, dict):
        return str(value.get("version") or value.get("git") or value.get("path") or "")[:60]
    return ""


def _requirements(text: str, scope: str) -> list[Dep]:
    return [d for line in text.splitlines() if (d := _pep508(line, scope))]


def _cargo(text: str) -> list[Dep]:
    data = tomllib.loads(text)
    out: list[Dep] = []
    for key, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"), ("build-dependencies", "build")):
        for name, ver in (data.get(key) or {}).items():
            out.append(Dep("cargo", name, _ver(ver), scope))
    for name, ver in ((data.get("workspace") or {}).get("dependencies") or {}).items():
        out.append(Dep("cargo", name, _ver(ver)))
    return out


def _gomod(text: str) -> list[Dep]:
    out: list[Dep] = []
    in_block = False
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip() if "// indirect" not in line else line.strip()
        indirect = "// indirect" in line
        line = line.replace("// indirect", "").strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require ") and "(" not in line:
            line = line[len("require "):]
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            out.append(Dep("go", parts[0], parts[1][:60], "optional" if indirect else "runtime"))
    return out


def _regex_pairs(text: str, pattern: str, ecosystem: str, scope: str = "runtime") -> list[Dep]:
    return [Dep(ecosystem, m.group(1), (m.group(2) or "")[:60] if m.lastindex and m.lastindex > 1 else "", scope)
            for m in re.finditer(pattern, text, re.MULTILINE)]


def _maven(text: str) -> list[Dep]:
    out: list[Dep] = []
    for m in re.finditer(
        r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]+)</version>)?(?:.*?<scope>([^<]+)</scope>)?",
        text, re.DOTALL,
    ):
        scope = "dev" if (m.group(4) or "").strip() == "test" else "runtime"
        out.append(Dep("maven", f"{m.group(1).strip()}:{m.group(2).strip()}", (m.group(3) or "").strip()[:60], scope))
    return out


def _gradle(text: str) -> list[Dep]:
    out: list[Dep] = []
    for m in re.finditer(
        r"\b(implementation|api|compileOnly|runtimeOnly|testImplementation|kapt|ksp)\s*\(?\s*[\"']([^\"':]+:[^\"':]+)(?::([^\"']+))?[\"']",
        text,
    ):
        out.append(Dep("maven", m.group(2), (m.group(3) or "")[:60], "dev" if m.group(1).startswith("test") else "runtime"))
    return out


def _composer(text: str) -> list[Dep]:
    data = json.loads(text)
    out: list[Dep] = []
    if isinstance(data, dict):
        for key, scope in (("require", "runtime"), ("require-dev", "dev")):
            for name, ver in (data.get(key) or {}).items():
                if name != "php" and not name.startswith("ext-"):
                    out.append(Dep("composer", name, str(ver)[:60], scope))
    return out


def _dockerfile(text: str) -> list[Dep]:
    out: list[Dep] = []
    for m in re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?([^\s:@]+)(?::([^\s@]+))?", text, re.MULTILINE | re.IGNORECASE):
        if m.group(1).lower() != "scratch" and not m.group(1).startswith("$"):
            out.append(Dep("docker", m.group(1), (m.group(2) or "")[:60], "build"))
    return out


def parse_manifest(path: str, text: str) -> list[Dep]:
    """Dependencies declared in one manifest. Malformed files yield an empty list."""
    name = path.rsplit("/", 1)[-1].lower()
    try:
        if name == "package.json":
            deps = _npm(text)
        elif name == "pyproject.toml":
            deps = _pyproject(text)
        elif name.startswith("requirements") and name.endswith(".txt"):
            deps = _requirements(text, "dev" if re.search(r"dev|test|lint|doc", name) else "runtime")
        elif name == "cargo.toml":
            deps = _cargo(text)
        elif name == "go.mod":
            deps = _gomod(text)
        elif name == "pom.xml":
            deps = _maven(text)
        elif name in ("build.gradle", "build.gradle.kts"):
            deps = _gradle(text)
        elif name == "composer.json":
            deps = _composer(text)
        elif name == "gemfile":
            deps = _regex_pairs(text, r"^\s*gem\s+[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']+)[\"'])?", "rubygems")
        elif name == "pubspec.yaml":
            deps = _regex_pairs(text, r"^  ([a-z_][a-z0-9_]*):\s*([\^~<>=\d][^\n#]*)?$", "pub")
        elif name.endswith(".csproj") or name == "directory.packages.props":
            deps = _regex_pairs(text, r"<Package(?:Reference|Version)\s+Include=\"([^\"]+)\"(?:\s+Version=\"([^\"]+)\")?", "nuget")
        elif name == "dockerfile":
            deps = _dockerfile(text)
        elif name == "setup.py":
            block = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
            deps = [d for s in re.findall(r"[\"']([^\"']+)[\"']", block.group(1)) if (d := _pep508(s))] if block else []
        else:
            deps = []
    except (ValueError, TypeError, AttributeError, tomllib.TOMLDecodeError):
        return []
    seen: set[tuple[str, str, str]] = set()
    unique: list[Dep] = []
    for d in deps:
        key = (d.ecosystem, d.name, d.scope)
        if d.name and len(d.name) <= 200 and key not in seen:
            seen.add(key)
            unique.append(d)
    return unique[:400]
