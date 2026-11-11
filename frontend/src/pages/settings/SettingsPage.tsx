import { Activity, Clock, Database, HardDrive, Layers, MessageCircle, Search, ShieldCheck, type LucideIcon } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { GithubMark } from "@/components/ui/GithubMark";
import { cn } from "@/lib/cn";
import ChatGPTSettings from "./ChatGPTSettings";
import { DiagnosticsSettings, GitHubSettings, IndexingSettings, PrivacySettings, ScheduleSettings, SearchSettings, StorageSettings } from "./GeneralSettings";
import ModelSettings from "./ModelSettings";

const SECTIONS: { to: string; label: string; icon: LucideIcon | typeof GithubMark }[] = [
  { to: "github", label: "GitHub access", icon: GithubMark },
  { to: "indexing", label: "Indexing", icon: Layers },
  { to: "search", label: "Search & embeddings", icon: Search },
  { to: "models", label: "Model providers", icon: HardDrive },
  { to: "chatgpt", label: "ChatGPT plan", icon: MessageCircle },
  { to: "privacy", label: "Privacy", icon: ShieldCheck },
  { to: "storage", label: "Storage & backups", icon: Database },
  { to: "schedules", label: "Schedules", icon: Clock },
  { to: "diagnostics", label: "Diagnostics", icon: Activity },
];

export default function SettingsPage() {
  return (
    <div className="mx-auto grid w-full max-w-[1280px] gap-x-12 px-4 pb-24 pt-6 md:px-8 grid-cols-1 lg:grid-cols-[212px_minmax(0,1fr)]">
      <aside className="min-w-0">
        <h1 className="text-2xl font-semibold text-fg">Settings</h1>
        <nav aria-label="Settings sections" className="mt-4 flex gap-1 overflow-x-auto pb-2 lg:sticky lg:top-4 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:pb-0">
          {SECTIONS.map((s) => (
            <NavLink
              key={s.to}
              to={s.to}
              className={({ isActive }) =>
                cn(
                  "flex h-8 shrink-0 items-center gap-2.5 whitespace-nowrap rounded-sm px-2.5 text-sm transition-colors",
                  isActive ? "bg-overlay font-medium text-fg" : "text-fg-3 hover:bg-hover hover:text-fg",
                )
              }
            >
              <s.icon className="size-3.5 shrink-0" />
              {s.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 max-w-[900px] pt-4 lg:pt-[52px]">
        <Routes>
          <Route index element={<Navigate to="github" replace />} />
          <Route path="github" element={<GitHubSettings />} />
          <Route path="indexing" element={<IndexingSettings />} />
          <Route path="search" element={<SearchSettings />} />
          <Route path="models" element={<ModelSettings />} />
          <Route path="chatgpt" element={<ChatGPTSettings />} />
          <Route path="privacy" element={<PrivacySettings />} />
          <Route path="storage" element={<StorageSettings />} />
          <Route path="schedules" element={<ScheduleSettings />} />
          <Route path="diagnostics" element={<DiagnosticsSettings />} />
          <Route path="*" element={<Navigate to="github" replace />} />
        </Routes>
      </div>
    </div>
  );
}
