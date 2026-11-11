import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownLeft, FlaskConical, MessagesSquare, RefreshCw, Search as SearchIcon, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { keys } from "@/api/queries";
import { Kbd } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";
import { useDebounced } from "@/lib/useDebounced";
import { toast, useUi } from "@/state/stores";
import { NAV } from "./nav";

interface Command {
  id: string;
  group: string;
  title: string;
  detail?: string;
  icon?: LucideIcon;
  meta?: string;
  run: () => void;
}

export function CommandPalette() {
  const open = useUi((s) => s.paletteOpen);
  const setOpen = useUi((s) => s.setPalette);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const debounced = useDebounced(q.trim(), 140);

  const repos = useQuery({
    queryKey: ["palette", debounced],
    queryFn: ({ signal }) => api.search({ query: debounced, mode: "keyword", limit: 7, record: false }, signal),
    enabled: open && debounced.length > 1,
    staleTime: 20_000,
  });
  const sync = useMutation({
    mutationFn: (kind: "full" | "incremental") => api.startSync(kind),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.sync });
      toast.success("Sync started.");
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  useEffect(() => {
    if (open) {
      setQ("");
      setCursor(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    const list: Command[] = [];
    const text = q.trim();
    if (text) {
      list.push(
        { id: "search", group: "Query", title: `Search for “${text}”`, icon: SearchIcon, meta: "Hybrid", run: go(`/search?q=${encodeURIComponent(text)}`) },
        { id: "ask", group: "Query", title: `Ask AI: “${text}”`, icon: MessagesSquare, run: go(`/ask?q=${encodeURIComponent(text)}`) },
        { id: "research", group: "Query", title: `Research: “${text}”`, icon: FlaskConical, run: go(`/research?q=${encodeURIComponent(text)}`) },
      );
      for (const r of repos.data?.results ?? []) {
        list.push({
          id: `repo-${r.repo.id}`,
          group: "Repositories",
          title: r.repo.full_name,
          detail: r.repo.description ?? undefined,
          meta: `★ ${formatCount(r.repo.stars)}`,
          run: go(`/repo/${r.repo.id}`),
        });
      }
    }
    const lower = text.toLowerCase();
    const match = (s: string) => !lower || s.toLowerCase().includes(lower);
    for (const item of NAV) {
      if (match(item.label)) list.push({ id: `nav-${item.to}`, group: "Go to", title: item.label, icon: item.icon, meta: `g ${item.shortcut}`, run: go(item.to) });
    }
    const actions: Command[] = [
      { id: "sync-inc", group: "Actions", title: "Sync new stars now", icon: RefreshCw, run: () => sync.mutate("incremental") },
      { id: "sync-full", group: "Actions", title: "Run a full sync", icon: RefreshCw, run: () => sync.mutate("full") },
      { id: "models", group: "Actions", title: "Model providers and routing", run: go("/settings/models") },
      { id: "privacy", group: "Actions", title: "Privacy and cloud access", run: go("/settings/privacy") },
      { id: "reports", group: "Actions", title: "Saved reports and comparisons", run: go("/collections?tab=reports") },
      { id: "dupes", group: "Actions", title: "Find duplicates and forks", run: go("/library?view=duplicates") },
      { id: "diag", group: "Actions", title: "Diagnostics", run: go("/settings/diagnostics") },
    ];
    for (const a of actions) if (match(a.title)) list.push(a);
    return list;
  }, [q, repos.data, navigate, sync]);

  useEffect(() => setCursor(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (!open) return null;

  const run = (cmd: Command | undefined) => {
    if (!cmd) return;
    setOpen(false);
    cmd.run();
  };

  let lastGroup = "";
  return createPortal(
    <div className="fixed inset-0 z-[85] flex items-start justify-center p-3 pt-[11vh]">
      <div className="fixed inset-0 bg-black/65 animate-fade" onClick={() => setOpen(false)} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Command palette" className="relative flex max-h-[70vh] w-full max-w-[640px] flex-col overflow-hidden rounded-lg border border-line-strong bg-surface shadow-pop animate-pop">
        <div className="flex items-center gap-2.5 border-b border-line px-4">
          <SearchIcon className="size-4 shrink-0 text-fg-3" aria-hidden />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search repositories, ask a question, or run a command"
            aria-label="Command palette input"
            className="h-12 min-w-0 flex-1 bg-transparent text-md text-fg outline-none placeholder:text-fg-4"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, commands.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                run(commands[cursor]);
              } else if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setOpen(false);
              }
            }}
          />
          <Kbd>Esc</Kbd>
        </div>
        <div ref={listRef} className="min-h-0 overflow-y-auto py-1.5" role="listbox">
          {commands.length === 0 && <p className="px-4 py-6 text-sm text-fg-3">No matching commands.</p>}
          {commands.map((cmd, i) => {
            const header = cmd.group !== lastGroup ? cmd.group : null;
            lastGroup = cmd.group;
            const Icon = cmd.icon;
            return (
              <div key={cmd.id}>
                {header && <p className="label px-4 pb-1 pt-2">{header}</p>}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === cursor}
                  data-index={i}
                  onMouseMove={() => setCursor(i)}
                  onClick={() => run(cmd)}
                  className={cn("flex w-full cursor-pointer items-center gap-3 px-4 py-2 text-left", i === cursor && "bg-overlay")}
                >
                  <span className="flex size-4 shrink-0 items-center justify-center text-fg-3">{Icon ? <Icon className="size-4" aria-hidden /> : <span className="size-1 rounded-full bg-fg-4" />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base text-fg">{cmd.title}</span>
                    {cmd.detail && <span className="block truncate text-xs text-fg-3">{cmd.detail}</span>}
                  </span>
                  {cmd.meta && <span className="mono shrink-0 text-2xs text-fg-4">{cmd.meta}</span>}
                  {i === cursor && <CornerDownLeft className="size-3.5 shrink-0 text-fg-3" aria-hidden />}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
