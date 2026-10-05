import { useState } from "react";
import type { ScheduleWeek } from "../api/types";
import { Spinner } from "../components/Spinner";
import { fmtDateTime } from "../lib/format";

interface Props {
  season: number;
  week: number;
  info: ScheduleWeek | undefined;
  busy: string | null;
  onInit: (useMarket: boolean) => void;
}

export function SetupWeek({ season, week, info, busy, onInit }: Props) {
  const [source, setSource] = useState<"market" | "nflverse">("market");
  return (
    <section className="panel empty-state" aria-label="Set up this week">
      <h2>
        {season} week {week} isn't set up yet
      </h2>
      <p>
        The league publishes its spreads on Wednesday and they stay frozen all week. Setting up creates this week's league
        sheet{info ? ` for its ${info.n_games} games` : ""}, so the model can compare the sportsbook market against it.
        {info?.first_kickoff && <> First kickoff: {fmtDateTime(info.first_kickoff)}.</>}
      </p>
      <fieldset className="radio-list">
        <legend>Seed the league lines from</legend>
        <label className="check">
          <input type="radio" name="seed" checked={source === "market"} onChange={() => setSource("market")} />
          <span>
            <strong>Current sportsbook lines</strong> (closest to what the league posts on Wednesday)
          </span>
        </label>
        <label className="check">
          <input type="radio" name="seed" checked={source === "nflverse"} onChange={() => setSource("nflverse")} />
          <span>
            <strong>nflverse spreads</strong> (no odds fetch; good for past weeks or offline)
          </span>
        </label>
      </fieldset>
      <p className="hint">Afterwards, click any league line in the games table to correct it to exactly what the app shows.</p>
      <button type="button" className="btn btn-primary btn-lg" disabled={busy != null} onClick={() => onInit(source === "market")}>
        {busy === "init" && <Spinner label="Setting up" />}
        Set up this week
      </button>
    </section>
  );
}
