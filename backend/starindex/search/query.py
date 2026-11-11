"""Query language: operators, phrases, exclusions -> filters and an FTS5 expression."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..api.schemas import ParsedQuery, RepoFilters

_TOKEN = re.compile(r'(-?)(?:([a-z_]+):)?(?:"([^"]*)"|(\S+))', re.IGNORECASE)
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
OPERATORS = {
    "lang", "language", "topic", "license", "owner", "user", "org", "stars", "size", "pushed", "starred",
    "is", "tag", "category", "collection", "in", "repo",
}
STOPWORDS = frozenset(
    """a an and are as at be been but by can could do does for from had has have how i if in into is it its
    me my of on or our should show so some such than that the their them then there these they this those to
    was we were what when where which who why will with would you your find all any related get give want
    need looking use used using about also best good great like please""".split()
)
# Words that carry intent in a query about repositories but rarely discriminate between them.
SOFT_STOPWORDS = frozenset(
    "repo repos repository repositories project projects library libraries tool tools starred github open source".split()
)
IN_COLUMNS = {
    "name": ("name", "owner"),
    "description": ("description",),
    "desc": ("description",),
    "topics": ("topics",),
    "readme": ("readme",),
    "notes": ("notes",),
}


@dataclass
class Query:
    raw: str
    text: str = ""                                   # free text without operators
    terms: list[str] = field(default_factory=list)   # content words (stopwords removed)
    all_terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    any_mode: bool = False                           # user wrote OR
    operators: dict[str, list[str]] = field(default_factory=dict)
    in_scopes: list[str] = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)
    expansions: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.terms or self.phrases)

    def to_schema(self) -> ParsedQuery:
        from ..api.schemas import TermCorrection

        return ParsedQuery(
            text=self.text,
            phrases=self.phrases,
            excluded=self.excluded,
            operators=self.operators,
            corrections=[TermCorrection(original=k, corrected=v) for k, v in self.corrections.items()],
            expansions=self.expansions,
        )


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _range(value: str) -> tuple[int | None, int | None]:
    """'>1000', '>=5', '<10', '100..500', '1k', '42' -> (min, max)."""

    def num(s: str) -> int | None:
        s = s.strip().lower().replace(",", "")
        mult = 1
        if s.endswith("k"):
            mult, s = 1_000, s[:-1]
        elif s.endswith("m"):
            mult, s = 1_000_000, s[:-1]
        try:
            return int(float(s) * mult)
        except ValueError:
            return None

    if ".." in value:
        lo, hi = value.split("..", 1)
        return (num(lo) if lo not in ("", "*") else None, num(hi) if hi not in ("", "*") else None)
    if value.startswith(">="):
        return num(value[2:]), None
    if value.startswith("<="):
        return None, num(value[2:])
    if value.startswith(">"):
        n = num(value[1:])
        return (n + 1 if n is not None else None), None
    if value.startswith("<"):
        n = num(value[1:])
        return None, (n - 1 if n is not None else None)
    n = num(value)
    return n, n


def _date_range(value: str) -> tuple[str | None, str | None]:
    if ".." in value:
        lo, hi = value.split("..", 1)
        return lo or None, hi or None
    if value.startswith(">="):
        return value[2:], None
    if value.startswith("<="):
        return None, value[2:]
    if value.startswith(">"):
        return value[1:], None
    if value.startswith("<"):
        return None, value[1:]
    return value, value


def parse_query(raw: str, base: RepoFilters | None = None) -> tuple[Query, RepoFilters]:
    """Split a query into free text and operators; operators are merged into the filters."""
    filters = (base or RepoFilters()).model_copy(deep=True)
    q = Query(raw=raw)
    free: list[str] = []
    for m in _TOKEN.finditer(raw.strip()):
        neg, op, quoted, bare = m.group(1), (m.group(2) or "").lower(), m.group(3), m.group(4)
        value = quoted if quoted is not None else (bare or "")
        if op and op in OPERATORS and value:
            q.operators.setdefault(op, []).append(value)
            _apply_operator(op, value, filters, q, negated=bool(neg))
            continue
        if op:                                   # unknown "foo:bar" -> ordinary text
            value = f"{op}:{value}" if quoted is None else value
        if quoted is not None and not op:
            if neg:
                q.excluded.extend(words(value))
            elif value.strip():
                q.phrases.append(value.strip())
                free.append(f'"{value.strip()}"')
            continue
        if value.upper() == "OR" and not neg:
            q.any_mode = True
            continue
        if value.upper() == "AND" and not neg:
            continue
        if neg and value:
            q.excluded.extend(words(value))
            continue
        free.append(value)
    q.text = " ".join(free).strip()
    plain = " ".join(t for t in free if not t.startswith('"'))
    q.all_terms = words(plain)
    content = [t for t in q.all_terms if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]
    strong = [t for t in content if t not in SOFT_STOPWORDS]
    q.terms = list(dict.fromkeys(strong or content or q.all_terms))
    return q, filters


def _apply_operator(op: str, value: str, f: RepoFilters, q: Query, *, negated: bool) -> None:
    v = value.strip()
    if op in ("lang", "language"):
        f.languages = _add(f.languages, v)
    elif op == "topic":
        f.topics = _add(f.topics, v)
    elif op == "license":
        f.licenses = _add(f.licenses, v)
    elif op in ("owner", "user", "org"):
        f.owners = _add(f.owners, v)
    elif op == "repo" and "/" in v:
        f.owners = _add(f.owners, v.split("/", 1)[0])
        q.phrases.append(v.split("/", 1)[1])
    elif op == "stars":
        lo, hi = _range(v)
        f.min_stars = lo if lo is not None else f.min_stars
        f.max_stars = hi if hi is not None else f.max_stars
    elif op == "size":
        lo, hi = _range(v)
        f.min_size_kb = lo if lo is not None else f.min_size_kb
        f.max_size_kb = hi if hi is not None else f.max_size_kb
    elif op == "pushed":
        lo, hi = _date_range(v)
        f.pushed_after = lo or f.pushed_after
        f.pushed_before = hi or f.pushed_before
    elif op == "starred":
        lo, hi = _date_range(v)
        f.starred_after = lo or f.starred_after
        f.starred_before = hi or f.starred_before
    elif op == "tag":
        f.tags = _add(f.tags, v)
    elif op == "category":
        f.categories = _add(f.categories, v)
    elif op == "collection":
        q.operators.setdefault("_collection_names", []).append(v)
    elif op == "in":
        q.in_scopes.append(v.lower())
    elif op == "is":
        flag = v.lower()
        yes = not negated
        if flag == "archived":
            f.archived = yes
        elif flag == "fork":
            f.fork = yes
        elif flag in ("favorite", "favourite", "fav"):
            f.favorite = yes
        elif flag == "unstarred":
            f.star_state = "unstarred" if yes else "starred"
        elif flag == "noted":
            f.has_note = yes
        elif flag in ("active", "maintained", "slowing", "stale"):
            f.maintenance = _add(f.maintenance, flag)
        elif flag in ("indexed", "pending", "failed"):
            f.index_status = _add(f.index_status, flag)
        elif flag == "deep":
            f.indexed_level = _add(f.indexed_level, "deep")


def _add(values: list[str], value: str) -> list[str]:
    return values if value.lower() in {x.lower() for x in values} else [*values, value]


# ---------------------------------------------------------------------------------- FTS5 expression


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def fts_expression(q: Query, *, columns: tuple[str, ...] | None = None, strict: bool | None = None) -> str | None:
    """Build an FTS5 MATCH expression.

    Short queries are conjunctive (every term required); longer natural-language queries are
    disjunctive and rely on BM25 to rank documents matching more terms first. Phrases are
    always required and excluded terms always removed. ``strict`` overrides the default.
    """
    groups: list[str] = []
    for term in q.terms:
        alts = [term]
        corrected = q.corrections.get(term)
        if corrected and corrected != term:
            alts.append(corrected)
        groups.append(_quote(alts[0]) if len(alts) == 1 else "(" + " OR ".join(_quote(a) for a in alts) + ")")
    if strict is None:
        strict = len(groups) <= 3 and not q.any_mode
    body = (" AND " if strict else " OR ").join(groups)
    extra = [e for e in q.expansions if e]
    if extra and body:
        body = f"({body}) OR " + " OR ".join(_quote(w) for e in extra for w in words(e))
    parts: list[str] = []
    if body:
        parts.append(f"({body})" if q.phrases or len(groups) > 1 else body)
    for phrase in q.phrases:
        ws = words(phrase)
        if ws:
            parts.append(_quote(" ".join(ws)))
    if not parts:
        return None
    expr = " AND ".join(parts)
    if columns:
        expr = "{" + " ".join(columns) + "}: (" + expr + ")"
    for term in q.excluded:
        expr += f" NOT {_quote(term)}"
    return expr


def in_columns(q: Query) -> tuple[str, ...] | None:
    cols: list[str] = []
    for scope in q.in_scopes:
        cols.extend(IN_COLUMNS.get(scope, ()))
    return tuple(dict.fromkeys(cols)) or None


def chunk_kinds_for(q: Query, scope: str) -> list[str] | None:
    """Restrict chunk retrieval by ``in:docs`` / ``in:code`` or by the request scope."""
    doc_kinds = ["readme", "doc", "example", "manifest", "config", "api"]
    if scope == "code" or "code" in q.in_scopes:
        return ["code", "api", "example"]
    if scope == "docs" or "docs" in q.in_scopes:
        return doc_kinds
    if "readme" in q.in_scopes:
        return ["readme"]
    return None
