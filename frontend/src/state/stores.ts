import { create } from "zustand";
import type { Evidence } from "@/api/types";

// ------------------------------------------------------------------ toasts

export interface Toast {
  id: number;
  kind: "info" | "success" | "error";
  message: string;
  action?: { label: string; onClick: () => void };
}

interface ToastState {
  toasts: Toast[];
  push: (toast: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
}

let toastId = 0;

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (toast) => {
    const id = ++toastId;
    set({ toasts: [...get().toasts.slice(-3), { ...toast, id }] });
    setTimeout(() => get().dismiss(id), toast.kind === "error" ? 8000 : 4200);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = {
  info: (message: string, action?: Toast["action"]) => useToasts.getState().push({ kind: "info", message, action }),
  success: (message: string, action?: Toast["action"]) => useToasts.getState().push({ kind: "success", message, action }),
  error: (message: string, action?: Toast["action"]) => useToasts.getState().push({ kind: "error", message, action }),
};

// ------------------------------------------------------------------ evidence viewer

export type EvidenceTarget =
  | { type: "evidence"; evidence: Evidence }
  | { type: "chunk"; chunkId: number; label?: string };

interface EvidenceViewerState {
  target: EvidenceTarget | null;
  open: (target: EvidenceTarget) => void;
  close: () => void;
}

export const useEvidenceViewer = create<EvidenceViewerState>((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}));

// ------------------------------------------------------------------ compare tray (selection that survives navigation)

interface CompareTrayState {
  ids: number[];
  names: Record<number, string>;
  toggle: (id: number, fullName: string) => void;
  remove: (id: number) => void;
  clear: () => void;
}

export const MAX_COMPARE = 6;

export const useCompareTray = create<CompareTrayState>((set, get) => ({
  ids: [],
  names: {},
  toggle: (id, fullName) => {
    const { ids, names } = get();
    if (ids.includes(id)) set({ ids: ids.filter((x) => x !== id) });
    else if (ids.length < MAX_COMPARE) set({ ids: [...ids, id], names: { ...names, [id]: fullName } });
    else toast.info(`Compare supports up to ${MAX_COMPARE} repositories.`);
  },
  remove: (id) => set({ ids: get().ids.filter((x) => x !== id) }),
  clear: () => set({ ids: [] }),
}));

// ------------------------------------------------------------------ global UI

interface UiState {
  paletteOpen: boolean;
  shortcutsOpen: boolean;
  setPalette: (open: boolean) => void;
  setShortcuts: (open: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
  paletteOpen: false,
  shortcutsOpen: false,
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setShortcuts: (shortcutsOpen) => set({ shortcutsOpen }),
}));
