import type { ReactNode } from "react";

/**
 * The search API marks hits in plain text with «…» guillemets. Split such a string into
 * alternating plain/hit segments. Unbalanced markers degrade gracefully to plain text.
 */
export interface Segment {
  text: string;
  hit: boolean;
}

export function splitHighlights(input: string): Segment[] {
  const segments: Segment[] = [];
  let i = 0;
  while (i < input.length) {
    const open = input.indexOf("«", i);
    if (open === -1) {
      segments.push({ text: input.slice(i), hit: false });
      break;
    }
    const close = input.indexOf("»", open + 1);
    if (close === -1) {
      // Unbalanced: drop the stray marker and treat the rest as plain text.
      segments.push({ text: input.slice(i, open) + input.slice(open + 1), hit: false });
      break;
    }
    if (open > i) segments.push({ text: input.slice(i, open), hit: false });
    const inner = input.slice(open + 1, close).replaceAll("«", "");
    if (inner) segments.push({ text: inner, hit: true });
    i = close + 1;
  }
  return segments
    .map((s) => ({ ...s, text: s.hit ? s.text : s.text.replaceAll("»", "") }))
    .filter((s) => s.text.length > 0);
}

export function stripHighlights(input: string): string {
  return input.replaceAll("«", "").replaceAll("»", "");
}

/** Render «marked» text as React nodes. Text is never interpreted as HTML. */
export function Highlighted({ text }: { text: string }): ReactNode {
  const segments = splitHighlights(text);
  return (
    <>
      {segments.map((s, i) =>
        s.hit ? (
          <mark key={i} className="hit">
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

/**
 * Snippets come from raw Markdown or code. For one-line previews, drop the Markdown
 * punctuation that only adds noise (headings, emphasis, badges, link targets) while
 * keeping the «hit» markers intact.
 */
export function cleanSnippet(input: string): string {
  return input
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "") // images / badges
    .replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, "$1") // links -> text
    .replace(/(^|\s)#{1,6}\s+/g, "$1") // heading markers
    .replace(/(\*\*|__)(?=\S)/g, "")
    .replace(/(?<=\S)(\*\*|__)/g, "")
    .replace(/`{1,3}/g, "")
    .replace(/\s*\|\s*(?:-{3,}\s*\|\s*)+/g, " ") // table rules
    .replace(/\s*\|(?:\s*\|)*\s*/g, " · ") // table cell separators
    .replace(/^(?:\s*·\s*)+|(?:\s*·\s*)+$/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}
