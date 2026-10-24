"use client";

import { useEffect } from "react";
import type { LiveConversation } from "@/lib/liveConversation";

/** Visible entry and continuous dwell only; the server owns opportunities and tasks. */
export function useVisitOutreach(live: LiveConversation | null, conversationId: string | null, blocked: boolean) {
  useEffect(() => {
    if (!live || !conversationId || blocked) return;
    let lookup: AbortController | null = null;
    const enter = () => {
      lookup?.abort();
      if (document.visibilityState !== "visible") {
        live.cancelNightVisit();
        return;
      }
      const controller = new AbortController();
      lookup = controller;
      void (async () => {
        try {
          const response = await fetch(`/api/visit-outreach/dwell/${conversationId}`, {
            signal: controller.signal, cache: "no-store",
          });
          if (!response.ok) return;
          const policy = await response.json();
          if (!controller.signal.aborted && document.visibilityState === "visible"
            && typeof policy.seconds === "number" && Number.isFinite(policy.seconds)
            && policy.seconds >= 0 && policy.seconds <= 3600)
            await live.nightVisit(policy.seconds, true);
        } catch { /* A failed/cancelled lookup must not start a model request. */ }
      })();
    };
    enter();
    document.addEventListener("visibilitychange", enter);
    return () => {
      document.removeEventListener("visibilitychange", enter);
      lookup?.abort();
      live.cancelNightVisit();
    };
  }, [live, conversationId, blocked]);
}
