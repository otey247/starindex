import { ArrowLeftRight, FlaskConical, FolderKanban, Library, MessagesSquare, RefreshCw, Search, Settings, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  shortcut: string; // second key of the "g x" sequence
}

export const NAV: NavItem[] = [
  { to: "/search", label: "Search", icon: Search, shortcut: "s" },
  { to: "/library", label: "Library", icon: Library, shortcut: "l" },
  { to: "/ask", label: "Ask AI", icon: MessagesSquare, shortcut: "a" },
  { to: "/research", label: "Research", icon: FlaskConical, shortcut: "r" },
  { to: "/compare", label: "Compare", icon: ArrowLeftRight, shortcut: "c" },
  { to: "/collections", label: "Collections", icon: FolderKanban, shortcut: "o" },
  { to: "/sync", label: "Sync", icon: RefreshCw, shortcut: "y" },
  { to: "/settings", label: "Settings", icon: Settings, shortcut: "," },
];
