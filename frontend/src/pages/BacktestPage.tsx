import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Json } from "../api/types";
import { Loading } from "../components/Spinner";
import { errorText } from "../components/Toasts";

type Obj = { [k: string]: Json };

const isObj = (v: Json | undefined): v is Obj => v != null && typeof v === "object" && !Array.isArray(v);
const isScalar = (v: Json | undefined) => v == null || typeof v !== "object";
const isFlatObj = (v: Json | undefined): v is Obj => isObj(v) && Object.values(v).every(isScalar);

/** A dict whose values are all flat objects renders as one table: a row per key. */
const isTableOfRows = (v: Json | undefined): v is { [k: string]: Obj } =>
  isObj(v) && Object.keys(v).length > 0 && Object.values(v).every(isFlatObj);
const isArrayOfRows = (v: Json | undefined): v is Obj[] => Array.isArray(v) && v.length > 0 && v.every(isFlatObj);

export function humanKey(k: string): string {
  return k.replace(/_/g, " ").replace(/\bpts\b/, "points").replace(/\bse\b/, "SE").replace(/\bsd\b/, "SD");
}

/** Probabilities and rates read better as percentages. */
function isProbKey(k: string): boolean {
  return /^p_|_rate$|^pct|_pct$|^share/.test(k);
}

export function fmtValue(v: Json, key = ""): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    if (isProbKey(key) && v >= 0 && v <= 1) return `${(v * 100).toFixed(1)}%`;
    if (Number.isInteger(v)) return v.toLocaleString();
    const a = Math.abs(v);
    return v.toFixed(a >= 100 ? 1 : a >= 1 ? 2 : 3);
  }
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function numClass(v: Json, key: string): string {
  if (typeof v !== "number") return "";
  if (/^(mean|z_|total|mean_diff)/.test(key)) return v > 0 ? "pos" : v < 0 ? "neg" : "";
  return "";
}

function RowsTable({ rows, keyHeader, caption }: { rows: [string, Obj][]; keyHeader?: string; caption?: string }) {
  const cols: string[] = [];
  for (const [, r] of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
  return (
    <div className="table-wrap">
      <table className="data-table">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {keyHeader != null && <th scope="col">{keyHeader}</th>}
            {cols.map((c) => (
              <th key={c} scope="col" title={c}>
                {humanKey(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, r]) => (
            <tr key={k}>
              {keyHeader != null && (
                <th scope="row" className="nowrap">
                  {k}
                </th>
              )}
              {cols.map((c) => (
                <td key={c} className={`num ${numClass(r[c] ?? null, c)}`}>
                  {fmtValue(r[c] ?? null, c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KeyValues({ obj }: { obj: Obj }) {
  const entries = Object.entries(obj).filter(([, v]) => isScalar(v) || (Array.isArray(v) && v.every(isScalar)));
  if (entries.length === 0) return null;
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <div key={k} className="kv-row">
          <dt>{humanKey(k)}</dt>
          <dd>{Array.isArray(v) ? v.map((x) => fmtValue(x, k)).join(", ") : fmtValue(v, k)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Renders any JSON value: scalars as key/values, dicts of flat dicts and arrays of flat dicts as tables. */
export function JsonSection({ name, value, depth = 0 }: { name: string; value: Json; depth?: number }) {
  const H = depth === 0 ? "h2" : "h3";
  if (isTableOfRows(value)) {
    return (
      <section className={depth === 0 ? "panel" : "subsection"}>
        <H>{humanKey(name)}</H>
        <RowsTable rows={Object.entries(value)} keyHeader="" caption={name} />
      </section>
    );
  }
  if (isArrayOfRows(value)) {
    return (
      <section className={depth === 0 ? "panel" : "subsection"}>
        <H>{humanKey(name)}</H>
        <RowsTable rows={value.map((r, i) => [String(i), r])} caption={name} />
      </section>
    );
  }
  if (isObj(value)) {
    const nested = Object.entries(value).filter(([, v]) => !isScalar(v) && !(Array.isArray(v) && v.every(isScalar)));
    return (
      <section className={depth === 0 ? "panel" : "subsection"}>
        <H>{humanKey(name)}</H>
        <KeyValues obj={value} />
        {nested.map(([k, v]) => (
          <JsonSection key={k} name={k} value={v} depth={depth + 1} />
        ))}
      </section>
    );
  }
  if (Array.isArray(value)) {
    return (
      <section className={depth === 0 ? "panel" : "subsection"}>
        <H>{humanKey(name)}</H>
        <pre className="alert-body">{JSON.stringify(value, null, 2)}</pre>
      </section>
    );
  }
  return null;
}

export function BacktestPage() {
  const [data, setData] = useState<Json | undefined>(undefined);
  const [error, setError] = useState<{ status: number; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .backtest()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError({ status: e instanceof ApiError ? e.status : 0, text: errorText(e) }));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Backtest</h1>
      </header>
      {error?.status === 404 ? (
        <div className="panel empty-state">
          <h2>No backtest results yet</h2>
          <p>
            The backtest compares pick strategies (line movement, favourites, random, ...) on past seasons and saves the results
            to <code>data/history/backtest_results.json</code>. Generate it with:
          </p>
          <pre className="alert-body">python -m cover5.backtest_movement</pre>
          <p className="hint">Then reload this page.</p>
        </div>
      ) : error ? (
        <p className="panel field-error">{error.text}</p>
      ) : data === undefined ? (
        <Loading what="backtest" />
      ) : isObj(data) ? (
        <BacktestBody data={data} />
      ) : (
        <JsonSection name="results" value={data} />
      )}
    </div>
  );
}

function BacktestBody({ data }: { data: Obj }) {
  // Strategies first when present, then everything else in file order.
  const keys = Object.keys(data);
  const first = keys.filter((k) => /strateg/i.test(k));
  const nested = keys.filter((k) => !first.includes(k) && !isScalar(data[k]) && !(Array.isArray(data[k]) && (data[k] as Json[]).every(isScalar)));
  const scalars: Obj = {};
  for (const k of keys) if (!first.includes(k) && !nested.includes(k)) scalars[k] = data[k]!;
  return (
    <>
      {Object.keys(scalars).length > 0 && (
        <section className="panel">
          <h2>Run</h2>
          <KeyValues obj={scalars} />
        </section>
      )}
      {first.map((k) => (
        <JsonSection key={k} name={k} value={data[k]!} />
      ))}
      {nested.map((k) => (
        <JsonSection key={k} name={k} value={data[k]!} />
      ))}
    </>
  );
}
