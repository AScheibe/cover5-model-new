import { useEffect, useId, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Game, Pick, SnapshotGame, Snapshots } from "../api/types";
import { errorText } from "../components/Toasts";
import { fmtDateTime, fmtKickoff, fmtPoints, fmtSpread, teamSpread } from "../lib/format";

// Categorical series colours, validated for CVD separation on the navy panel.
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#e66767", "#008300"];
const INK = "#a9b6d3";
const GRID = "#22314f";

interface Props {
  season: number;
  week: number;
  games: Game[];
  picks: Pick[];
  version: number;
}

const PICKS = "__picks__";

function tickTime(ms: number) {
  return fmtKickoff(new Date(ms).toISOString());
}

export function MovementChart({ season, week, games, picks, version }: Props) {
  const [data, setData] = useState<Snapshots | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<string>(PICKS);
  const selectId = useId();

  useEffect(() => {
    let alive = true;
    api
      .snapshots(season, week)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [season, week, version]);

  const byId = useMemo(() => new Map((data?.games ?? []).map((g) => [g.game_id, g])), [data]);
  const gameInfo = useMemo(() => new Map(games.map((g) => [g.game_id, g])), [games]);

  let body: React.ReactNode;
  if (error) body = <p className="field-error">Couldn't load line history: {error}</p>;
  else if (!data) body = <p className="muted">Loading line history...</p>;
  else if (!data.games.some((g) => g.series.length > 0))
    body = <p className="muted">No market snapshots yet. "Fetch lines" records one each time it runs.</p>;
  else if (sel === PICKS) body = <PicksEdgeChart picks={picks} byId={byId} />;
  else {
    const sg = byId.get(sel);
    body = sg ? <GameChart sg={sg} game={gameInfo.get(sel)} /> : <p className="muted">No snapshots for that game.</p>;
  }

  return (
    <section className="panel chart-panel" aria-label="Line movement">
      <div className="panel-head">
        <h2>Line movement</h2>
        <label htmlFor={selectId} className="sr-only">
          Game
        </label>
        <select id={selectId} className="select" value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value={PICKS}>Your picks: edge over time</option>
          {games.map((g) => (
            <option key={g.game_id} value={g.game_id}>
              {g.away} @ {g.home}
            </option>
          ))}
        </select>
      </div>
      {body}
    </section>
  );
}

function PicksEdgeChart({ picks, byId }: { picks: Pick[]; byId: Map<string, SnapshotGame> }) {
  // Stable colour per game: order by game_id, not by edge rank.
  const series = [...picks]
    .sort((a, b) => a.game_id.localeCompare(b.game_id))
    .map((p, i) => ({ pick: p, color: SERIES[i % SERIES.length]!, sg: byId.get(p.game_id) }))
    .filter((s) => s.sg && s.sg.series.length > 0);
  if (series.length === 0) return <p className="muted">No snapshots for your picks yet.</p>;

  const rows = new Map<number, Record<string, number>>();
  for (const s of series) {
    const sg = s.sg!;
    const league = teamSpread(sg.league_home_spread, s.pick.is_home);
    for (const pt of sg.series) {
      const m = teamSpread(pt.home_spread, s.pick.is_home);
      if (m == null || league == null) continue;
      const t = new Date(pt.t).getTime();
      const row = rows.get(t) ?? { t };
      row[s.pick.team] = league - m;
      rows.set(t, row);
    }
  }
  const data = [...rows.values()].sort((a, b) => a.t! - b.t!);
  return (
    <>
      <p className="chart-caption">
        Edge = league line minus market line, from each pick's side. Above 0 means the market moved toward your team.
      </p>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tickTime} stroke={INK} tick={{ fill: INK, fontSize: 11 }} />
            <YAxis stroke={INK} tick={{ fill: INK, fontSize: 11 }} width={44} label={{ value: "edge (pts)", angle: -90, position: "insideLeft", fill: INK, fontSize: 11 }} />
            <ReferenceLine y={0} stroke="#5d6f95" />
            <Tooltip
              contentStyle={{ background: "#0d1628", border: "1px solid #2a3b5e", borderRadius: 8 }}
              labelFormatter={(v) => fmtDateTime(new Date(Number(v)).toISOString())}
              formatter={(v: number, name: string) => [fmtPoints(v, 1), name]}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: INK }} />
            {series.map((s) => (
              <Line key={s.pick.team} type="stepAfter" dataKey={s.pick.team} name={s.pick.team} stroke={s.color} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

function GameChart({ sg, game }: { sg: SnapshotGame; game: Game | undefined }) {
  const team = game?.best_team ?? game?.picked_team ?? sg.home;
  const isHome = team === sg.home;
  const opp = isHome ? sg.away : sg.home;
  const league = teamSpread(sg.league_home_spread, isHome);
  const data = sg.series
    .map((pt) => ({ t: new Date(pt.t).getTime(), market: teamSpread(pt.home_spread, isHome), books: pt.books }))
    .filter((r) => r.market != null)
    .sort((a, b) => a.t - b.t);
  const values = data.map((d) => d.market as number).concat(league == null ? [] : [league]);
  const lo = Math.floor(Math.min(...values) - 1);
  const hi = Math.ceil(Math.max(...values) + 1);
  return (
    <>
      <p className="chart-caption">
        {team}'s spread ({team} {isHome ? "vs" : "@"} {opp}). The axis is flipped: higher on the chart means the market likes {team} more. The dashed line is the frozen league line.
      </p>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tickTime} stroke={INK} tick={{ fill: INK, fontSize: 11 }} />
            <YAxis
              reversed
              domain={[lo, hi]}
              allowDecimals
              stroke={INK}
              tick={{ fill: INK, fontSize: 11 }}
              tickFormatter={(v: number) => fmtSpread(v)}
              width={48}
              label={{ value: `${team} spread`, angle: -90, position: "insideLeft", fill: INK, fontSize: 11 }}
            />
            {league != null && (
              <ReferenceLine y={league} stroke="#e8eefc" strokeDasharray="5 4" label={{ value: `League ${fmtSpread(league)}`, fill: "#e8eefc", fontSize: 11, position: "insideTopRight" }} />
            )}
            <Tooltip
              contentStyle={{ background: "#0d1628", border: "1px solid #2a3b5e", borderRadius: 8 }}
              labelFormatter={(v) => fmtDateTime(new Date(Number(v)).toISOString())}
              formatter={(v: number) => [`${team} ${fmtSpread(v)}`, "Market"]}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: INK }} payload={[{ value: `Market (${team} view)`, type: "line", color: SERIES[0] }, ...(league != null ? [{ value: "League line (frozen)", type: "plainline" as const, color: "#e8eefc" }] : [])]} />
            <Line type="stepAfter" dataKey="market" name="Market" stroke={SERIES[0]} strokeWidth={2} dot={data.length < 30 ? { r: 3 } : false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
