"""Format and deliver pick-change alerts."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from cover5 import config
from cover5.picks import Pick
from cover5.scoring import HOME, fmt_pick

ET = ZoneInfo("America/New_York")


def _status_tag(p: Pick, t=None) -> str:
    if t is not None and t.status in ("final", "live"):
        src = " (override)" if "override" in t.source else ""
        return f" [{t.status.upper()} {t.points:+g}{src}]"
    if p.manual:
        return " [LOCKED by you]"
    if p.locked:
        return " [LOCKED]"
    return ""


def _mark(label: str, overridden: bool) -> str:
    """'ARI +7 @ NYG' -> 'ARI +7* @ NYG' when the league line is a user override."""
    if not overridden:
        return label
    team, num, rest = label.split(" ", 2)
    return f"{team} {num}* {rest}"


def _pick_line(p: Pick, board: pd.DataFrame, t=None) -> str:
    r = board[board.game_id == p.game_id].iloc[0]
    is_home = p.side == HOME
    opp = r.away if is_home else r.home
    league = _mark(fmt_pick(p.team, opp, r.league_home_spread, is_home), r.line_overridden)
    market = fmt_pick(p.team, opp, r.market_home_spread, is_home).split()[1]
    return f"{league} (market {market}, edge {p.edge:+.1f}){_status_tag(p, t)}"


def format_alert(season: int, week: int, picks: list[Pick], diff: dict, board: pd.DataFrame,
                 when: datetime | None = None, tallies=None, warnings: list[str] | None = None,
                 reason: str = "") -> tuple[str, str]:
    from cover5.tally import summarize, summary_line
    when = (when or datetime.now(tz=ET)).astimezone(ET)
    title = f"Cover 5 wk{week}: " + ("picks changed" if diff["changed"] else "no change")
    by_game = {t.pick.game_id: t for t in (tallies or [])}
    lines = [f"{when:%a %b %d %I:%M %p ET}" + (f"  ({reason})" if reason else "")]
    for w in warnings or []:
        lines.append(f"WARNING: {w}")
    if diff["changed"]:
        lines.append("")
        lines.append("DO THIS:")
        for p in diff["dropped"]:
            lines.append(f"  - drop  {p.team}")
        for p in diff["added"]:
            lines.append(f"  + add   {_pick_line(p, board)}")
        for p in diff["flipped"]:
            lines.append(f"  ~ flip to {_pick_line(p, board)}")
    lines.append("")
    lines.append("CURRENT PICKS:")
    for i, p in enumerate(picks, 1):
        lines.append(f"  {i}. {_pick_line(p, board, by_game.get(p.game_id))}")
    picked = {p.game_id for p in picks}
    bench = board[~board.game_id.isin(picked) & ~board.locked & board.edge.notna()].head(3)
    if len(bench):
        lines.append("")
        lines.append("NEXT UP (not picked):")
        for r in bench.itertuples():
            is_home = r.best_side == HOME
            opp = r.away if is_home else r.home
            league = _mark(fmt_pick(r.best_team, opp, r.league_home_spread, is_home), r.line_overridden)
            market = fmt_pick(r.best_team, opp, r.market_home_spread, is_home).split()[1]
            lines.append(f"  {league} (market {market}, edge {r.edge:+.1f})")
    lines.append("")
    if tallies is not None:
        lines.append(summary_line(summarize(tallies)))
    else:
        lines.append(f"Expected week score from line movement: {sum(p.edge for p in picks):+.1f}")
    if board.line_overridden.any():
        lines.append("* league line set by your override")
    return title, "\n".join(lines)


def send(title: str, body: str, force: bool = False, changed: bool = True) -> None:
    """Print always; push to ntfy/webhook only when picks changed (or force)."""
    print(title)
    print(body)
    if not (changed or force):
        return
    if config.NTFY_TOPIC:
        try:
            requests.post(f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}", data=body.encode(),
                          headers={"Title": title, "Priority": "high" if changed else "default",
                                   "Tags": "football"}, timeout=15)
        except requests.RequestException as e:
            print(f"[alerts] ntfy failed: {e}")
    if config.ALERT_WEBHOOK_URL:
        try:
            text = f"*{title}*\n```\n{body}\n```"
            requests.post(config.ALERT_WEBHOOK_URL, json={"text": text, "content": text}, timeout=15)
        except requests.RequestException as e:
            print(f"[alerts] webhook failed: {e}")
