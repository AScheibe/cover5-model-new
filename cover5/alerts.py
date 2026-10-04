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


def _pick_line(p: Pick, board: pd.DataFrame) -> str:
    r = board[board.game_id == p.game_id].iloc[0]
    is_home = p.side == HOME
    opp = r.away if is_home else r.home
    lock = " [LOCKED]" if p.locked else ""
    league = fmt_pick(p.team, opp, r.league_home_spread, is_home)
    market = fmt_pick(p.team, opp, r.market_home_spread, is_home).split()[1]
    return f"{league} (market {market}, edge {p.edge:+.1f}){lock}"


def format_alert(season: int, week: int, picks: list[Pick], diff: dict, board: pd.DataFrame,
                 when: datetime | None = None) -> tuple[str, str]:
    when = (when or datetime.now(tz=ET)).astimezone(ET)
    title = f"Cover 5 wk{week}: " + (
        "picks changed" if diff["changed"] else "no change")
    lines = [f"{when:%a %b %d %I:%M %p ET}"]
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
        lines.append(f"  {i}. {_pick_line(p, board)}")
    picked = {p.game_id for p in picks}
    bench = board[~board.game_id.isin(picked) & ~board.locked & board.edge.notna()].head(3)
    if len(bench):
        lines.append("")
        lines.append("NEXT UP (not picked):")
        for r in bench.itertuples():
            is_home = r.best_side == HOME
            opp = r.away if is_home else r.home
            league = fmt_pick(r.best_team, opp, r.league_home_spread, is_home)
            market = fmt_pick(r.best_team, opp, r.market_home_spread, is_home).split()[1]
            lines.append(f"  {league} (market {market}, edge {r.edge:+.1f})")
    total = sum(p.edge for p in picks)
    lines.append("")
    lines.append(f"Expected week score from line movement: {total:+.1f}")
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
