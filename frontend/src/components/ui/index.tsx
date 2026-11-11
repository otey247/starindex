/**
 * StarIndex UI primitives. Neutral surfaces, solid clearly-defined controls,
 * state through type and iconography — no tinted fills, no accent borders.
 */
import {
  forwardRef,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, ChevronDown, Loader2, X } from "lucide-react";
import { cn } from "@/lib/cn";

// ------------------------------------------------------------------------------ Button

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const BUTTON_BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap font-medium select-none " +
  "transition-colors duration-100 disabled:opacity-45 disabled:pointer-events-none cursor-pointer";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent-solid text-white hover:bg-accent-solid-hover active:bg-accent-solid",
  secondary: "bg-raised text-fg border border-line-strong hover:bg-overlay hover:border-fg-4",
  ghost: "text-fg-2 hover:text-fg hover:bg-hover",
  danger: "bg-danger-solid text-white hover:brightness-110",
};

const BUTTON_SIZE: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs rounded-sm",
  md: "h-8 px-3 text-sm rounded-sm",
  lg: "h-10 px-4 text-base rounded-md",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, icon, className, children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      className={cn(BUTTON_BASE, BUTTON_VARIANT[variant], BUTTON_SIZE[size], className)}
      {...rest}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  size?: "sm" | "md";
  active?: boolean;
  tooltipSide?: "top" | "bottom" | "left" | "right";
  noTooltip?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, size = "md", active, className, children, tooltipSide = "bottom", noTooltip, type, ...rest },
  ref,
) {
  const button = (
    <button
      ref={ref}
      type={type ?? "button"}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-sm transition-colors cursor-pointer",
        "disabled:opacity-40 disabled:pointer-events-none",
        size === "sm" ? "size-7" : "size-8",
        active ? "bg-overlay text-fg" : "text-fg-3 hover:bg-hover hover:text-fg",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
  if (noTooltip) return button;
  return (
    <Tooltip content={label} side={tooltipSide}>
      {button}
    </Tooltip>
  );
});

// ------------------------------------------------------------------------------ Fields

const FIELD_BASE =
  "w-full bg-bg border border-line-strong rounded-sm text-sm text-fg placeholder:text-fg-4 " +
  "transition-colors hover:border-fg-4 focus:border-accent focus:outline-none disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(FIELD_BASE, "h-8 px-2.5", className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(FIELD_BASE, "px-2.5 py-2 leading-relaxed resize-y", className)} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <span className={cn("relative inline-flex", className)}>
      <select
        ref={ref}
        className={cn(FIELD_BASE, "h-8 appearance-none pl-2.5 pr-7 cursor-pointer bg-raised")}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-fg-3" aria-hidden />
    </span>
  );
});

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {label}
      </label>
      {children}
      {error ? (
        <p className="flex items-start gap-1.5 text-xs text-danger" role="alert">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs leading-relaxed text-fg-3">{hint}</p>
      ) : null}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  className,
  disabled,
  indeterminate,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: ReactNode;
  className?: string;
  disabled?: boolean;
  indeterminate?: boolean;
}) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className={cn("group inline-flex min-w-0 cursor-pointer items-center gap-2 text-sm", disabled && "opacity-50", className)}
    >
      <input
        id={id}
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span
        aria-hidden
        className={cn(
          "flex size-[15px] shrink-0 items-center justify-center rounded-[3px] border transition-colors",
          "peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
          checked || indeterminate
            ? "border-accent-solid bg-accent-solid text-white"
            : "border-fg-4 bg-bg group-hover:border-fg-3",
        )}
      >
        {indeterminate ? <span className="h-0.5 w-2 bg-white" /> : checked ? <Check className="size-3" strokeWidth={3} /> : null}
      </span>
      {label !== undefined && <span className="min-w-0 truncate text-fg-2 group-hover:text-fg">{label}</span>}
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-[20px] w-[36px] shrink-0 cursor-pointer items-center rounded-full border transition-colors",
        "disabled:opacity-45 disabled:pointer-events-none",
        checked ? "border-accent-solid bg-accent-solid" : "border-line-strong bg-raised",
      )}
    >
      <span
        className={cn(
          "inline-block size-[14px] rounded-full transition-transform duration-150",
          checked ? "translate-x-[18px] bg-white" : "translate-x-[2px] bg-fg-3",
        )}
      />
    </button>
  );
}

export function Segmented<V extends string>({
  value,
  onChange,
  options,
  size = "md",
  label,
  className,
}: {
  value: V;
  onChange: (next: V) => void;
  options: { value: V; label: ReactNode; title?: string; icon?: ReactNode }[];
  size?: "sm" | "md";
  label: string;
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex rounded-sm border border-line-strong bg-bg p-0.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex cursor-pointer items-center gap-1.5 rounded-[3px] font-medium transition-colors",
              size === "sm" ? "h-6 px-2 text-xs" : "h-7 px-2.5 text-sm",
              active ? "bg-overlay text-fg" : "text-fg-3 hover:text-fg",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------------------ Small pieces

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[3px] border border-line-strong bg-raised px-1",
        "font-mono text-[10px] leading-none text-fg-3",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

/** Neutral token for topics, tags and operators. Removable variant shows an ×. */
export function Chip({
  children,
  onRemove,
  onClick,
  className,
  title,
  mono,
}: {
  children: ReactNode;
  onRemove?: () => void;
  onClick?: () => void;
  className?: string;
  title?: string;
  mono?: boolean;
}) {
  const body = (
    <span className={cn("truncate", mono && "mono")}>{children}</span>
  );
  const base = cn(
    "inline-flex h-[22px] max-w-full items-center gap-1 rounded-[4px] border border-line bg-raised px-1.5 text-xs text-fg-2",
    onClick && "cursor-pointer hover:border-fg-4 hover:text-fg",
    className,
  );
  return (
    <span className={base} title={title}>
      {onClick ? (
        <button type="button" onClick={onClick} className="min-w-0 cursor-pointer truncate">
          {body}
        </button>
      ) : (
        body
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${typeof children === "string" ? children : "item"}`}
          className="-mr-0.5 cursor-pointer rounded-[2px] text-fg-4 hover:text-fg"
        >
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-fg-3", className)} aria-label="Loading" />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-3", className)} aria-hidden />;
}

/** Determinate when `value` is a number (0–100); otherwise an indeterminate sweep. */
export function ProgressBar({
  value,
  className,
  label,
}: {
  value?: number | null;
  className?: string;
  label?: string;
}) {
  const determinate = typeof value === "number";
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={determinate ? Math.round(value) : undefined}
      className={cn("relative h-[3px] w-full overflow-hidden rounded-full bg-line", className)}
    >
      {determinate ? (
        <div className="h-full bg-accent transition-[width] duration-500 ease-out" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      ) : (
        <div className="absolute inset-y-0 w-2/5 bg-accent animate-indeterminate" />
      )}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-start gap-3 px-1 py-10 animate-rise", className)}>
      {icon && <div className="text-fg-4 [&_svg]:size-6">{icon}</div>}
      <div className="max-w-[54ch]">
        <h3 className="text-lg font-semibold text-fg">{title}</h3>
        {children && <div className="mt-1.5 text-base leading-relaxed text-fg-3">{children}</div>}
      </div>
      {action && <div className="mt-1 flex flex-wrap gap-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  className,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div role="alert" className={cn("flex items-start gap-3 rounded-md border border-line-strong bg-surface p-4", className)}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fg">{title}</p>
        <p className="mt-0.5 text-sm text-fg-3 break-words">{message}</p>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** Section heading with the mono small-caps label and an optional action area. */
export function SectionHeader({
  title,
  count,
  children,
  className,
}: {
  title: string;
  count?: number | string | null;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-7 items-center justify-between gap-3", className)}>
      <h2 className="label flex items-baseline gap-2">
        {title}
        {count !== undefined && count !== null && <span className="text-fg-4 tnum">{count}</span>}
      </h2>
      {children && <div className="flex items-center gap-1.5">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------------------ Tooltip

export function Tooltip({
  content,
  children,
  side = "bottom",
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const el = ref.current?.firstElementChild ?? ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const gap = 7;
      const p =
        side === "top"
          ? { x: r.left + r.width / 2, y: r.top - gap }
          : side === "left"
            ? { x: r.left - gap, y: r.top + r.height / 2 }
            : side === "right"
              ? { x: r.right + gap, y: r.top + r.height / 2 }
              : { x: r.left + r.width / 2, y: r.bottom + gap };
      setPos(p);
      setOpen(true);
    }, 350);
  };
  const hide = () => {
    if (timer.current) clearTimeout(timer.current);
    setOpen(false);
  };
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const transform =
    side === "top"
      ? "translate(-50%, -100%)"
      : side === "left"
        ? "translate(-100%, -50%)"
        : side === "right"
          ? "translate(0, -50%)"
          : "translate(-50%, 0)";

  return (
    <span ref={ref} className="inline-flex" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      {children}
      {open &&
        pos &&
        createPortal(
          <span
            role="tooltip"
            style={{ left: pos.x, top: pos.y, transform }}
            className="pointer-events-none fixed z-[90] max-w-[260px] rounded-[4px] border border-line-strong bg-overlay px-2 py-1 text-xs text-fg shadow-pop animate-fade"
          >
            {content}
          </span>,
          document.body,
        )}
    </span>
  );
}

// ------------------------------------------------------------------------------ Dialog & Drawer

function useEscape(onClose: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, active]);
}

function useFocusTrap(ref: React.RefObject<HTMLElement | null>, active: boolean) {
  useLayoutEffect(() => {
    if (!active) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    if (node) {
      const target =
        node.querySelector<HTMLElement>("[data-autofocus]") ??
        node.querySelector<HTMLElement>("input, textarea, select, button:not([data-dialog-close])") ??
        node;
      target.focus({ preventScroll: true });
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || !node) return;
      const focusable = Array.from(
        node.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), textarea, select, [tabindex]:not([tabindex="-1"])'),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.({ preventScroll: true });
    };
  }, [ref, active]);
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEscape(onClose, open);
  useFocusTrap(ref, open);
  if (!open) return null;
  const width = size === "sm" ? "max-w-[420px]" : size === "lg" ? "max-w-[760px]" : "max-w-[540px]";
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto p-4 pt-[12vh] max-sm:items-end max-sm:p-0 max-sm:pt-10">
      <div className="fixed inset-0 bg-black/65 animate-fade" onClick={onClose} aria-hidden />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "relative w-full rounded-lg border border-line-strong bg-surface shadow-pop animate-pop outline-none",
          "max-sm:rounded-b-none max-sm:max-w-none",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold text-fg">
              {title}
            </h2>
            {description && <div className="mt-1 text-sm leading-relaxed text-fg-3">{description}</div>}
          </div>
          <IconButton label="Close" size="sm" onClick={onClose} data-dialog-close noTooltip>
            <X className="size-4" />
          </IconButton>
        </div>
        {children && <div className="px-5 py-4">{children}</div>}
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 560,
  header,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: number;
  header?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEscape(onClose, open);
  useFocusTrap(ref, open);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60]">
      <div className="absolute inset-0 bg-black/50 animate-fade" onClick={onClose} aria-hidden />
      <aside
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{ width: `min(100vw, ${width}px)` }}
        className="absolute inset-y-0 right-0 flex flex-col border-l border-line-strong bg-surface shadow-pop animate-drawer outline-none"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="label">
              {title}
            </h2>
            {header}
          </div>
          <IconButton label="Close panel" size="sm" onClick={onClose} data-dialog-close noTooltip>
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

// ------------------------------------------------------------------------------ Menu (popover)

export interface MenuItem {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
  checked?: boolean;
  hint?: ReactNode;
}

export function Menu({
  trigger,
  items,
  align = "start",
  header,
  width = 220,
  footer,
}: {
  trigger: (props: { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu" }) => ReactNode;
  items: (MenuItem | "separator")[];
  align?: "start" | "end";
  header?: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; maxHeight: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !wrap.current) return;
    const r = wrap.current.getBoundingClientRect();
    const vw = window.innerWidth;
    let left = align === "end" ? r.right - width : r.left;
    left = Math.max(8, Math.min(left, vw - width - 8));
    const spaceBelow = window.innerHeight - r.bottom - 12;
    const spaceAbove = r.top - 12;
    if (spaceBelow < 180 && spaceAbove > spaceBelow) {
      setPos({ left, top: Math.max(8, r.top - Math.min(spaceAbove, 420) - 4), maxHeight: Math.min(spaceAbove, 420) });
    } else {
      setPos({ left, top: r.bottom + 4, maxHeight: Math.min(spaceBelow, 420) });
    }
  }, [open, align, width]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (wrap.current?.contains(target)) return;
      if ((target as HTMLElement).closest?.("[data-menu-panel]")) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <span ref={wrap} className="inline-flex">
      {trigger({ onClick: () => setOpen((o) => !o), "aria-expanded": open, "aria-haspopup": "menu" })}
      {open &&
        pos &&
        createPortal(
          <div
            data-menu-panel
            role="menu"
            style={{ left: pos.left, top: pos.top, width, maxHeight: pos.maxHeight }}
            className="fixed z-[80] flex flex-col overflow-hidden rounded-md border border-line-strong bg-overlay py-1 shadow-pop animate-pop"
          >
            {header && <div className="border-b border-line px-2.5 pb-1.5 pt-1">{header}</div>}
            <div className="min-h-0 overflow-y-auto">
              {items.map((item, i) =>
                item === "separator" ? (
                  <div key={`sep-${i}`} className="my-1 border-t border-line" />
                ) : (
                  <button
                    key={item.key}
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={() => {
                      item.onSelect();
                      setOpen(false);
                    }}
                    className={cn(
                      "flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-left text-sm transition-colors",
                      "disabled:opacity-40 disabled:pointer-events-none hover:bg-line focus-visible:bg-line",
                      item.danger ? "text-danger" : "text-fg-2 hover:text-fg",
                    )}
                  >
                    {item.icon && <span className="shrink-0 text-fg-3 [&_svg]:size-3.5">{item.icon}</span>}
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.hint && <span className="shrink-0 text-xs text-fg-4">{item.hint}</span>}
                    {item.checked && <Check className="size-3.5 shrink-0 text-accent" />}
                  </button>
                ),
              )}
            </div>
            {footer && <div className="border-t border-line px-2.5 pt-1.5 pb-1">{footer}</div>}
          </div>,
          document.body,
        )}
    </span>
  );
}

// ------------------------------------------------------------------------------ Tabs

export function Tabs<V extends string>({
  value,
  onChange,
  tabs,
  className,
  label,
}: {
  value: V;
  onChange: (next: V) => void;
  tabs: { value: V; label: ReactNode; count?: number | null }[];
  className?: string;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className={cn("flex gap-5 overflow-x-auto border-b border-line", className)}>
      {tabs.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.value)}
            className={cn(
              "relative -mb-px inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 border-b-2 text-sm font-medium transition-colors",
              active ? "border-fg text-fg" : "border-transparent text-fg-3 hover:text-fg",
            )}
          >
            {t.label}
            {t.count !== undefined && t.count !== null && <span className="text-xs text-fg-4 tnum">{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Label/value row used in metadata panels. */
export function MetaRow({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1 text-sm">
      <dt className="shrink-0 text-fg-3">{label}</dt>
      <dd className={cn("min-w-0 truncate text-right text-fg-2", mono && "mono text-xs")}>{children}</dd>
    </div>
  );
}

/** A settings row: explanation on the left, control on the right. */
export function SettingRow({
  title,
  description,
  children,
  stacked,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  stacked?: boolean;
}) {
  return (
    <div className={cn("flex gap-x-8 gap-y-2.5 border-b border-line py-4 last:border-b-0", stacked ? "flex-col" : "flex-col sm:flex-row sm:items-start sm:justify-between")}>
      <div className="min-w-0 max-w-[62ch]">
        <p className="text-sm font-medium text-fg">{title}</p>
        {description && <p className="mt-0.5 text-sm leading-relaxed text-fg-3">{description}</p>}
      </div>
      <div className={cn("shrink-0", stacked && "w-full")}>{children}</div>
    </div>
  );
}
