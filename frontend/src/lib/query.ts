/**
 * Client-side awareness of search operators. The server is authoritative for parsing;
 * this only powers hints/autocomplete in the query bar.
 */

export interface OperatorHint {
  op: string;
  example: string;
  description: string;
}

export const OPERATORS: OperatorHint[] = [
  { op: "lang:", example: "lang:python", description: "Programming language" },
  { op: "topic:", example: "topic:mcp", description: "GitHub topic" },
  { op: "license:", example: "license:mit", description: "SPDX license id" },
  { op: "owner:", example: "owner:vercel", description: "Repository owner" },
  { op: "stars:", example: "stars:>1000", description: "Star count, also 100..5000" },
  { op: "pushed:", example: "pushed:>2025-01-01", description: "Last push date" },
  { op: "starred:", example: "starred:<2024-06-01", description: "When you starred it" },
  { op: "is:", example: "is:archived", description: "archived · fork · favorite · unstarred" },
  { op: "tag:", example: "tag:todo", description: "Your tag" },
  { op: "category:", example: "category:mcp-servers", description: "Category slug" },
  { op: "collection:", example: 'collection:"Agent stack"', description: "One of your collections" },
  { op: "in:", example: "in:readme", description: "name · description · readme · docs · code" },
];

const OPERATOR_NAMES = new Set(OPERATORS.map((o) => o.op.slice(0, -1)));

export interface QueryToken {
  text: string;
  start: number;
  end: number;
  kind: "term" | "phrase" | "operator" | "excluded" | "or";
  operator?: string;
  value?: string;
}

/** Tokenise a query string the way the query bar needs it (quotes kept together). */
export function tokenizeQuery(query: string): QueryToken[] {
  const tokens: QueryToken[] = [];
  const re = /(-?[a-zA-Z_]+:)?"[^"]*"?|\S+/g;
  for (const m of query.matchAll(re)) {
    const text = m[0];
    const start = m.index ?? 0;
    const end = start + text.length;
    const colon = text.indexOf(":");
    const name = colon > 0 ? text.slice(0, colon).replace(/^-/, "").toLowerCase() : "";
    if (colon > 0 && OPERATOR_NAMES.has(name)) {
      tokens.push({ text, start, end, kind: "operator", operator: name, value: text.slice(colon + 1).replace(/^"|"$/g, "") });
    } else if (text === "OR") {
      tokens.push({ text, start, end, kind: "or" });
    } else if (text.startsWith('"')) {
      tokens.push({ text, start, end, kind: "phrase", value: text.replace(/^"|"$/g, "") });
    } else if (text.startsWith("-") && text.length > 1) {
      tokens.push({ text, start, end, kind: "excluded", value: text.slice(1) });
    } else {
      tokens.push({ text, start, end, kind: "term" });
    }
  }
  return tokens;
}

/** The token under the caret, used to decide which suggestions to offer. */
export function tokenAt(query: string, caret: number): QueryToken | null {
  for (const t of tokenizeQuery(query)) {
    if (caret >= t.start && caret <= t.end) return t;
  }
  return null;
}

/** Operator hints matching what is being typed (e.g. "la" -> lang:). */
export function matchOperators(fragment: string): OperatorHint[] {
  const f = fragment.toLowerCase();
  if (!f || f.includes(":")) return [];
  return OPERATORS.filter((o) => o.op.startsWith(f));
}

/** Replace the token under the caret with `replacement`; returns new text and caret. */
export function replaceToken(query: string, caret: number, replacement: string): { text: string; caret: number } {
  const token = tokenAt(query, caret);
  if (!token) {
    const sep = query && !query.endsWith(" ") ? " " : "";
    const text = `${query}${sep}${replacement}`;
    return { text, caret: text.length };
  }
  const text = query.slice(0, token.start) + replacement + query.slice(token.end);
  return { text, caret: token.start + replacement.length };
}

export function hasOperators(query: string): boolean {
  return tokenizeQuery(query).some((t) => t.kind === "operator");
}
