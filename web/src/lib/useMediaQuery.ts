/**
 * A media query, as React state.
 *
 * `useSyncExternalStore` rather than `useState` + an effect, because the
 * first render has to get the answer right: a layout that renders the
 * desktop arrangement and then corrects itself is a visible jump on every
 * page load, and on a phone the jump is the whole report moving.
 *
 * This application is client-rendered, so there is no server snapshot to
 * disagree with. The third argument is supplied anyway: without it, any
 * future server render would throw rather than fall back.
 */

import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => {};
      const list = window.matchMedia(query);
      // `addEventListener` rather than `addListener`: the latter is
      // deprecated and WebKit removed it from the type surface years after
      // keeping the behaviour, which is a lint error waiting to happen.
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );

  const read = useCallback(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  }, [query]);

  return useSyncExternalStore(subscribe, read, () => false);
}
