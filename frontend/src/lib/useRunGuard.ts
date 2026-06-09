"use client";

import { useEffect } from "react";

// Guard a long-running playground experiment against accidental navigation.
//
// WHY: the playground experiments hold a live SSE connection in component state.
// When the page component unmounts (the judge clicks another sidebar link, or
// refreshes), React tears down the AbortController and drops all useState — so
// the run silently dies and the board resets to its initial state. Experiment 2
// runs ~200s, so a stray click loses a lot of waiting.
//
// Next.js App Router has no public API to block client-side <Link> navigation,
// so we intercept at the DOM level while `running` is true:
//   1. beforeunload  — covers browser refresh / tab close / external nav.
//   2. capture-phase click on any <a> — covers in-app <Link> clicks (Next Link
//      renders a real <a>); we confirm() and cancel the click if the judge
//      declines, before Next's own click handler runs.
//
// No-ops entirely when `running` is false (listeners are only attached during a
// live run), so normal navigation is never affected.
export function useRunGuard(running: boolean, message?: string): void {
  useEffect(() => {
    if (!running) return;

    const prompt =
      message ??
      "An experiment is still running. Leaving this page will stop it. Leave anyway?";

    // 1) refresh / tab close / external navigation
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Legacy requirement: setting returnValue triggers the browser's own
      // confirm dialog. The custom string is ignored by modern browsers.
      e.returnValue = prompt;
      return prompt;
    };

    // 2) in-app <Link> clicks — intercept in the capture phase so we run before
    //    Next's router handler and can cancel the navigation.
    const onClickCapture = (e: MouseEvent) => {
      // only care about plain left-clicks without modifier keys
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
        return;
      }
      const anchor = (e.target as HTMLElement | null)?.closest("a");
      if (!anchor) return;
      const href = anchor.getAttribute("href");
      // ignore non-navigations: new-tab links, hash-only, downloads, external.
      if (
        !href ||
        anchor.target === "_blank" ||
        href.startsWith("#") ||
        anchor.hasAttribute("download")
      ) {
        return;
      }
      // same-page link — no navigation, let it through
      if (href === window.location.pathname) return;

      if (!window.confirm(prompt)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };

    window.addEventListener("beforeunload", onBeforeUnload);
    // capture: true so we see the click before React/Next's bubbling handlers.
    document.addEventListener("click", onClickCapture, true);

    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClickCapture, true);
    };
  }, [running, message]);
}
