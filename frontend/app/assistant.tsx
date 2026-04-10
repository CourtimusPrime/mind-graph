"use client";

import { memo, useEffect, useState } from "react";
import { AssistantRuntimeProvider, useAssistantRuntime } from "@assistant-ui/react";
import {
  useChatRuntime,
  AssistantChatTransport,
} from "@assistant-ui/react-ai-sdk";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { GraphPanel } from "@/components/assistant-ui/graph-panel";
import { useThreadStore, type StoredMessage } from "@/lib/thread-store";
import type { ThreadMessage } from "@assistant-ui/react";

// Converts stored messages back to UIMessage shape for useChatRuntime
function toInitialMessages(stored: StoredMessage[]) {
  return stored.map((m) => ({
    id: m.id,
    role: m.role as "user" | "assistant",
    parts: [{ type: "text" as const, text: m.text }],
  }));
}

// Converts a ThreadMessage to StoredMessage
function toStoredMessage(m: ThreadMessage): StoredMessage | null {
  if (m.role !== "user" && m.role !== "assistant") return null;
  const text =
    m.content
      .filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("") ?? "";
  return { id: m.id, role: m.role, text };
}

// Invisible component — syncs runtime messages to localStorage on every change
function MessageSyncer({ threadId }: { threadId: string }) {
  const runtime = useAssistantRuntime();
  const saveMessages = useThreadStore((s) => s.saveMessages);

  useEffect(() => {
    return runtime.thread.subscribe(() => {
      const msgs = runtime.thread.getState().messages;
      const stored = msgs
        .map(toStoredMessage)
        .filter((m): m is StoredMessage => m !== null);
      saveMessages(threadId, stored);
    });
  }, [runtime, threadId, saveMessages]);

  return null;
}

// Per-thread chat component — remounts on threadId change via key prop.
// Wrapped in memo to prevent re-renders when the parent (Assistant) re-renders
// due to Zustand store updates — those re-renders must not reach useChatRuntime
// or the runtime will re-initialize and re-trigger MessageSyncer's subscriber.
const ThreadChat = memo(function ThreadChat({ threadId }: { threadId: string }) {
  // Load initial messages once on mount; use getState() to avoid subscribing
  // to the store and triggering re-renders when messages are saved.
  const [initialMessages] = useState(() =>
    toInitialMessages(useThreadStore.getState().loadMessages(threadId)),
  );

  const runtime = useChatRuntime({
    transport: new AssistantChatTransport({
      api: "/api/chat",
      body: { threadId },
    }),
    messages: initialMessages,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex min-w-0 flex-1 flex-col">
        <Thread />
      </div>
      <MessageSyncer threadId={threadId} />
    </AssistantRuntimeProvider>
  );
});

export const Assistant = () => {
  const { threads, activeThreadId, createThread, switchThread, deleteThread } =
    useThreadStore();

  return (
    <div className="flex h-dvh overflow-hidden">
      <ThreadList
        threads={threads}
        activeId={activeThreadId}
        onNew={createThread}
        onSwitch={switchThread}
        onDelete={deleteThread}
      />
      <ThreadChat key={activeThreadId} threadId={activeThreadId} />
      <GraphPanel threadId={activeThreadId} />
    </div>
  );
};
