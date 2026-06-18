import type {
  AvgCalculatorRow,
  AvgSummaryRow,
  CalculatedAvgRow,
  CalculatedOptionRow,
  CalculatedTradeRow,
  OptionCalculatorRow,
  TradeCalculatorRow,
  TradeSummaryRow,
} from "./types";
import {
  dateToInputValue,
  daysBetween,
  formatManualSpotDistance,
  normalizeSymbol,
  optionBreakeven,
  parseOptionSymbol,
} from "./optionMetrics";
import { generateExitAlert } from "./alertEngine";

export function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function emptyOptionRow(): OptionCalculatorRow {
  return {
    id: newId("option"),
    symbol: "",
    openQty: "",
    avgPrice: "",
    ltp: "",
    spot: "",
    expiry: "",
    strike: "",
    optionType: "",
    exitPrice: "",
  };
}

export function emptyTradeRow(): TradeCalculatorRow {
  return { id: newId("trade"), symbol: "", buy: "", qty: "", sell: "", entry: "", exit: "", sl: "", tgt: "" };
}

export function emptyAvgRow(): AvgCalculatorRow {
  return { id: newId("avg"), symbol: "", qty: "", avgPrice: "", ltp: "" };
}

export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const next = Number(value);
  return Number.isFinite(next) ? next : null;
}

export function calculateOptionRows(rows: OptionCalculatorRow[]): CalculatedOptionRow[] {
  const today = new Date();
  return rows.map((row) => {
    const symbol = normalizeSymbol(row.symbol);
    const openQty = toNumber(row.openQty);
    const avgPrice = toNumber(row.avgPrice);
    const ltp = toNumber(row.ltp);
    const spot = toNumber(row.spot);
    const exitPrice = toNumber(row.exitPrice);
    const strike = toNumber(row.strike);
    const optionType = row.optionType === "CE" || row.optionType === "PE" ? row.optionType : null;
    const parsed = parseOptionSymbol(symbol);
    const expiry = row.expiry ? new Date(row.expiry) : parsed?.expiry;
    const effectiveAvgPrice = avgPrice ?? ltp;
    const valuationPrice = exitPrice ?? ltp;
    const invested = openQty !== null && ltp !== null ? Math.abs(openQty) * ltp : null;
    const current = openQty !== null && valuationPrice !== null ? openQty * valuationPrice : null;
    const pnl =
      openQty !== null && valuationPrice !== null && effectiveAvgPrice !== null
        ? openQty * (valuationPrice - effectiveAvgPrice)
        : null;
    const breakeven =
      effectiveAvgPrice !== null && strike !== null && optionType !== null
        ? optionType === "PE"
          ? strike - effectiveAvgPrice
          : strike + effectiveAvgPrice
        : optionBreakeven(symbol, effectiveAvgPrice);
    const isOtm =
      spot !== null &&
      ((optionType !== null && strike !== null && ((optionType === "CE" && spot < strike) || (optionType === "PE" && spot > strike))) ||
        (parsed !== null && ((parsed.type === "CE" && spot < parsed.strike) || (parsed.type === "PE" && spot > parsed.strike))));
    const daysExpiry = expiry && Number.isFinite(expiry.getTime()) ? daysBetween(today, expiry) : null;
    const alert = generateExitAlert({
      entryPrice: effectiveAvgPrice,
      currentLtp: valuationPrice,
      dte: daysExpiry,
      isOtm,
    });

    return {
      ...row,
      symbol,
      avgPrice: row.avgPrice || (ltp === null ? "" : String(ltp)),
      optionType: row.optionType || parsed?.type || "",
      strike: row.strike || (parsed?.strike === undefined ? "" : String(parsed.strike)),
      expiry: row.expiry || dateToInputValue(parsed?.expiry ?? null),
      daysExpiry,
      breakeven,
      distSpot: formatManualSpotDistance(breakeven, spot),
      alert: alert.label,
      alertTone: alert.tone,
      invested,
      current,
      pnl,
      pnlPct: pnl !== null && invested !== null && invested !== 0 ? (pnl / invested) * 100 : null,
    };
  });
}

export function calculateTradeRows(rows: TradeCalculatorRow[]): CalculatedTradeRow[] {
  const today = new Date();
  return rows.map((row) => {
    const symbol = normalizeSymbol(row.symbol);
    const buy = toNumber(row.buy);
    const qty = toNumber(row.qty);
    const sell = toNumber(row.sell);
    const totalInvested = buy !== null && qty !== null ? buy * qty : null;
    const profit = sell !== null && buy !== null && qty !== null ? (sell - buy) * qty : null;
    const entryDate = row.entry ? new Date(row.entry) : null;
    const exitDate = row.exit ? new Date(row.exit) : today;

    return {
      ...row,
      symbol,
      totalInvested,
      profit,
      profitPct: profit !== null && totalInvested !== null && totalInvested !== 0 ? (profit / totalInvested) * 100 : null,
      days: entryDate && Number.isFinite(entryDate.getTime()) ? daysBetween(entryDate, exitDate) : null,
    };
  });
}

export function summarizeTrades(rows: TradeCalculatorRow[]): TradeSummaryRow[] {
  const grouped = new Map<string, { qty: number; invested: number; profit: number; hasProfit: boolean }>();
  for (const row of calculateTradeRows(rows)) {
    if (!row.symbol) continue;
    const qty = toNumber(row.qty) ?? 0;
    const invested = row.totalInvested ?? 0;
    const existing = grouped.get(row.symbol) ?? { qty: 0, invested: 0, profit: 0, hasProfit: false };
    existing.qty += qty;
    existing.invested += invested;
    if (row.profit !== null) {
      existing.profit += row.profit;
      existing.hasProfit = true;
    }
    grouped.set(row.symbol, existing);
  }

  return Array.from(grouped, ([symbol, value]) => ({
    symbol,
    qty: value.qty,
    avgBuy: value.qty !== 0 ? value.invested / value.qty : 0,
    totalInvested: value.invested,
    profit: value.hasProfit ? value.profit : null,
    profitPct: value.hasProfit && value.invested !== 0 ? (value.profit / value.invested) * 100 : null,
  }));
}

export function calculateAvgRows(rows: AvgCalculatorRow[]): CalculatedAvgRow[] {
  return rows.map((row) => {
    const qty = toNumber(row.qty);
    const avgPrice = toNumber(row.avgPrice);
    const ltp = toNumber(row.ltp);
    return {
      ...row,
      symbol: normalizeSymbol(row.symbol),
      invested: qty !== null && avgPrice !== null ? Math.abs(qty) * avgPrice : null,
      profit: qty !== null && avgPrice !== null && ltp !== null ? qty * (ltp - avgPrice) : null,
    };
  });
}

export function summarizeAverage(rows: AvgCalculatorRow[]): AvgSummaryRow[] {
  const grouped = new Map<string, { qty: number; invested: number; weightedCost: number; profit: number; hasProfit: boolean }>();
  for (const row of calculateAvgRows(rows)) {
    if (!row.symbol) continue;
    const qty = toNumber(row.qty) ?? 0;
    const avgPrice = toNumber(row.avgPrice);
    const existing = grouped.get(row.symbol) ?? { qty: 0, invested: 0, weightedCost: 0, profit: 0, hasProfit: false };
    existing.qty += qty;
    if (avgPrice !== null) {
      existing.invested += Math.abs(qty) * avgPrice;
      existing.weightedCost += qty * avgPrice;
    }
    if (row.profit !== null) {
      existing.profit += row.profit;
      existing.hasProfit = true;
    }
    grouped.set(row.symbol, existing);
  }

  return Array.from(grouped, ([symbol, value]) => {
    const totalAveragePrice = value.qty !== 0 ? value.weightedCost / value.qty : null;
    const profit = value.hasProfit ? value.profit : null;
    return {
      symbol,
      totalQty: value.qty,
      totalAveragePrice,
      breakeven: parseOptionSymbol(symbol) ? optionBreakeven(symbol, totalAveragePrice) : totalAveragePrice,
      totalInvested: value.invested,
      profit,
      profitPct: profit !== null && value.invested !== 0 ? (profit / value.invested) * 100 : null,
    };
  });
}

export function seedAverageRowsFromTrade(rows: TradeCalculatorRow[], selectedId: string | null): AvgCalculatorRow[] {
  if (!selectedId) return [emptyAvgRow()];
  const calculatedRows = calculateTradeRows(rows);
  const selected = calculatedRows.find((row) => row.id === selectedId);
  if (!selected?.symbol) return [emptyAvgRow()];

  const seededRows = calculatedRows
    .filter((row) => row.symbol === selected.symbol && toNumber(row.qty) !== null && toNumber(row.buy) !== null)
    .map((row) => ({
      id: newId("avg"),
      symbol: row.symbol,
      qty: row.qty,
      avgPrice: row.buy,
      ltp: row.sell,
    }));

  return seededRows.length > 0 ? seededRows : [{ ...emptyAvgRow(), symbol: selected.symbol }];
}
