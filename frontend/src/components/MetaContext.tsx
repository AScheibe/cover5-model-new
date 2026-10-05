import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Meta } from "../api/types";

interface MetaState {
  meta: Meta | null;
  error: string | null;
  refresh: () => Promise<void>;
}

const Ctx = createContext<MetaState | null>(null);

/** /api/meta, refreshed every minute so the scheduler pill stays current. */
export function MetaProvider({ children, pollMs = 60000 }: { children: ReactNode; pollMs?: number }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const m = await api.meta();
      if (alive.current) {
        setMeta(m);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const id = pollMs > 0 ? window.setInterval(() => void refresh(), pollMs) : undefined;
    return () => {
      alive.current = false;
      if (id) window.clearInterval(id);
    };
  }, [refresh, pollMs]);

  return <Ctx.Provider value={{ meta, error, refresh }}>{children}</Ctx.Provider>;
}

export function useMeta(): MetaState {
  const c = useContext(Ctx);
  if (!c) throw new Error("useMeta outside MetaProvider");
  return c;
}
