import { useEffect, useMemo, useState } from "react";

/**
 * The server's clock, kept ticking in the browser.
 *
 * A week view carries the server time it was computed at (`view.now`); the
 * offset between that and the browser clock is taken when the view arrives,
 * so a page left open past a kickoff still knows the game has started even
 * though nothing was refetched. Re-renders every `tickMs`.
 */
export function useServerNow(serverNow: string | undefined, tickMs = 15000): string | undefined {
  const offset = useMemo(() => (serverNow ? new Date(serverNow).getTime() - Date.now() : 0), [serverNow]);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!serverNow || tickMs <= 0) return;
    const id = window.setInterval(() => setTick((t) => t + 1), tickMs);
    return () => window.clearInterval(id);
  }, [serverNow, tickMs]);
  return serverNow ? new Date(Date.now() + offset).toISOString() : undefined;
}

/** Has a game that kicks off at `kickoffIso` started by `nowIso` (the browser clock if missing)? */
export function hasKickedOff(kickoffIso: string, nowIso?: string): boolean {
  const t = nowIso ? new Date(nowIso).getTime() : Date.now();
  return new Date(kickoffIso).getTime() <= t;
}
