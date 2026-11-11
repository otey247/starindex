/**
 * Sanitised markdown for untrusted repository content and model output.
 *
 * - Raw HTML in the source is never executed (react-markdown ignores it; rehype-sanitize
 *   is a second line of defence with the default GitHub-style schema).
 * - Links open in a new tab with rel="noopener noreferrer"; only http(s)/mailto survive.
 * - Remote images are never fetched: READMEs routinely embed trackers and badges, the app
 *   must work offline, and the server's Content-Security-Policy forbids third-party image
 *   hosts. An image renders as a labelled link that opens in the browser.
 * - `[E3]`-style citations become chips when `onCite` is supplied.
 */
import { memo, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { ImageOff } from "lucide-react";
import { citationRefFromHref, linkifyCitations, UNVERIFIED_HREF } from "@/lib/citations";
import { cn } from "@/lib/cn";

const schema = {
  ...defaultSchema,
  protocols: { ...defaultSchema.protocols, href: ["http", "https", "mailto"], src: ["http", "https"] },
};

function safeHref(href: string | undefined, base?: string): string | null {
  if (!href) return null;
  if (/^(https?:|mailto:)/i.test(href)) return href;
  if (href.startsWith("#")) return null;
  if (base) {
    try {
      const url = new URL(href, base);
      return /^https?:$/.test(url.protocol) ? url.toString() : null;
    } catch {
      return null;
    }
  }
  return null;
}

export interface MarkdownProps {
  children: string;
  className?: string;
  compact?: boolean;
  /** Resolve relative links/images against this URL (e.g. the repo's GitHub blob URL). */
  baseUrl?: string;
  onCite?: (ref: string) => void;
  /** Refs that exist in the evidence list; others render as unverified. */
  knownRefs?: Set<string>;
  streaming?: boolean;
}

export const Markdown = memo(function Markdown({
  children,
  className,
  compact,
  baseUrl,
  onCite,
  knownRefs,
  streaming,
}: MarkdownProps) {
  const source = useMemo(() => (onCite ? linkifyCitations(children) : children), [children, onCite]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children: kids }) {
        if (href === UNVERIFIED_HREF && onCite) {
          return (
            <span className="label mx-0.5 !text-fg-2" title="StarIndex could not tie this statement to indexed evidence." data-testid="unverified-mark">
              ◌ unverified
            </span>
          );
        }
        const ref = citationRefFromHref(href);
        if (ref && onCite) {
          const known = !knownRefs || knownRefs.has(ref);
          return (
            <button
              type="button"
              onClick={() => known && onCite(ref)}
              title={known ? `Open evidence ${ref}` : `${ref} is not in the evidence list`}
              aria-label={`Evidence ${ref}`}
              className={cn(
                "mx-[1px] inline-flex h-[18px] translate-y-[-1px] items-center rounded-[4px] border px-1 align-middle font-mono text-[10.5px] font-medium leading-none transition-colors",
                known
                  ? "cursor-pointer border-line-strong bg-raised text-accent hover:border-accent hover:text-fg"
                  : "cursor-not-allowed border-line bg-transparent text-fg-4 line-through",
              )}
            >
              {ref}
            </button>
          );
        }
        const safe = safeHref(href, baseUrl);
        if (!safe) return <span>{kids}</span>;
        return (
          <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
            {kids}
          </a>
        );
      },
      img({ src, alt }) {
        const safe = safeHref(typeof src === "string" ? src : undefined, baseUrl);
        if (!safe) return <span className="text-fg-4">[image{alt ? `: ${alt}` : ""}]</span>;
        return (
          <a
            href={safe}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="inline-flex items-center gap-1 rounded-[4px] border border-line bg-raised px-1.5 py-0.5 align-middle text-xs !text-fg-3 hover:!text-fg hover:!no-underline"
            title="Image not loaded. Open in browser."
          >
            <ImageOff className="size-3" aria-hidden />
            {alt?.trim() || "image"}
          </a>
        );
      },
      h1({ children: kids }) {
        return <h2 data-md-level="1">{kids as ReactNode}</h2>;
      },
      table({ children: kids }) {
        return <table>{kids as ReactNode}</table>;
      },
    }),
    [baseUrl, onCite, knownRefs],
  );

  return (
    <div className={cn("prose-si", compact && "compact", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeSanitize, schema]]} components={components}>
        {source}
      </ReactMarkdown>
      {streaming && <span className="ml-0.5 inline-block h-[1.05em] w-[7px] translate-y-[3px] bg-accent animate-caret" aria-hidden />}
    </div>
  );
});
