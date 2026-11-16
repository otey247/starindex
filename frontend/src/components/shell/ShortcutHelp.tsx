import { Dialog, Kbd } from "@/components/ui";
import { useUi } from "@/state/stores";
import { NAV } from "./nav";

const GROUPS: { title: string; rows: [string[], string][] }[] = [
  {
    title: "Anywhere",
    rows: [
      [["Ctrl", "K"], "Command palette: jump to a repository or action"],
      [["/"], "Focus the search bar"],
      [["?"], "Show this help"],
      [["Esc"], "Close a panel, dialog or menu"],
    ],
  },
  {
    title: "Results",
    rows: [
      [["j"], "Next result"],
      [["k"], "Previous result"],
      [["Enter"], "Open the focused repository"],
      [["o"], "Open the focused repository on GitHub"],
      [["x"], "Add the focused repository to the comparison"],
      [["+"], "Mark the focused result relevant to this query"],
      [["-"], "Mark the focused result not relevant"],
    ],
  },
  {
    title: "Search bar",
    rows: [
      [["Enter"], "Run the search"],
      [["↑", "↓"], "Move through suggestions"],
      [["Tab"], "Accept the highlighted suggestion"],
      [["Alt", "1…5"], "Switch mode: Keyword, Semantic, Hybrid, Ask, Research"],
    ],
  },
];

export function ShortcutHelp() {
  const open = useUi((s) => s.shortcutsOpen);
  const setOpen = useUi((s) => s.setShortcuts);
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="Keyboard shortcuts" size="lg">
      <div className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
        {GROUPS.map((group) => (
          <section key={group.title}>
            <h3 className="label mb-2">{group.title}</h3>
            <dl className="flex flex-col gap-1.5">
              {group.rows.map(([keys, text]) => (
                <div key={text} className="flex items-center justify-between gap-4 text-sm">
                  <dd className="text-fg-2">{text}</dd>
                  <dt className="flex shrink-0 items-center gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </dt>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <section>
          <h3 className="label mb-2">Go to</h3>
          <dl className="flex flex-col gap-1.5">
            {NAV.map((item) => (
              <div key={item.to} className="flex items-center justify-between gap-4 text-sm">
                <dd className="text-fg-2">{item.label}</dd>
                <dt className="flex shrink-0 items-center gap-1">
                  <Kbd>g</Kbd>
                  <Kbd>{item.shortcut}</Kbd>
                </dt>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </Dialog>
  );
}
