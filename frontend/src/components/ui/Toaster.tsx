import { AlertTriangle, Check, Info, X } from "lucide-react";
import { useToasts } from "@/state/stores";

export function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[95] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2 max-md:bottom-[72px]"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className="pointer-events-auto flex items-start gap-2.5 rounded-md border border-line-strong bg-overlay px-3 py-2.5 text-sm text-fg shadow-pop animate-rise"
        >
          <span className="mt-0.5 shrink-0">
            {t.kind === "error" ? (
              <AlertTriangle className="size-4 text-danger" />
            ) : t.kind === "success" ? (
              <Check className="size-4 text-ok" />
            ) : (
              <Info className="size-4 text-fg-3" />
            )}
          </span>
          <span className="min-w-0 flex-1 break-words leading-snug">{t.message}</span>
          {t.action && (
            <button
              type="button"
              className="shrink-0 cursor-pointer font-medium text-accent hover:underline"
              onClick={() => {
                t.action?.onClick();
                dismiss(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" aria-label="Dismiss" className="shrink-0 cursor-pointer text-fg-4 hover:text-fg" onClick={() => dismiss(t.id)}>
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
