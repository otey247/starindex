/**
 * Answers cite evidence as [E1], [E2, E5] or [E1][E2]. These helpers find citations and
 * rewrite them into markdown links (`#cite-E1`) that the markdown renderer turns into chips.
 */

const GROUP = /\[((?:E\d+)(?:\s*[,;]\s*E\d+)*)\]/g;

export function extractCitationRefs(text: string): string[] {
  const refs: string[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(GROUP)) {
    for (const ref of match[1]!.split(/\s*[,;]\s*/)) {
      if (!seen.has(ref)) {
        seen.add(ref);
        refs.push(ref);
      }
    }
  }
  return refs;
}

/** Replace [E#] groups outside of code spans/blocks with one link per reference. */
export function linkifyCitations(markdown: string): string {
  const parts = markdown.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part; // code: leave untouched
      return part
        .replace(GROUP, (_all, inner: string) =>
          inner
            .split(/\s*[,;]\s*/)
            .map((ref) => `[${ref}](#cite-${ref})`)
            .join(""),
        )
        .replace(/\[unverified\](?!\()/gi, "[unverified](#unverified)");
    })
    .join("");
}

export function citationRefFromHref(href: string | undefined): string | null {
  if (!href) return null;
  const m = /^#cite-(E\d+)$/.exec(href);
  return m ? m[1]! : null;
}

/** The server marks claims it could not tie to evidence with "[unverified]". */
export const UNVERIFIED_HREF = "#unverified";
