const OPTION_SYMBOL_PATTERN = /^(.+?)(\d{2})([A-Z]{3})(\d+(?:\.\d+)?)(CE|PE)$/;
const MONTHS: Record<string, number> = {
  JAN: 0,
  FEB: 1,
  MAR: 2,
  APR: 3,
  MAY: 4,
  JUN: 5,
  JUL: 6,
  AUG: 7,
  SEP: 8,
  OCT: 9,
  NOV: 10,
  DEC: 11,
};

export type ParsedOption = {
  underlying: string;
  strike: number;
  type: "CE" | "PE";
  expiry: Date;
};

export function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase();
}

function lastTuesday(year: number, month: number): Date {
  const expiry = new Date(year, month + 1, 0);
  while (expiry.getDay() !== 2) {
    expiry.setDate(expiry.getDate() - 1);
  }
  return expiry;
}

export function parseOptionSymbol(symbol: string): ParsedOption | null {
  const match = normalizeSymbol(symbol).match(OPTION_SYMBOL_PATTERN);
  if (!match) return null;

  const month = MONTHS[match[3]];
  if (month === undefined) return null;

  return {
    underlying: match[1],
    strike: Number(match[4]),
    type: match[5] as "CE" | "PE",
    expiry: lastTuesday(2000 + Number(match[2]), month),
  };
}

export function optionBreakeven(symbol: string, avgPrice: number | null): number | null {
  const parsed = parseOptionSymbol(symbol);
  if (!parsed || avgPrice === null) return null;
  return parsed.type === "PE" ? parsed.strike - avgPrice : parsed.strike + avgPrice;
}

export function daysBetween(start: Date, end: Date): number {
  const startUtc = Date.UTC(start.getFullYear(), start.getMonth(), start.getDate());
  const endUtc = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
  return Math.round((endUtc - startUtc) / 86400000);
}

export function dateToInputValue(date: Date | null): string {
  if (!date) return "";
  return date.toISOString().slice(0, 10);
}

export function formatManualSpotDistance(breakeven: number | null, spot: number | null): string {
  if (breakeven === null || spot === null || spot === 0) return "";
  const distance = Math.abs(breakeven - spot);
  const distanceText = Number.isInteger(distance) ? distance.toFixed(0) : distance.toFixed(2);
  return `${distanceText} [${((distance / spot) * 100).toFixed(1)}%]`;
}
