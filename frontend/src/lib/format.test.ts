import { describe, expect, it } from "vitest";
import { formatBytes, formatCost, formatCount, formatDuration, lineRange, pct, plural, shortSha, timeAgo } from "./format";

describe("format helpers", () => {
  it("abbreviates counts", () => {
    expect(formatCount(999)).toBe("999");
    expect(formatCount(1500)).toBe("1.5k");
    expect(formatCount(26800)).toBe("27k");
    expect(formatCount(1_250_000)).toBe("1.3M");
    expect(formatCount(null)).toBe("—");
  });

  it("formats relative time in both directions", () => {
    const now = Date.parse("2026-11-11T12:00:00Z");
    expect(timeAgo("2026-11-11T11:59:50Z", now)).toBe("just now");
    expect(timeAgo("2026-11-11T09:00:00Z", now)).toBe("3h ago");
    expect(timeAgo("2026-11-08T12:00:00Z", now)).toBe("3d ago");
    expect(timeAgo("2026-05-11T12:00:00Z", now)).toBe("6mo ago");
    expect(timeAgo("2024-11-11T12:00:00Z", now)).toBe("2y ago");
    expect(timeAgo("2026-11-11T15:00:00Z", now)).toBe("in 3h");
    expect(timeAgo(null, now)).toBe("—");
    expect(timeAgo("not a date", now)).toBe("—");
  });

  it("formats bytes, durations, cost and misc", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(412_876_800)).toBe("394 MB");
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatCost(null)).toBeNull();
    expect(formatCost(0.0042)).toBe("$0.0042");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(shortSha("abcdef1234")).toBe("abcdef1");
    expect(lineRange(10, 24)).toBe("L10–24");
    expect(lineRange(10, 10)).toBe("L10");
    expect(pct(1, 4)).toBe(25);
    expect(pct(1, 0)).toBe(0);
    expect(plural(1, "repository", "repositories")).toBe("1 repository");
    expect(plural(2, "repository", "repositories")).toBe("2 repositories");
  });
});
