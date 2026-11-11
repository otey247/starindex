import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Plus } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { invalidateRepoData, useCollections } from "@/api/queries";
import { Button, Dialog, Field, Input, Menu, Textarea, type MenuItem } from "@/components/ui";
import { toast } from "@/state/stores";

export function NewCollectionDialog({
  open,
  onClose,
  repoIds = [],
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  repoIds?: number[];
  onCreated?: (id: number) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => api.createCollection({ name: name.trim(), description: description.trim(), repo_ids: repoIds }),
    onSuccess: (c) => {
      invalidateRepoData(qc);
      toast.success(`Created “${c.name}”${repoIds.length ? ` with ${repoIds.length} repositor${repoIds.length === 1 ? "y" : "ies"}` : ""}.`);
      setName("");
      setDescription("");
      onCreated?.(c.id);
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New collection"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim()} loading={create.isPending} onClick={() => create.mutate()}>
            Create collection
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <Field label="Name" error={create.isError ? errorMessage(create.error) : undefined} htmlFor="new-collection-name">
          <Input id="new-collection-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Local RAG stack" maxLength={120} />
        </Field>
        <Field label="Description" htmlFor="new-collection-desc">
          <Textarea id="new-collection-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" />
        </Field>
      </form>
    </Dialog>
  );
}

/** "Add to collection" menu for one or many repositories. */
export function CollectionMenu({
  repoIds,
  trigger,
  memberOf = [],
}: {
  repoIds: number[];
  trigger?: (props: { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu" }) => ReactNode;
  memberOf?: number[];
}) {
  const qc = useQueryClient();
  const collections = useCollections();
  const [creating, setCreating] = useState(false);
  const add = useMutation({
    mutationFn: ({ id, remove }: { id: number; remove: boolean }) =>
      remove ? api.removeFromCollection(id, repoIds) : api.addToCollection(id, repoIds),
    onSuccess: (c, vars) => {
      invalidateRepoData(qc, repoIds.length === 1 ? repoIds[0] : undefined);
      toast.success(vars.remove ? `Removed from “${c.name}”.` : `Added to “${c.name}”.`);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const manual = (collections.data ?? []).filter((c) => c.kind === "manual");
  const items: (MenuItem | "separator")[] = [
    ...manual.map<MenuItem>((c) => {
      const isMember = repoIds.length === 1 && memberOf.includes(c.id);
      return {
        key: String(c.id),
        label: c.name,
        hint: String(c.repo_count),
        checked: isMember,
        onSelect: () => add.mutate({ id: c.id, remove: isMember }),
      };
    }),
    ...(manual.length ? (["separator"] as const) : []),
    { key: "new", label: "New collection…", icon: <Plus />, onSelect: () => setCreating(true) },
  ];
  return (
    <>
      <Menu
        width={250}
        header={<span className="label">Add to collection</span>}
        items={items}
        trigger={
          trigger ??
          ((props) => (
            <Button size="sm" icon={<FolderPlus className="size-3.5" />} {...props}>
              Collection
            </Button>
          ))
        }
      />
      <NewCollectionDialog open={creating} onClose={() => setCreating(false)} repoIds={repoIds} />
    </>
  );
}
