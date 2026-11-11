import { Cloud, Command, HardDrive, Menu as MenuIcon, RefreshCw, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { isSyncActive, useModelOptions, useSyncStatus, useSystemStatus } from "@/api/queries";
import { ChatGPTPlanNote, KIND_LABEL } from "@/components/ai/ModelControls";
import { EvidenceViewer } from "@/components/evidence/EvidenceViewer";
import { Button, Kbd, Tooltip } from "@/components/ui";
import { Toaster } from "@/components/ui/Toaster";
import { cn } from "@/lib/cn";
import { formatNumber, timeAgo } from "@/lib/format";
import { useHotkeys } from "@/lib/hotkeys";
import { useCompareTray, useUi } from "@/state/stores";
import { CommandPalette } from "./CommandPalette";
import { NAV } from "./nav";
import { ShortcutHelp } from "./ShortcutHelp";

export function Wordmark({ compact }: { compact?: boolean }) {
  return (
    <Link to="/search" className="flex items-center gap-2.5" aria-label="StarIndex home">
      <svg viewBox="0 0 24 24" className="size-[22px] shrink-0" aria-hidden>
        <path d="M12 2.6l2.5 5.7 6.2.6-4.7 4.1 1.4 6.1L12 15.9 6.6 19.1 8 13 3.3 8.9l6.2-.6z" fill="none" stroke="var(--color-accent)" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M7.5 22.2h9" stroke="var(--color-fg)" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
      {!compact && (
        <span className="text-[17px] font-semibold tracking-[-0.02em] text-fg">
          Star<span className="font-normal text-fg-3">Index</span>
        </span>
      )}
    </Link>
  );
}

function NavRail() {
  return (
    <nav aria-label="Primary" className="hidden h-dvh w-[60px] shrink-0 flex-col border-r border-line bg-surface md:flex lg:w-[212px]">
      <div className="flex h-12 items-center px-[18px] lg:px-4">
        <span className="lg:hidden">
          <Wordmark compact />
        </span>
        <span className="hidden lg:block">
          <Wordmark />
        </span>
      </div>
      <ul className="mt-2 flex flex-1 flex-col gap-0.5 px-2">
        {NAV.map((item, i) => (
          <li key={item.to} className={cn(i === 6 && "mt-auto")}>
            <NavLink
              to={item.to}
              title={item.label}
              className={({ isActive }) =>
                cn(
                  "group flex h-9 items-center gap-3 rounded-sm px-3 text-base transition-colors",
                  isActive ? "bg-overlay font-medium text-fg" : "text-fg-3 hover:bg-hover hover:text-fg",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cn("size-[17px] shrink-0", isActive ? "text-accent" : "")} strokeWidth={isActive ? 2.2 : 1.8} aria-hidden />
                  <span className="hidden flex-1 lg:inline">{item.label}</span>
                  <span className="hidden items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 lg:flex" aria-hidden>
                    <Kbd>g</Kbd>
                    <Kbd>{item.shortcut}</Kbd>
                  </span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="h-3" />
    </nav>
  );
}

function SyncIndicator() {
  const sync = useSyncStatus();
  const s = sync.data;
  if (!s) return null;
  const active = isSyncActive(s);
  const failed = s.queue.failed;
  let text: string;
  if (s.current_run) {
    const total = s.current_run.total_expected;
    text = `Syncing ${formatNumber(s.current_run.repos_seen)}${total ? ` / ${formatNumber(total)}` : ""}`;
  } else if (active) {
    const left = s.index.pending + s.index.queued + s.index.indexing;
    text = left > 0 ? `Indexing · ${formatNumber(left)} left` : `${formatNumber(s.queue.queued + s.queue.running)} jobs running`;
  } else if (!s.github.configured) {
    text = "GitHub not connected";
  } else {
    const last = s.last_incremental?.finished_at ?? s.last_full?.finished_at;
    text = last ? `Synced ${timeAgo(last)}` : "Not synced yet";
  }
  return (
    <Link
      to="/sync"
      className="flex h-7 items-center gap-1.5 rounded-sm px-2 text-xs text-fg-3 transition-colors hover:bg-hover hover:text-fg"
      title="Open Sync"
    >
      {failed > 0 && !active ? (
        <TriangleAlert className="size-3.5 text-warn" aria-hidden />
      ) : (
        <RefreshCw className={cn("size-3.5", active && "animate-spin text-accent")} aria-hidden />
      )}
      <span className="max-sm:hidden tnum">{text}</span>
      {failed > 0 && <span className="text-fg-2 max-sm:hidden">· {failed} failed</span>}
    </Link>
  );
}

/** Always-visible statement of which provider and model would answer right now. */
export function ActiveModel() {
  const options = useModelOptions();
  const a = options.data?.active;
  if (!options.data) return null;
  if (!a?.configured) {
    return (
      <Tooltip content="Search works without a model. Configure one to enable Ask AI and Research." side="bottom">
        <Link to="/settings/models" className="flex h-7 items-center gap-1.5 rounded-sm px-2 text-xs text-fg-3 hover:bg-hover hover:text-fg">
          <HardDrive className="size-3.5" aria-hidden />
          <span>No model · search only</span>
        </Link>
      </Tooltip>
    );
  }
  const isPlan = a.provider_kind === "chatgpt_plan";
  return (
    <div className="flex items-center gap-2">
      <Tooltip
        content={`${a.is_cloud ? "Cloud" : "Local"} model · ${KIND_LABEL[a.provider_kind ?? ""] ?? a.provider_kind}. Click to change.`}
        side="bottom"
      >
        <Link
          to="/settings/models"
          data-testid="active-model"
          className="flex h-7 min-w-0 items-center gap-1.5 rounded-sm px-2 text-xs text-fg-2 hover:bg-hover hover:text-fg"
        >
          {a.is_cloud ? <Cloud className="size-3.5 shrink-0" aria-hidden /> : <HardDrive className="size-3.5 shrink-0" aria-hidden />}
          <span className="max-w-[120px] truncate font-medium">{a.provider_name ?? a.provider_id}</span>
          <span className="mono max-w-[190px] truncate text-fg-3 max-sm:hidden">{a.model}</span>
        </Link>
      </Tooltip>
      {isPlan && <ChatGPTPlanNote className="max-lg:hidden" />}
    </div>
  );
}

function StatusStrip({ onMenu }: { onMenu: () => void }) {
  const setPalette = useUi((s) => s.setPalette);
  return (
    <header className="sticky top-0 z-40 flex h-12 shrink-0 items-center gap-2 border-b border-line bg-bg/95 px-3 backdrop-blur md:px-4">
      <div className="flex items-center gap-2 md:hidden">
        <Wordmark compact />
      </div>
      <button
        type="button"
        onClick={() => setPalette(true)}
        className="ml-1 flex h-8 min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-sm border border-line bg-surface px-2.5 text-left text-sm text-fg-4 transition-colors hover:border-line-strong hover:text-fg-3 md:ml-0 md:max-w-[360px]"
      >
        <Command className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate">Jump to repository or command…</span>
        <span className="ml-auto hidden items-center gap-0.5 sm:flex" aria-hidden>
          <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>
      <div className="ml-auto flex min-w-0 items-center gap-1">
        <SyncIndicator />
        <span className="h-4 w-px bg-line max-sm:hidden" aria-hidden />
        <ActiveModel />
        <button
          type="button"
          onClick={onMenu}
          aria-label="Open navigation"
          className="inline-flex size-8 cursor-pointer items-center justify-center rounded-sm text-fg-2 hover:bg-hover md:hidden"
        >
          <MenuIcon className="size-[18px]" />
        </button>
      </div>
    </header>
  );
}

function MobileNav({ open, onClose }: { open: boolean; onClose: () => void }) {
  const primary = NAV.slice(0, 4);
  return (
    <>
      <nav aria-label="Primary" className="fixed inset-x-0 bottom-0 z-40 flex h-[58px] items-stretch border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        {primary.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn("flex flex-1 flex-col items-center justify-center gap-0.5 text-[10.5px] font-medium", isActive ? "text-fg" : "text-fg-3")
            }
          >
            {({ isActive }) => (
              <>
                <item.icon className={cn("size-[19px]", isActive && "text-accent")} strokeWidth={isActive ? 2.2 : 1.8} aria-hidden />
                {item.label}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      {open && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/60 animate-fade" onClick={onClose} aria-hidden />
          <div className="absolute inset-y-0 right-0 flex w-[min(300px,86vw)] flex-col border-l border-line-strong bg-surface p-3 animate-drawer">
            <div className="flex items-center justify-between px-1 pb-3">
              <Wordmark />
              <button type="button" onClick={onClose} aria-label="Close navigation" className="inline-flex size-8 cursor-pointer items-center justify-center rounded-sm text-fg-3 hover:bg-hover">
                <X className="size-4" />
              </button>
            </div>
            <ul className="flex flex-col gap-0.5">
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    onClick={onClose}
                    className={({ isActive }) =>
                      cn("flex h-11 items-center gap-3 rounded-sm px-3 text-md", isActive ? "bg-overlay font-medium text-fg" : "text-fg-2")
                    }
                  >
                    <item.icon className="size-[18px]" aria-hidden />
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

/** Floating tray for repositories queued for comparison; survives navigation. */
function CompareTray() {
  const ids = useCompareTray((s) => s.ids);
  const names = useCompareTray((s) => s.names);
  const remove = useCompareTray((s) => s.remove);
  const clear = useCompareTray((s) => s.clear);
  const navigate = useNavigate();
  const location = useLocation();
  if (ids.length === 0 || location.pathname.startsWith("/compare")) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex justify-center px-3 max-md:bottom-[70px]">
      <div className="pointer-events-auto flex max-w-full items-center gap-2 rounded-lg border border-line-strong bg-overlay py-1.5 pl-3 pr-1.5 shadow-pop animate-rise">
        <span className="label shrink-0">Compare</span>
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
          {ids.map((id) => (
            <span key={id} className="inline-flex h-6 shrink-0 items-center gap-1 rounded-[4px] border border-line bg-raised pl-1.5 pr-1 text-xs text-fg-2">
              <span className="max-w-[150px] truncate">{names[id]?.split("/")[1] ?? id}</span>
              <button type="button" onClick={() => remove(id)} aria-label={`Remove ${names[id] ?? id}`} className="cursor-pointer text-fg-4 hover:text-fg">
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
        <Button size="sm" variant="ghost" onClick={clear}>
          Clear
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={ids.length < 2}
          onClick={() => navigate(`/compare?ids=${ids.join(",")}`)}
          title={ids.length < 2 ? "Select at least two repositories" : undefined}
        >
          Compare {ids.length}
        </Button>
      </div>
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const status = useSystemStatus();
  const [menuOpen, setMenuOpen] = useState(false);
  const setPalette = useUi((s) => s.setPalette);
  const setShortcuts = useUi((s) => s.setShortcuts);

  // First run: take the user through connecting GitHub and the first sync.
  const firstRun = status.data?.first_run === true;
  useEffect(() => {
    if (firstRun && !location.pathname.startsWith("/welcome") && sessionStorage.getItem("starindex.skipOnboarding") !== "1") {
      navigate("/welcome", { replace: true });
    }
  }, [firstRun, location.pathname, navigate]);

  useHotkeys([
    { keys: "mod+k", handler: () => setPalette(true), allowInInput: true },
    { keys: "?", handler: () => setShortcuts(true) },
    {
      keys: "/",
      handler: () => {
        const input = document.querySelector<HTMLInputElement>("[data-search-input]");
        if (input) {
          input.focus();
          input.select();
        } else {
          navigate("/search?focus=1");
        }
      },
    },
    ...NAV.map((item) => ({ keys: `g ${item.shortcut}`, handler: () => navigate(item.to) })),
  ]);

  return (
    <div className="flex h-dvh overflow-hidden">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded-sm focus:bg-accent-solid focus:px-3 focus:py-1.5 focus:text-white">
        Skip to content
      </a>
      <NavRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <StatusStrip onMenu={() => setMenuOpen(true)} />
        <main id="main" className="min-h-0 flex-1 overflow-y-auto pb-[58px] md:pb-0" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <MobileNav open={menuOpen} onClose={() => setMenuOpen(false)} />
      <CompareTray />
      <CommandPalette />
      <ShortcutHelp />
      <EvidenceViewer />
      <Toaster />
    </div>
  );
}
