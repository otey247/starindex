import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookMarked, Clock, Code2, FlaskConical, Hash, MessagesSquare, Search as SearchIcon, SlidersHorizontal, X } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import type { Suggestion } from "@/api/types";
import { Kbd } from "@/components/ui";
import { cn } from "@/lib/cn";
import { matchOperators, replaceToken, tokenAt } from "@/lib/query";
import { useDebounced } from "@/lib/useDebounced";

export type BarMode = "keyword" | "semantic" | "hybrid" | "ask" | "research";

export const BAR_MODES: { value: BarMode; label: string; hint: string }[] = [
  { value: "keyword", label: "Keyword", hint: "Exact terms, phrases and operators. Typo tolerant." },
  { value: "semantic", label: "Semantic", hint: "Finds conceptually related repositories even without shared words." },
  { value: "hybrid", label: "Hybrid", hint: "Lexical and vector retrieval fused, then reranked." },
  { value: "ask", label: "Ask", hint: "Retrieves evidence and writes a cited answer." },
  { value: "research", label: "Research", hint: "Bounded multi-step investigation across repositories." },
];

interface Row {
  key: string;
  icon: ReactNode;
  text: string;
  detail?: string | null;
  apply: () => void;
}

function suggestionIcon(kind: Suggestion["kind"]): ReactNode {
  switch (kind) {
    case "repo":
      return <BookMarked className="size-3.5" />;
    case "topic":
      return <Hash className="size-3.5" />;
    case "language":
      return <Code2 className="size-3.5" />;
    case "history":
      return <Clock className="size-3.5" />;
    case "saved":
      return <BookMarked className="size-3.5" />;
    default:
      return <SlidersHorizontal className="size-3.5" />;
  }
}

export interface SearchBarHandle {
  focus: () => void;
}

export interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  mode: BarMode;
  onMode: (mode: BarMode) => void;
  onOpenRepo?: (repoId: number) => void;
  loading?: boolean;
  autoFocus?: boolean;
}

/** The query bar: input, mode selector, operator hints and server suggestions. */
export const SearchBar = forwardRef<SearchBarHandle, SearchBarProps>(function SearchBar(
  { value, onChange, onSubmit, mode, onMode, onOpenRepo, loading, autoFocus },
  ref,
) {
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(-1);
  const [caret, setCaret] = useState(0);
  const debounced = useDebounced(value, 150);

  useImperativeHandle(ref, () => ({ focus: () => inputRef.current?.focus() }));
  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const suggestions = useQuery({
    queryKey: ["suggest", debounced],
    queryFn: ({ signal }) => api.suggest(debounced, signal),
    enabled: open,
    staleTime: 30_000,
  });

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    const token = tokenAt(value, caret);
    const fragment = token && token.kind === "term" ? token.text : "";
    for (const op of matchOperators(fragment).slice(0, 4)) {
      out.push({
        key: `op-${op.op}`,
        icon: <SlidersHorizontal className="size-3.5" />,
        text: op.op,
        detail: `${op.description} · e.g. ${op.example}`,
        apply: () => {
          const next = replaceToken(value, caret, op.op);
          onChange(next.text);
          requestAnimationFrame(() => {
            inputRef.current?.setSelectionRange(next.caret, next.caret);
            setCaret(next.caret);
          });
        },
      });
    }
    for (const s of suggestions.data ?? []) {
      out.push({
        key: `${s.kind}-${s.text}-${s.repo_id ?? ""}`,
        icon: suggestionIcon(s.kind),
        text: s.text,
        detail: s.detail,
        apply: () => {
          if (s.kind === "repo" && s.repo_id && onOpenRepo) {
            onOpenRepo(s.repo_id);
          } else if (s.kind === "history" || s.kind === "saved") {
            onChange(s.text);
            onSubmit(s.text);
            setOpen(false);
          } else {
            const next = replaceToken(value, caret, s.text.includes(" ") && !s.text.includes(":") ? `"${s.text}"` : s.text);
            onChange(`${next.text} `.replace(/\s+$/, " "));
            requestAnimationFrame(() => inputRef.current?.focus());
          }
        },
      });
    }
    return out.slice(0, 10);
  }, [value, caret, suggestions.data, onChange, onSubmit, onOpenRepo]);

  useEffect(() => setCursor(-1), [value]);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const submit = () => {
    setOpen(false);
    onSubmit(value);
  };

  const handoff = mode === "ask" || mode === "research";
  const placeholder =
    mode === "ask"
      ? "Ask a question about your starred repositories"
      : mode === "research"
        ? "Describe what you want investigated across your stars"
        : "Search names, descriptions, topics, READMEs, docs and code";

  return (
    <div ref={wrapRef} className="relative">
      <div
        className={cn(
          "flex h-[52px] items-center gap-3 rounded-lg border bg-surface px-4 transition-colors",
          "border-line-strong focus-within:border-accent hover:border-fg-4 focus-within:hover:border-accent",
        )}
      >
        {mode === "ask" ? (
          <MessagesSquare className="size-[18px] shrink-0 text-fg-3" aria-hidden />
        ) : mode === "research" ? (
          <FlaskConical className="size-[18px] shrink-0 text-fg-3" aria-hidden />
        ) : (
          <SearchIcon className="size-[18px] shrink-0 text-fg-3" aria-hidden />
        )}
        <input
          ref={inputRef}
          data-search-input
          type="text"
          role="combobox"
          aria-expanded={open && rows.length > 0}
          aria-controls="search-suggestions"
          aria-autocomplete="list"
          aria-label="Search query"
          autoComplete="off"
          spellCheck={false}
          value={value}
          placeholder={placeholder}
          onChange={(e) => {
            onChange(e.target.value);
            setCaret(e.target.selectionStart ?? e.target.value.length);
            setOpen(true);
          }}
          onClick={(e) => {
            setCaret(e.currentTarget.selectionStart ?? 0);
            setOpen(true);
          }}
          onKeyUp={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          onKeyDown={(e) => {
            if (e.altKey && /^[1-5]$/.test(e.key)) {
              e.preventDefault();
              onMode(BAR_MODES[Number(e.key) - 1]!.value);
              return;
            }
            if (e.key === "ArrowDown" && rows.length) {
              e.preventDefault();
              setOpen(true);
              setCursor((c) => (c + 1) % rows.length);
            } else if (e.key === "ArrowUp" && rows.length) {
              e.preventDefault();
              setCursor((c) => (c <= 0 ? rows.length - 1 : c - 1));
            } else if (e.key === "Tab" && open && cursor >= 0 && rows[cursor]) {
              e.preventDefault();
              rows[cursor].apply();
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (open && cursor >= 0 && rows[cursor]) rows[cursor].apply();
              else submit();
            } else if (e.key === "Escape") {
              if (open) {
                e.stopPropagation();
                setOpen(false);
              } else {
                inputRef.current?.blur();
              }
            }
          }}
          className="h-full min-w-0 flex-1 bg-transparent text-lg text-fg outline-none placeholder:text-fg-4"
        />
        {loading && <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-line-strong border-t-accent" aria-label="Searching" />}
        {value && (
          <button
            type="button"
            aria-label="Clear query"
            onClick={() => {
              onChange("");
              inputRef.current?.focus();
            }}
            className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm text-fg-4 hover:bg-hover hover:text-fg"
          >
            <X className="size-3.5" />
          </button>
        )}
        <span className="hidden shrink-0 sm:block" aria-hidden>
          <Kbd>/</Kbd>
        </span>
        <button
          type="button"
          onClick={submit}
          className={cn(
            "inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-sm px-3 text-sm font-medium text-white transition-colors",
            "bg-accent-solid hover:bg-accent-solid-hover",
          )}
        >
          {handoff ? (mode === "ask" ? "Ask" : "Research") : "Search"}
          {handoff && <ArrowRight className="size-3.5" aria-hidden />}
        </button>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <div role="radiogroup" aria-label="Search mode" className="flex items-center gap-0.5">
          {BAR_MODES.map((m, i) => {
            const active = m.value === mode;
            return (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={active}
                title={`${m.hint} (Alt+${i + 1})`}
                onClick={() => onMode(m.value)}
                className={cn(
                  "relative h-7 cursor-pointer rounded-sm px-2.5 text-sm font-medium transition-colors",
                  active ? "bg-overlay text-fg" : "text-fg-3 hover:text-fg",
                  i === 3 && "ml-2",
                )}
              >
                {m.label}
              </button>
            );
          })}
        </div>
        <p className="min-w-0 flex-1 truncate text-xs text-fg-4 max-sm:hidden">{BAR_MODES.find((m) => m.value === mode)?.hint}</p>
      </div>

      {open && rows.length > 0 && (
        <ul
          id="search-suggestions"
          role="listbox"
          className="absolute inset-x-0 top-[56px] z-30 overflow-hidden rounded-md border border-line-strong bg-overlay py-1 shadow-pop animate-pop"
        >
          {rows.map((row, i) => (
            <li key={row.key} role="option" aria-selected={i === cursor}>
              <button
                type="button"
                onMouseMove={() => cursor !== i && setCursor(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={row.apply}
                className={cn("flex w-full cursor-pointer items-center gap-3 px-4 py-1.5 text-left", i === cursor && "bg-line")}
              >
                <span className="shrink-0 text-fg-4">{row.icon}</span>
                <span className="mono shrink-0 text-sm text-fg">{row.text}</span>
                {row.detail && <span className="min-w-0 truncate text-xs text-fg-3">{row.detail}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
