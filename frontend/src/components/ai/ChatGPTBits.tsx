import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { keys, useChatGPTStatus } from "@/api/queries";
import { CHATGPT_USAGE_URL } from "@/components/ai/ModelControls";
import { Button, Dialog } from "@/components/ui";

/**
 * "Continue with ChatGPT" — the label OpenAI's guidelines require for the action that
 * starts sign-in. Solid, high-contrast, neutral. (Drop the approved ChatGPT logo asset
 * into this button when distributing under OpenAI's brand guidelines.)
 */
export function ContinueWithChatGPT({ onClick, loading, disabled, label = "Continue with ChatGPT" }: { onClick: () => void; loading?: boolean; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      className="inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-md bg-white px-5 text-base font-semibold text-black transition-colors hover:bg-[#e6e6e6] disabled:pointer-events-none disabled:opacity-50"
    >
      {loading && <span className="size-3.5 animate-spin rounded-full border-2 border-black/25 border-t-black" aria-hidden />}
      {label}
    </button>
  );
}

/** First-sign-in confirmation, shown once per account (OpenAI UI guidelines). */
export function ChatGPTWelcome() {
  const qc = useQueryClient();
  const status = useChatGPTStatus();
  const pending = status.data?.accounts.find((a) => a.welcome_pending && a.plan_usage_enabled);
  const ack = useMutation({
    mutationFn: (id: string) => api.chatgptWelcomeShown(id),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.chatgpt }),
  });
  if (!pending) return null;
  return (
    <Dialog
      open
      onClose={() => ack.mutate(pending.id)}
      size="sm"
      title="You're using your ChatGPT plan"
      footer={
        <Button variant="primary" onClick={() => ack.mutate(pending.id)} data-autofocus>
          Got it
        </Button>
      }
    >
      <p className="text-sm leading-relaxed text-fg-2">
        Eligible AI requests in StarIndex now use your ChatGPT plan. You can review usage and adjust this app's limits in{" "}
        <a href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer" className="link">
          ChatGPT settings
        </a>
        . StarIndex itself has no subscription or charges.
      </p>
    </Dialog>
  );
}

