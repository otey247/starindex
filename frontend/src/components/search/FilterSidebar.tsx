import { ChevronRight, Search as SearchIcon } from "lucide-react";
import { useMemo, useState, type Dispatch, type ReactNode } from "react";
import type { FacetValue, Facets, RepoFilters } from "@/api/types";
import { Button, Checkbox, Input, Segmented } from "@/components/ui";
import { cn } from "@/lib/cn";
import { countActiveFilters, type FilterAction, type ListKey, type TriKey } from "@/lib/filters";
import { formatCount, INDEX_LEVEL_LABEL, MAINTENANCE_LABEL } from "@/lib/format";

function Section({
  title,
  activeCount = 0,
  defaultOpen = true,
  children,
}: {
  title: string;
  activeCount?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen || activeCount > 0);
  return (
    <section className="border-b border-line py-2.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="group flex w-full cursor-pointer items-center gap-1.5 text-left"
      >
        <ChevronRight className={cn("size-3 text-fg-4 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="label group-hover:!text-fg">{title}</span>
        {activeCount > 0 && <span className="mono ml-auto text-2xs text-accent">{activeCount}</span>}
      </button>
      {open && <div className="mt-2 pl-[18px]">{children}</div>}
    </section>
  );
}

function FacetList({
  values,
  selected,
  onToggle,
  searchable,
  limit = 7,
  labels,
  emptyText = "No values in this result set.",
}: {
  values: FacetValue[];
  selected: string[];
  onToggle: (value: string) => void;
  searchable?: boolean;
  limit?: number;
  labels?: Record<string, string>;
  emptyText?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [q, setQ] = useState("");
  const merged = useMemo(() => {
    // Keep selected values visible even when the current result set no longer contains them.
    const known = new Set(values.map((v) => v.value));
    const extra = selected.filter((s) => !known.has(s)).map((s) => ({ value: s, label: null, count: 0 }));
    return [...values, ...extra];
  }, [values, selected]);
  const filtered = q ? merged.filter((v) => (v.label ?? v.value).toLowerCase().includes(q.toLowerCase())) : merged;
  const ordered = [...filtered.filter((v) => selected.includes(v.value)), ...filtered.filter((v) => !selected.includes(v.value))];
  const shown = expanded || q ? ordered.slice(0, 60) : ordered.slice(0, Math.max(limit, selected.length));
  if (merged.length === 0) return <p className="text-xs text-fg-4">{emptyText}</p>;
  return (
    <div className="flex flex-col gap-1">
      {searchable && merged.length > limit && (
        <div className="relative mb-1">
          <SearchIcon className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2 text-fg-4" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" className="!h-7 pl-6 text-xs" aria-label="Filter values" />
        </div>
      )}
      {shown.map((v) => (
        <div key={v.value} className="flex items-center justify-between gap-2">
          <Checkbox
            checked={selected.includes(v.value)}
            onChange={() => onToggle(v.value)}
            label={v.label ?? labels?.[v.value] ?? v.value}
            className="min-w-0 flex-1 !text-[13px]"
          />
          <span className="shrink-0 text-2xs text-fg-4 tnum">{formatCount(v.count)}</span>
        </div>
      ))}
      {!q && ordered.length > shown.length && (
        <button type="button" onClick={() => setExpanded(true)} className="mt-0.5 cursor-pointer self-start text-xs text-fg-3 hover:text-fg">
          Show {ordered.length - shown.length} more
        </button>
      )}
      {!q && expanded && ordered.length > limit && (
        <button type="button" onClick={() => setExpanded(false)} className="mt-0.5 cursor-pointer self-start text-xs text-fg-3 hover:text-fg">
          Show fewer
        </button>
      )}
      {q && shown.length === 0 && <p className="text-xs text-fg-4">No match.</p>}
    </div>
  );
}

function Tri({ label, value, onChange }: { label: string; value: boolean | null; onChange: (v: boolean | null) => void }) {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="text-[13px] text-fg-2">{label}</span>
      <Segmented
        size="sm"
        label={label}
        value={value === null ? "any" : value ? "yes" : "no"}
        onChange={(v) => onChange(v === "any" ? null : v === "yes")}
        options={[
          { value: "any", label: "Any" },
          { value: "yes", label: "Yes" },
          { value: "no", label: "No" },
        ]}
      />
    </div>
  );
}

function NumberRange({
  min,
  max,
  onMin,
  onMax,
  presets,
  unit,
}: {
  min: number | null;
  max: number | null;
  onMin: (v: number | null) => void;
  onMax: (v: number | null) => void;
  presets?: { label: string; min: number | null; max: number | null }[];
  unit?: string;
}) {
  const parse = (s: string) => (s.trim() === "" ? null : Number(s));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <Input type="number" min={0} inputMode="numeric" value={min ?? ""} onChange={(e) => onMin(parse(e.target.value))} placeholder="min" aria-label={`Minimum ${unit ?? ""}`} className="!h-7 text-xs" />
        <span className="text-fg-4">–</span>
        <Input type="number" min={0} inputMode="numeric" value={max ?? ""} onChange={(e) => onMax(parse(e.target.value))} placeholder="max" aria-label={`Maximum ${unit ?? ""}`} className="!h-7 text-xs" />
      </div>
      {presets && (
        <div className="flex flex-wrap gap-1">
          {presets.map((p) => {
            const active = p.min === min && p.max === max;
            return (
              <button
                key={p.label}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  onMin(active ? null : p.min);
                  onMax(active ? null : p.max);
                }}
                className={cn(
                  "h-6 cursor-pointer rounded-[4px] border px-1.5 text-xs transition-colors",
                  active ? "border-fg-3 bg-overlay text-fg" : "border-line text-fg-3 hover:border-fg-4 hover:text-fg",
                )}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

function DateRange({
  after,
  before,
  onAfter,
  onBefore,
  label,
}: {
  after: string | null;
  before: string | null;
  onAfter: (v: string | null) => void;
  onBefore: (v: string | null) => void;
  label: string;
}) {
  const presets = [
    { label: "30d", days: 30 },
    { label: "6mo", days: 183 },
    { label: "1y", days: 365 },
    { label: "2y", days: 730 },
  ];
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5">
        <span className="text-xs text-fg-3">After</span>
        <Input type="date" value={after ?? ""} onChange={(e) => onAfter(e.target.value || null)} aria-label={`${label} after`} className="!h-7 text-xs" />
        <span className="text-xs text-fg-3">Before</span>
        <Input type="date" value={before ?? ""} onChange={(e) => onBefore(e.target.value || null)} aria-label={`${label} before`} className="!h-7 text-xs" />
      </div>
      <div className="flex flex-wrap gap-1">
        {presets.map((p) => {
          const v = isoDaysAgo(p.days);
          const active = after === v && !before;
          return (
            <button
              key={p.label}
              type="button"
              aria-pressed={active}
              onClick={() => {
                onAfter(active ? null : v);
                onBefore(null);
              }}
              className={cn(
                "h-6 cursor-pointer rounded-[4px] border px-1.5 text-xs transition-colors",
                active ? "border-fg-3 bg-overlay text-fg" : "border-line text-fg-3 hover:border-fg-4 hover:text-fg",
              )}
            >
              last {p.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export interface FilterSidebarProps {
  filters: RepoFilters;
  dispatch: Dispatch<FilterAction>;
  facets: Facets | null | undefined;
  loading?: boolean;
  className?: string;
}

/** Filter panel bound to RepoFilters; used by Search and Library. */
export function FilterSidebar({ filters, dispatch, facets, className }: FilterSidebarProps) {
  const active = countActiveFilters(filters);
  const toggle = (key: ListKey) => (value: string) => dispatch({ type: "toggle", key, value });
  const tri = (key: TriKey) => (value: boolean | null) => dispatch({ type: "setTri", key, value });
  const f = facets;
  const mb = (kb: number | null) => (kb === null ? null : Math.round(kb / 1024));
  const kb = (m: number | null) => (m === null || Number.isNaN(m) ? null : m * 1024);

  return (
    <aside className={cn("text-sm", className)} aria-label="Filters">
      <div className="flex h-8 items-center justify-between border-b border-line pb-1">
        <span className="label">
          Filters{active > 0 && <span className="ml-1.5 text-accent">{active}</span>}
        </span>
        {active > 0 && (
          <Button size="sm" variant="ghost" onClick={() => dispatch({ type: "clear" })} className="!h-6 !px-1.5">
            Clear all
          </Button>
        )}
      </div>

      <Section title="Language" activeCount={filters.languages.length}>
        <FacetList values={f?.languages ?? []} selected={filters.languages} onToggle={toggle("languages")} searchable />
      </Section>

      <Section title="Category" activeCount={filters.categories.length}>
        <FacetList values={f?.categories ?? []} selected={filters.categories} onToggle={toggle("categories")} limit={6} />
      </Section>

      <Section title="Topics" activeCount={filters.topics.length} defaultOpen={false}>
        <FacetList values={f?.topics ?? []} selected={filters.topics} onToggle={toggle("topics")} searchable limit={8} />
      </Section>

      <Section title="Stars" activeCount={filters.min_stars !== null || filters.max_stars !== null ? 1 : 0}>
        <NumberRange
          unit="stars"
          min={filters.min_stars}
          max={filters.max_stars}
          onMin={(v) => dispatch({ type: "setNum", key: "min_stars", value: v })}
          onMax={(v) => dispatch({ type: "setNum", key: "max_stars", value: v })}
          presets={[
            { label: "100+", min: 100, max: null },
            { label: "1k+", min: 1000, max: null },
            { label: "10k+", min: 10_000, max: null },
            { label: "< 1k", min: null, max: 999 },
          ]}
        />
      </Section>

      <Section title="License" activeCount={filters.licenses.length} defaultOpen={false}>
        <FacetList values={f?.licenses ?? []} selected={filters.licenses} onToggle={toggle("licenses")} labels={{ none: "No license" }} />
      </Section>

      <Section title="Maintenance" activeCount={filters.maintenance.length + (filters.archived !== null ? 1 : 0)}>
        <FacetList values={f?.maintenance ?? []} selected={filters.maintenance} onToggle={toggle("maintenance")} labels={MAINTENANCE_LABEL} />
        <div className="mt-2">
          <Tri label="Archived" value={filters.archived} onChange={tri("archived")} />
        </div>
      </Section>

      <Section title="Last commit" activeCount={filters.pushed_after || filters.pushed_before ? 1 : 0} defaultOpen={false}>
        <DateRange
          label="Pushed"
          after={filters.pushed_after}
          before={filters.pushed_before}
          onAfter={(v) => dispatch({ type: "setDate", key: "pushed_after", value: v })}
          onBefore={(v) => dispatch({ type: "setDate", key: "pushed_before", value: v })}
        />
      </Section>

      <Section title="Date starred" activeCount={filters.starred_after || filters.starred_before ? 1 : 0} defaultOpen={false}>
        <DateRange
          label="Starred"
          after={filters.starred_after}
          before={filters.starred_before}
          onAfter={(v) => dispatch({ type: "setDate", key: "starred_after", value: v })}
          onBefore={(v) => dispatch({ type: "setDate", key: "starred_before", value: v })}
        />
      </Section>

      <Section title="Repository size" activeCount={filters.min_size_kb !== null || filters.max_size_kb !== null ? 1 : 0} defaultOpen={false}>
        <NumberRange
          unit="size in MB"
          min={mb(filters.min_size_kb)}
          max={mb(filters.max_size_kb)}
          onMin={(v) => dispatch({ type: "setNum", key: "min_size_kb", value: kb(v) })}
          onMax={(v) => dispatch({ type: "setNum", key: "max_size_kb", value: kb(v) })}
          presets={[
            { label: "< 10 MB", min: null, max: 10 },
            { label: "< 100 MB", min: null, max: 100 },
            { label: "100 MB+", min: 100, max: null },
          ]}
        />
        <p className="mt-1 text-2xs text-fg-4">Megabytes</p>
      </Section>

      <Section title="Tags" activeCount={filters.tags.length} defaultOpen={false}>
        <FacetList values={f?.tags ?? []} selected={filters.tags} onToggle={toggle("tags")} searchable emptyText="You have not tagged anything yet." />
      </Section>

      <Section title="Collections" activeCount={filters.collections.length} defaultOpen={false}>
        <FacetList
          values={f?.collections ?? []}
          selected={filters.collections.map(String)}
          onToggle={(v) => dispatch({ type: "toggleCollection", id: Number(v) })}
          emptyText="No collections yet."
        />
      </Section>

      <Section
        title="Indexing"
        activeCount={filters.index_status.length + filters.indexed_level.length}
        defaultOpen={false}
      >
        <p className="mb-1 text-2xs text-fg-4">Status</p>
        <FacetList values={f?.index_status ?? []} selected={filters.index_status} onToggle={toggle("index_status")} />
        <p className="mb-1 mt-2.5 text-2xs text-fg-4">Indexed level</p>
        <FacetList values={f?.indexed_level ?? []} selected={filters.indexed_level} onToggle={toggle("indexed_level")} labels={INDEX_LEVEL_LABEL} />
      </Section>

      <Section
        title="More"
        activeCount={[filters.fork, filters.favorite, filters.has_note].filter((v) => v !== null).length + (filters.star_state !== "starred" ? 1 : 0)}
        defaultOpen={false}
      >
        <Tri label="Forks" value={filters.fork} onChange={tri("fork")} />
        <Tri label="Favorites" value={filters.favorite} onChange={tri("favorite")} />
        <Tri label="Has note" value={filters.has_note} onChange={tri("has_note")} />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[13px] text-fg-2">Stars</span>
          <Segmented
            size="sm"
            label="Star state"
            value={filters.star_state}
            onChange={(v) => dispatch({ type: "setStarState", value: v })}
            options={[
              { value: "starred", label: "Starred" },
              { value: "unstarred", label: "Unstarred" },
              { value: "all", label: "All" },
            ]}
          />
        </div>
      </Section>
    </aside>
  );
}
