import { useEffect, useRef } from "react";

export function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable === true;
}

export interface Hotkey {
  /** e.g. "/", "?", "j", "mod+k", "escape", "g s" (two-key sequence) */
  keys: string;
  /** Return false to signal "not handled" so the browser's default action still runs. */
  handler: (event: KeyboardEvent) => void | boolean;
  /** Fire even while typing in an input (default false). */
  allowInInput?: boolean;
}

/** Focus is on something that already reacts to Enter/Space (button, link, tab…). */
export function isInteractive(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (["BUTTON", "A", "SUMMARY"].includes(target.tagName)) return true;
  const role = target.getAttribute("role");
  return role !== null && ["button", "link", "tab", "radio", "checkbox", "switch", "menuitem", "option"].includes(role);
}

function matches(event: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split("+");
  const key = parts[parts.length - 1]!;
  const wantMod = parts.includes("mod");
  const wantShift = parts.includes("shift");
  const mod = event.metaKey || event.ctrlKey;
  if (wantMod !== mod) return false;
  if (event.altKey) return false;
  // "?" needs shift on most layouts; only enforce shift when asked explicitly.
  if (wantShift && !event.shiftKey) return false;
  // "+" is the combo separator, so the plus and minus keys are spelled out.
  if (key === "plus") return event.key === "+";
  if (key === "minus") return event.key === "-" || event.key === "_";
  return event.key.toLowerCase() === key;
}

/** Global keyboard shortcuts. Sequences ("g s") expire after 900 ms. */
export function useHotkeys(hotkeys: Hotkey[], enabled = true): void {
  const ref = useRef(hotkeys);
  ref.current = hotkeys;
  useEffect(() => {
    if (!enabled) return;
    let pending: { key: string; at: number } | null = null;
    const onKey = (event: KeyboardEvent) => {
      const typing = isEditable(event.target);
      const now = Date.now();
      for (const hk of ref.current) {
        if (typing && !hk.allowInInput) continue;
        const seq = hk.keys.split(" ");
        if (seq.length === 2) {
          if (pending && now - pending.at < 900 && pending.key === seq[0] && matches(event, seq[1]!)) {
            pending = null;
            if (hk.handler(event) === false) continue;
            event.preventDefault();
            return;
          }
          continue;
        }
        if (matches(event, hk.keys)) {
          // Never steal Enter from a focused button or link.
          if (event.key === "Enter" && isInteractive(event.target)) continue;
          if (hk.handler(event) === false) continue;
          event.preventDefault();
          pending = null;
          return;
        }
      }
      if (!typing && !event.metaKey && !event.ctrlKey && event.key.length === 1) {
        pending = { key: event.key.toLowerCase(), at: now };
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
