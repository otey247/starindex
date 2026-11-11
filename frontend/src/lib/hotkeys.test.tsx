import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { isEditable, isInteractive, useHotkeys, type Hotkey } from "./hotkeys";

function Harness({ hotkeys }: { hotkeys: Hotkey[] }) {
  useHotkeys(hotkeys);
  return (
    <div>
      <input aria-label="field" />
      <button onClick={() => undefined}>action</button>
    </div>
  );
}

describe("useHotkeys", () => {
  it("fires single keys, but not while typing in a field", () => {
    const j = vi.fn();
    render(<Harness hotkeys={[{ keys: "j", handler: j }]} />);
    fireEvent.keyDown(document.body, { key: "j" });
    expect(j).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByLabelText("field"), { key: "j" });
    expect(j).toHaveBeenCalledTimes(1);
  });

  it("supports mod combos inside inputs when allowed", () => {
    const palette = vi.fn();
    render(<Harness hotkeys={[{ keys: "mod+k", handler: palette, allowInInput: true }]} />);
    fireEvent.keyDown(screen.getByLabelText("field"), { key: "k", ctrlKey: true });
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    fireEvent.keyDown(document.body, { key: "k" });
    expect(palette).toHaveBeenCalledTimes(2);
  });

  it("recognises two-key sequences such as g then l", () => {
    const go = vi.fn();
    const l = vi.fn();
    render(<Harness hotkeys={[{ keys: "g l", handler: go }]} />);
    fireEvent.keyDown(document.body, { key: "l" });
    expect(go).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "l" });
    expect(go).toHaveBeenCalledTimes(1);
    expect(l).not.toHaveBeenCalled();
  });

  it("never steals Enter from a focused button", () => {
    const enter = vi.fn();
    render(<Harness hotkeys={[{ keys: "enter", handler: enter }]} />);
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    screen.getByRole("button", { name: "action" }).dispatchEvent(event);
    expect(enter).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("leaves the default action alone when a handler declines", () => {
    render(<Harness hotkeys={[{ keys: "enter", handler: () => false }]} />);
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    document.body.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it("classifies targets", () => {
    render(<Harness hotkeys={[]} />);
    expect(isEditable(screen.getByLabelText("field"))).toBe(true);
    expect(isEditable(screen.getByRole("button"))).toBe(false);
    expect(isInteractive(screen.getByRole("button"))).toBe(true);
    expect(isInteractive(document.body)).toBe(false);
  });
});
