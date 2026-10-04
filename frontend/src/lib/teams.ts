// NFL team colours keyed by nflverse code (LA is the Rams). primary fills the
// tile; secondary is the accent stripe.
export interface TeamInfo {
  name: string;
  primary: string;
  secondary: string;
}

export const TEAMS: Record<string, TeamInfo> = {
  ARI: { name: "Arizona Cardinals", primary: "#97233F", secondary: "#FFB612" },
  ATL: { name: "Atlanta Falcons", primary: "#A71930", secondary: "#000000" },
  BAL: { name: "Baltimore Ravens", primary: "#241773", secondary: "#9E7C0C" },
  BUF: { name: "Buffalo Bills", primary: "#00338D", secondary: "#C60C30" },
  CAR: { name: "Carolina Panthers", primary: "#0085CA", secondary: "#101820" },
  CHI: { name: "Chicago Bears", primary: "#0B162A", secondary: "#C83803" },
  CIN: { name: "Cincinnati Bengals", primary: "#FB4F14", secondary: "#000000" },
  CLE: { name: "Cleveland Browns", primary: "#311D00", secondary: "#FF3C00" },
  DAL: { name: "Dallas Cowboys", primary: "#003594", secondary: "#869397" },
  DEN: { name: "Denver Broncos", primary: "#FB4F14", secondary: "#002244" },
  DET: { name: "Detroit Lions", primary: "#0076B6", secondary: "#B0B7BC" },
  GB: { name: "Green Bay Packers", primary: "#203731", secondary: "#FFB612" },
  HOU: { name: "Houston Texans", primary: "#03202F", secondary: "#A71930" },
  IND: { name: "Indianapolis Colts", primary: "#002C5F", secondary: "#A2AAAD" },
  JAX: { name: "Jacksonville Jaguars", primary: "#006778", secondary: "#D7A22A" },
  KC: { name: "Kansas City Chiefs", primary: "#E31837", secondary: "#FFB81C" },
  LA: { name: "Los Angeles Rams", primary: "#003594", secondary: "#FFA300" },
  LAC: { name: "Los Angeles Chargers", primary: "#0080C6", secondary: "#FFC20E" },
  LV: { name: "Las Vegas Raiders", primary: "#000000", secondary: "#A5ACAF" },
  MIA: { name: "Miami Dolphins", primary: "#008E97", secondary: "#FC4C02" },
  MIN: { name: "Minnesota Vikings", primary: "#4F2683", secondary: "#FFC62F" },
  NE: { name: "New England Patriots", primary: "#002244", secondary: "#C60C30" },
  NO: { name: "New Orleans Saints", primary: "#D3BC8D", secondary: "#101820" },
  NYG: { name: "New York Giants", primary: "#0B2265", secondary: "#A71930" },
  NYJ: { name: "New York Jets", primary: "#125740", secondary: "#FFFFFF" },
  PHI: { name: "Philadelphia Eagles", primary: "#004C54", secondary: "#A5ACAF" },
  PIT: { name: "Pittsburgh Steelers", primary: "#101820", secondary: "#FFB612" },
  SEA: { name: "Seattle Seahawks", primary: "#002244", secondary: "#69BE28" },
  SF: { name: "San Francisco 49ers", primary: "#AA0000", secondary: "#B3995D" },
  TB: { name: "Tampa Bay Buccaneers", primary: "#D50A0A", secondary: "#34302B" },
  TEN: { name: "Tennessee Titans", primary: "#0C2340", secondary: "#4B92DB" },
  WAS: { name: "Washington Commanders", primary: "#5A1414", secondary: "#FFB612" },
};

/** App/legacy abbreviations mapped to nflverse codes. */
const ALIASES: Record<string, string> = {
  LAR: "LA", STL: "LA", WSH: "WAS", JAC: "JAX", LVR: "LV", OAK: "LV", SD: "LAC", GNB: "GB",
  KAN: "KC", NWE: "NE", NOR: "NO", SFO: "SF", TAM: "TB",
};

const FALLBACK: TeamInfo = { name: "", primary: "#2a3a5c", secondary: "#8aa0c8" };

export function teamInfo(code: string | null | undefined): TeamInfo {
  if (!code) return FALLBACK;
  const c = code.toUpperCase();
  return TEAMS[c] ?? TEAMS[ALIASES[c] ?? ""] ?? { ...FALLBACK, name: code };
}

function luminance(hex: string): number {
  const m = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const v = parseInt(m.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Text colour that reads on the team's primary colour. */
export function teamInk(code: string | null | undefined): string {
  return luminance(teamInfo(code).primary) > 0.35 ? "#0b0f19" : "#ffffff";
}
