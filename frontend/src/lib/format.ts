// Number, spread and time formatting. Times always render in the viewer's
// local timezone (Intl picks it up from the browser).

function trim(n: number): string {
  const r = Math.round(n * 10) / 10;
  return Number.isInteger(r) ? r.toFixed(0) : r.toFixed(1);
}

/** League-app style spread: +2.5, -3, PK. */
export function fmtSpread(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 1e-9) return "PK";
  return n > 0 ? `+${trim(n)}` : trim(n);
}

/** Points with sign: +8, -3.5, 0. */
export function fmtPoints(n: number | null | undefined, digits?: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  const s = digits == null ? trim(Math.abs(n)) : Math.abs(n).toFixed(digits);
  if (Math.abs(n) < 1e-9) return digits == null ? "0" : (0).toFixed(digits);
  return (n > 0 ? "+" : "-") + s;
}

/** A home spread converted to one team's point of view. */
export function teamSpread(homeSpread: number | null | undefined, isHome: boolean): number | null {
  if (homeSpread == null) return null;
  const v = isHome ? homeSpread : -homeSpread;
  return v === 0 ? 0 : v;
}

/** "IND -3.5 @ WAS" / "CHI -3 vs NYJ" */
export function pickLabel(team: string, opponent: string, spread: number | null, isHome: boolean): string {
  return `${team} ${fmtSpread(spread)} ${isHome ? "vs" : "@"} ${opponent}`;
}

/** Parse a spread typed by the user; "PK"/"pick"/"0" are a pick'em. */
export function parseSpreadInput(raw: string): number | "PK" | null {
  const s = raw.trim().toUpperCase().replace(/\s+/g, "");
  if (s === "PK" || s === "PICK" || s === "PICKEM" || s === "EVEN") return "PK";
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(s)) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return n === 0 ? "PK" : n;
}

export function parseNumber(raw: string): number | null {
  const s = raw.trim();
  if (s === "" || !/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

const dayTime = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
const dateTime = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Sun 1:05 PM" */
export function fmtKickoff(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? dayTime.format(d) : "—";
}

/** "Sun, Oct 4, 5:05 PM" */
export function fmtDateTime(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? dateTime.format(d) : "—";
}

export function fmtShortDate(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? shortDate.format(d) : "—";
}

/** "5 min ago", "3 h ago", "in 12 min" relative to now. */
export function fmtAgo(iso: string | null | undefined, now: Date = new Date()): string {
  const d = toDate(iso);
  if (!d) return "never";
  const diff = (now.getTime() - d.getTime()) / 60000;
  const a = Math.abs(diff);
  let s: string;
  if (a < 1) s = "just now";
  else if (a < 60) s = `${Math.round(a)} min`;
  else if (a < 48 * 60) s = `${Math.round(a / 60)} h`;
  else s = `${Math.round(a / 1440)} d`;
  if (s === "just now") return s;
  return diff >= 0 ? `${s} ago` : `in ${s}`;
}

export function signClass(n: number | null | undefined): "pos" | "neg" | "zero" {
  if (n == null || Math.abs(n) < 1e-9) return "zero";
  return n > 0 ? "pos" : "neg";
}
