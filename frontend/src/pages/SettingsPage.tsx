import { useEffect, useId, useState } from "react";
import { api } from "../api/client";
import type { Provider, Settings, SettingsUpdate } from "../api/types";
import { useMeta } from "../components/MetaContext";
import { SchedulerPill } from "../components/SchedulerPill";
import { Loading, Spinner } from "../components/Spinner";
import { errorText, useToasts } from "../components/Toasts";
import { fmtDateTime } from "../lib/format";

interface Form {
  provider: Provider;
  ntfy_topic: string;
  ntfy_server: string;
  webhook_url: string;
  swap_margin: string;
  flip_margin: string;
  enabled: boolean;
  interval_minutes: string;
  sunday_interval_minutes: string;
  auto_init_wednesday: boolean;
}

function toForm(s: Settings): Form {
  const sch = s.scheduler ?? ({} as Partial<Settings["scheduler"]>);
  return {
    provider: s.provider,
    ntfy_topic: s.ntfy_topic ?? "",
    ntfy_server: s.ntfy_server ?? "",
    webhook_url: s.webhook_url ?? "",
    swap_margin: String(s.swap_margin ?? ""),
    flip_margin: String(s.flip_margin ?? ""),
    enabled: !!sch.enabled,
    interval_minutes: String(sch.interval_minutes ?? ""),
    sunday_interval_minutes: String(sch.sunday_interval_minutes ?? ""),
    auto_init_wednesday: !!sch.auto_init_wednesday,
  };
}

/** Only the fields that changed, as a partial Settings. */
export function settingsDiff(orig: Settings, f: Form, newKey: string): SettingsUpdate | string {
  const out: SettingsUpdate = {};
  const num = (s: string, name: string, min: number) => {
    const n = Number(s);
    if (s.trim() === "" || !Number.isFinite(n) || n < min) throw new Error(`${name} must be a number ≥ ${min}`);
    return n;
  };
  try {
    if (f.provider !== orig.provider) out.provider = f.provider;
    if (f.ntfy_topic !== (orig.ntfy_topic ?? "")) out.ntfy_topic = f.ntfy_topic.trim();
    if (f.ntfy_server !== (orig.ntfy_server ?? "")) out.ntfy_server = f.ntfy_server.trim();
    if (f.webhook_url !== (orig.webhook_url ?? "")) out.webhook_url = f.webhook_url.trim();
    const swap = num(f.swap_margin, "Swap margin", 0);
    const flip = num(f.flip_margin, "Flip margin", 0);
    if (swap !== orig.swap_margin) out.swap_margin = swap;
    if (flip !== orig.flip_margin) out.flip_margin = flip;
    const sch: NonNullable<SettingsUpdate["scheduler"]> = {};
    const iv = num(f.interval_minutes, "Interval", 1);
    const sun = num(f.sunday_interval_minutes, "Sunday interval", 1);
    if (f.enabled !== orig.scheduler.enabled) sch.enabled = f.enabled;
    if (iv !== orig.scheduler.interval_minutes) sch.interval_minutes = iv;
    if (sun !== orig.scheduler.sunday_interval_minutes) sch.sunday_interval_minutes = sun;
    if (f.auto_init_wednesday !== orig.scheduler.auto_init_wednesday) sch.auto_init_wednesday = f.auto_init_wednesday;
    if (Object.keys(sch).length) out.scheduler = sch;
    if (newKey.trim()) out.odds_api_key = newKey.trim();
  } catch (e) {
    return errorText(e);
  }
  return out;
}

function Field({ label, hint, children, id }: { label: string; hint?: string; children: React.ReactNode; id: string }) {
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function SettingsPage() {
  const toasts = useToasts();
  const { meta, refresh: refreshMeta } = useMeta();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [key, setKey] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const id = useId();

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s);
        setForm(toForm(s));
      })
      .catch((e) => setLoadError(errorText(e)));
  }, []);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  const diff = settings && form ? settingsDiff(settings, form, key) : {};
  const dirty = typeof diff === "string" || Object.keys(diff).length > 0;

  // Leaving (or reloading) the page with edits that aren't saved asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async (body: SettingsUpdate, what: string) => {
    setBusy(what);
    try {
      const s = await api.saveSettings(body);
      setSettings(s);
      if (what === "clear") {
        // Only the key changed: keep every other edit in the form (and a key being typed).
      } else {
        setForm(toForm(s));
        setKey("");
      }
      toasts.push({ kind: "success", text: what === "clear" ? "Odds API key cleared." : "Settings saved." });
      void refreshMeta();
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(null);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings || !form) return;
    const d = settingsDiff(settings, form, key);
    if (typeof d === "string") {
      toasts.push({ kind: "error", text: d });
      return;
    }
    if (Object.keys(d).length === 0) {
      toasts.push({ kind: "info", text: "Nothing changed." });
      return;
    }
    void save(d, "save");
  };

  const runNow = async () => {
    setBusy("run");
    setRunMsg(null);
    try {
      const r = await api.runScheduler();
      setRunMsg(r.message);
      if (r.outcome) toasts.outcome(r.outcome);
      void refreshMeta();
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(null);
    }
  };

  if (loadError) return <p className="panel field-error">{loadError}</p>;
  if (!settings || !form) return <Loading what="settings" />;
  const sch = meta?.scheduler;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <form className="settings" onSubmit={onSubmit}>
        <section className="panel">
          <h2>Odds</h2>
          <Field label="Provider" id={`${id}-prov`} hint="The Odds API averages several books (needs a key); ESPN needs no key; a local market file (COVER5_MARKET_FILE) is for offline demos and tests.">
            <select id={`${id}-prov`} className="select" value={form.provider} onChange={(e) => set("provider", e.target.value as Provider)}>
              <option value="oddsapi">The Odds API (oddsapi)</option>
              <option value="espn">ESPN (espn)</option>
              <option value="file">Local market file (file)</option>
            </select>
          </Field>
          <Field
            label="Odds API key"
            id={`${id}-key`}
            hint={settings.odds_api_key_set ? `A key is saved (${settings.odds_api_key_hint ?? "hidden"}). Type a new one to replace it.` : "No key saved."}
          >
            <div className="row gap">
              <input
                id={`${id}-key`}
                className="input"
                type="password"
                autoComplete="off"
                placeholder={settings.odds_api_key_set ? settings.odds_api_key_hint ?? "saved" : "paste key"}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              {settings.odds_api_key_set && (
                <button type="button" className="btn btn-ghost" disabled={busy != null} onClick={() => void save({ odds_api_key: "" }, "clear")}>
                  Clear key
                </button>
              )}
            </div>
          </Field>
        </section>

        <section className="panel">
          <h2>Alerts</h2>
          <Field label="ntfy topic" id={`${id}-topic`} hint="Subscribe to this topic in the ntfy phone app. Empty turns ntfy off.">
            <input id={`${id}-topic`} className="input" value={form.ntfy_topic} onChange={(e) => set("ntfy_topic", e.target.value)} />
          </Field>
          <Field label="ntfy server" id={`${id}-server`}>
            <input id={`${id}-server`} className="input" value={form.ntfy_server} placeholder="https://ntfy.sh" onChange={(e) => set("ntfy_server", e.target.value)} />
          </Field>
          <Field label="Webhook URL" id={`${id}-hook`} hint="Optional: receives each alert as JSON.">
            <input id={`${id}-hook`} className="input" type="url" value={form.webhook_url} onChange={(e) => set("webhook_url", e.target.value)} />
          </Field>
        </section>

        <section className="panel">
          <h2>Model</h2>
          <div className="grid-2">
            <Field label="Swap margin (points)" id={`${id}-swap`} hint="A new team replaces your weakest open pick only when its edge is better by at least this much.">
              <input id={`${id}-swap`} className="input" inputMode="decimal" value={form.swap_margin} onChange={(e) => set("swap_margin", e.target.value)} />
            </Field>
            <Field label="Flip margin (points)" id={`${id}-flip`} hint="An open pick switches sides once its edge falls below minus this much.">
              <input id={`${id}-flip`} className="input" inputMode="decimal" value={form.flip_margin} onChange={(e) => set("flip_margin", e.target.value)} />
            </Field>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Scheduler</h2>
            <SchedulerPill status={sch} />
          </div>
          <label className="check">
            <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
            Fetch lines and alert automatically while the server runs
          </label>
          <div className="grid-2">
            <Field label="Interval (minutes)" id={`${id}-iv`}>
              <input id={`${id}-iv`} className="input" inputMode="numeric" value={form.interval_minutes} onChange={(e) => set("interval_minutes", e.target.value)} />
            </Field>
            <Field label="Sunday interval (minutes)" id={`${id}-sun`} hint="Used Sundays 9:00-13:00 ET, before the early games.">
              <input id={`${id}-sun`} className="input" inputMode="numeric" value={form.sunday_interval_minutes} onChange={(e) => set("sunday_interval_minutes", e.target.value)} />
            </Field>
          </div>
          <label className="check">
            <input type="checkbox" checked={form.auto_init_wednesday} onChange={(e) => set("auto_init_wednesday", e.target.checked)} />
            Set up the week automatically on Wednesday (noon ET), seeded from the market
          </label>
          {sch && (
            <dl className="kv">
              <div className="kv-row">
                <dt>Last tick</dt>
                <dd>{fmtDateTime(sch.last_tick)}</dd>
              </div>
              <div className="kv-row">
                <dt>Last update</dt>
                <dd>{fmtDateTime(sch.last_update_at)}</dd>
              </div>
              <div className="kv-row">
                <dt>Next due</dt>
                <dd>{fmtDateTime(sch.next_due)}</dd>
              </div>
              {sch.last_result && (
                <div className="kv-row">
                  <dt>Last result</dt>
                  <dd>{sch.last_result}</dd>
                </div>
              )}
              {sch.last_error && (
                <div className="kv-row">
                  <dt>Last error</dt>
                  <dd className="field-error">{sch.last_error}</dd>
                </div>
              )}
            </dl>
          )}
          <div className="row gap">
            <button type="button" className="btn" disabled={busy != null} onClick={() => void runNow()}>
              {busy === "run" && <Spinner label="Running" />} Run scheduler now
            </button>
            {runMsg && (
              <span className="run-msg" role="status">
                {runMsg}
              </span>
            )}
          </div>
        </section>

        <div className="save-bar">
          {dirty && (
            <span className="unsaved" role="status">
              Unsaved changes
            </span>
          )}
          <button type="button" className="btn" disabled={busy != null} onClick={() => { setForm(toForm(settings)); setKey(""); }}>
            Reset
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy != null}>
            {busy === "save" && <Spinner label="Saving" />} Save settings
          </button>
        </div>
      </form>
    </div>
  );
}
