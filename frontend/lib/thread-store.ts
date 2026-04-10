"use client";

import { create } from "zustand";

export interface StoredThread {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

const THREADS_KEY = "mg-threads";
const msgsKey = (id: string) => `mg-msgs-${id}`;

function loadThreads(): StoredThread[] {
  try {
    return JSON.parse(localStorage.getItem(THREADS_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveThreadsToStorage(threads: StoredThread[]) {
  localStorage.setItem(THREADS_KEY, JSON.stringify(threads));
}

function makeThread(): StoredThread {
  return {
    id: crypto.randomUUID(),
    title: "New Chat",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

interface ThreadStore {
  threads: StoredThread[];
  activeThreadId: string;
  // Actions
  createThread: () => void;
  switchThread: (id: string) => void;
  deleteThread: (id: string) => void;
  renameThread: (id: string, title: string) => void;
  saveMessages: (threadId: string, msgs: StoredMessage[]) => void;
  loadMessages: (threadId: string) => StoredMessage[];
  clearAll: () => void;
}

function initState(): { threads: StoredThread[]; activeThreadId: string } {
  // SSR guard — localStorage not available on server
  if (typeof window === "undefined") {
    return { threads: [], activeThreadId: "" };
  }
  const threads = loadThreads();
  if (threads.length === 0) {
    const t = makeThread();
    saveThreadsToStorage([t]);
    return { threads: [t], activeThreadId: t.id };
  }
  // Most recently updated first → activate that one
  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt);
  return { threads: sorted, activeThreadId: sorted[0].id };
}

export const useThreadStore = create<ThreadStore>((set, get) => {
  const init = initState();

  return {
    threads: init.threads,
    activeThreadId: init.activeThreadId,

    createThread() {
      const t = makeThread();
      set((s) => {
        const threads = [t, ...s.threads];
        saveThreadsToStorage(threads);
        return { threads, activeThreadId: t.id };
      });
    },

    switchThread(id) {
      set({ activeThreadId: id });
    },

    deleteThread(id) {
      set((s) => {
        const threads = s.threads.filter((t) => t.id !== id);
        localStorage.removeItem(msgsKey(id));
        if (threads.length === 0) {
          const t = makeThread();
          saveThreadsToStorage([t]);
          return { threads: [t], activeThreadId: t.id };
        }
        saveThreadsToStorage(threads);
        const activeThreadId =
          s.activeThreadId === id ? threads[0].id : s.activeThreadId;
        return { threads, activeThreadId };
      });
    },

    renameThread(id, title) {
      set((s) => {
        const threads = s.threads.map((t) =>
          t.id === id ? { ...t, title } : t,
        );
        saveThreadsToStorage(threads);
        return { threads };
      });
    },

    saveMessages(threadId, msgs) {
      // Nothing to persist — skip entirely to avoid spurious Zustand updates
      // that would cascade re-renders back into useChatRuntime.
      if (msgs.length === 0) return;

      try {
        localStorage.setItem(msgsKey(threadId), JSON.stringify(msgs));
      } catch {
        // storage quota — silently ignore
      }
      // Update thread title from first user message and bump updatedAt
      set((s) => {
        const thread = s.threads.find((t) => t.id === threadId);
        if (!thread) return {};
        const firstUser = msgs.find((m) => m.role === "user");
        const title =
          thread.title === "New Chat" && firstUser
            ? firstUser.text.slice(0, 48)
            : thread.title;
        const threads = s.threads.map((t) =>
          t.id === threadId ? { ...t, title, updatedAt: Date.now() } : t,
        );
        saveThreadsToStorage(threads);
        return { threads };
      });
    },

    loadMessages(threadId) {
      if (typeof window === "undefined") return [];
      try {
        return JSON.parse(
          localStorage.getItem(msgsKey(threadId)) ?? "[]",
        ) as StoredMessage[];
      } catch {
        return [];
      }
    },

    clearAll() {
      get().threads.forEach((t) => localStorage.removeItem(msgsKey(t.id)));
      const first = makeThread();
      localStorage.setItem(THREADS_KEY, JSON.stringify([first]));
      set({ threads: [first], activeThreadId: first.id });
    },
  };
});
