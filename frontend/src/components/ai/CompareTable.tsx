import { Fragment, useMemo } from "react";
import type { Comparison, CompareRow, EvidenceClass } from "@/api/types";
import { EvidenceClassLabel } from "@/components/evidence/EvidenceBits";
import { RepoTitle } from "@/components/repo/RepoBits";
import { useEvidenceViewer } from "@/state/stores";

const GROUPS: { key: CompareRow["group"]; label: string }[] = [
  { key: "identity", label: "Identity" },
  { key: "capabilities", label: "Capabilities" },
  { key: "architecture", label: "Architecture" },
  { key: "maturity", label: "Maturity" },
  { key: "dependencies", label: "Dependencies" },
  { key: "licensing", label: "Licensing" },
];

/** Deterministic side-by-side comparison. Works with no model configured. */
export function CompareTable({ comparison, onRemove }: { comparison: Comparison; onRemove?: (repoId: number) => void }) {
  const open = useEvidenceViewer((s) => s.open);
  const byRef = useMemo(() => new Map(comparison.evidence.map((e) => [e.ref, e])), [comparison.evidence]);
  const n = comparison.repos.length;
  return (
    <div className="overflow-x-auto" data-testid="compare-table">
      <table className="w-full table-fixed border-collapse text-sm" style={{ minWidth: 220 + n * 230 }}>
        <colgroup>
          <col style={{ width: 216 }} />
          {comparison.repos.map((repo) => (
            <col key={repo.id} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th scope="col" className="sticky left-0 z-[2] bg-bg" />
            {comparison.repos.map((repo) => (
              <th key={repo.id} scope="col" className="border-b border-line-strong px-4 pb-3 text-left align-bottom font-normal">
                <RepoTitle repo={repo} />
                <p className="mt-0.5 line-clamp-2 text-xs text-fg-3">{repo.description}</p>
                {onRemove && n > 2 && (
                  <button type="button" onClick={() => onRemove(repo.id)} className="mt-1 cursor-pointer text-2xs text-fg-4 hover:text-fg">
                    Remove
                  </button>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {GROUPS.map((group) => {
            const rows = comparison.rows.filter((r) => r.group === group.key);
            if (rows.length === 0) return null;
            return (
              <Fragment key={group.key}>
                <tr>
                  <th scope="colgroup" colSpan={n + 1} className="sticky left-0 bg-bg pb-1.5 pt-6 text-left">
                    <span className="text-md font-semibold text-fg">{group.label}</span>
                  </th>
                </tr>
                {rows.map((row) => (
                  <tr key={row.key} className="border-t border-line align-top">
                    <th scope="row" className="sticky left-0 z-[1] bg-bg py-2 pr-4 text-left font-normal">
                      <span className="block text-sm leading-snug text-fg-2">{row.label}</span>
                      <EvidenceClassLabel value={row.evidence_class as EvidenceClass} className="!text-[9.5px] !leading-[14px]" />
                    </th>
                    {row.values.map((value, i) => {
                      const refs = row.evidence_refs[i] ?? [];
                      const differs = new Set(row.values).size > 1;
                      return (
                        <td key={i} className="px-4 py-2">
                          <span className={value && value !== "—" ? (differs ? "text-fg" : "text-fg-2") : "text-fg-4"}>{value || "—"}</span>
                          {refs.length > 0 && (
                            <span className="ml-1.5 inline-flex flex-wrap gap-0.5 align-middle">
                              {refs.map((ref) => {
                                const ev = byRef.get(ref);
                                return (
                                  <button
                                    key={ref}
                                    type="button"
                                    disabled={!ev}
                                    onClick={() => ev && open({ type: "evidence", evidence: ev })}
                                    className="mono inline-flex h-[16px] cursor-pointer items-center rounded-[3px] border border-line-strong bg-raised px-1 text-[10px] leading-none text-accent hover:border-accent disabled:cursor-default disabled:text-fg-4"
                                  >
                                    {ref}
                                  </button>
                                );
                              })}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SharedLists({ comparison }: { comparison: Comparison }) {
  const name = (id: number) => comparison.repos.find((r) => r.id === id)?.name ?? `#${id}`;
  const Block = ({ title, items, empty }: { title: string; items: Comparison["shared_topics"]; empty: string }) => (
    <section>
      <h3 className="label">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-1.5 text-sm text-fg-4">{empty}</p>
      ) : (
        <ul className="mt-1.5 flex flex-col">
          {items.slice(0, 14).map((item) => (
            <li key={item.name} className="flex items-baseline justify-between gap-4 border-b border-line py-1 text-sm">
              <span className="mono text-xs text-fg">{item.name}</span>
              <span className="min-w-0 truncate text-xs text-fg-3">{item.repo_ids.length === comparison.repos.length ? "all" : item.repo_ids.map(name).join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
  return (
    <div className="grid gap-x-10 gap-y-6 md:grid-cols-2">
      <Block title="Shared topics" items={comparison.shared_topics} empty="No topics in common." />
      <Block title="Shared dependencies" items={comparison.shared_dependencies} empty="No dependencies in common among indexed manifests." />
    </div>
  );
}
