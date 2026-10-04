import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Outcome, WeekView } from "../api/types";
import { errorText, useToasts } from "../components/Toasts";

export interface WeekState {
  view: WeekView | null;
  loadError: string | null;
  /** Label of the request in flight; every mutation button is disabled while set. */
  busy: string | null;
  /** Bumped after each mutation so side panels (chart, alert log) refetch. */
  version: number;
  reload: () => Promise<void>;
  run: (label: string, fn: () => Promise<Outcome>) => Promise<Outcome | null>;
}

/**
 * The week view for (season, week). Mutations replace the view with the
 * Outcome's week. A generation counter discards any GET that started before
 * the latest mutation or week change, so a slow refetch never overwrites a
 * newer outcome.
 */
export function useWeek(season: number, week: number): WeekState {
  const toasts = useToasts();
  const [view, setView] = useState<WeekView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const gen = useRef(0);
  const busyRef = useRef(false);
  const current = useRef({ season, week });
  current.current = { season, week };

  const reload = useCallback(async () => {
    const g = ++gen.current;
    try {
      const v = await api.week(season, week);
      if (g === gen.current) {
        setView(v);
        setLoadError(null);
      }
    } catch (e) {
      if (g === gen.current) setLoadError(errorText(e));
    }
  }, [season, week]);

  useEffect(() => {
    setView(null);
    setLoadError(null);
    void reload();
  }, [reload]);

  const run = useCallback(
    async (label: string, fn: () => Promise<Outcome>) => {
      if (busyRef.current) return null;
      busyRef.current = true;
      setBusy(label);
      ++gen.current;
      try {
        const o = await fn();
        const cur = current.current;
        if (o?.week && o.week.season === cur.season && o.week.week === cur.week) {
          ++gen.current;
          setView(o.week);
          setLoadError(null);
        }
        toasts.outcome(o);
        setVersion((v) => v + 1);
        return o;
      } catch (e) {
        toasts.error(e);
        return null;
      } finally {
        busyRef.current = false;
        setBusy(null);
      }
    },
    [toasts],
  );

  return { view, loadError, busy, version, reload, run };
}

/** Run two dependent mutations and keep both sets of messages. */
export async function chain(first: () => Promise<Outcome>, second: () => Promise<Outcome>): Promise<Outcome> {
  const a = await first();
  const b = await second();
  return { ...b, messages: [...(a.messages ?? []), ...(b.messages ?? [])] };
}
