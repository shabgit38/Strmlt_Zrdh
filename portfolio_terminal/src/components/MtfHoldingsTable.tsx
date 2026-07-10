import type { MtfHolding } from "../types";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";
import { useState } from "react";

type MtfHoldingsTableProps = {
  holdings: MtfHolding[];
};

export function MtfHoldingsTable({ holdings }: MtfHoldingsTableProps) {
  const [dailyInterestPct, setDailyInterestPct] = useState("0.04");

  if (holdings.length === 0) {
    return null;
  }

  const dailyInterestRate = parseNumber(dailyInterestPct) / 100;

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm font-semibold uppercase tracking-wide text-terminal-muted">
        <span>MTF Holdings</span>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-terminal-muted">
            Daily Interest %
            <input
              className="w-20 rounded-md border border-terminal-line bg-terminal-panel-alt px-2 py-1 text-right text-sm text-terminal-ink outline-none focus:border-terminal-watch"
              value={dailyInterestPct}
              onChange={(event) => setDailyInterestPct(event.target.value)}
            />
          </label>
          <span className="text-xs">{holdings.length} symbols</span>
        </div>
      </div>
      <div className="overflow-auto rounded-lg border border-terminal-line bg-terminal-panel shadow-sm">
        <table className="w-full min-w-[1320px] border-collapse text-left text-xs">
          <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">Symbol</th>
              <th className="px-2 py-2 text-right">MTF Qty</th>
              <th className="px-2 py-2 text-right">MTF Avg</th>
              <th className="px-2 py-2 text-right">MTF Value</th>
              <th className="px-2 py-2 text-right">LTP</th>
              <th className="px-2 py-2 text-right">Day Chng%</th>
              <th className="px-2 py-2 text-right">P&L</th>
              <th className="px-2 py-2 text-right" title="P&L - Interest - Charges">Net P&L</th>
              <th className="px-2 py-2 text-right">Breakeven</th>
              <th className="px-2 py-2 text-right">Days</th>
              <th className="px-2 py-2 text-right" title="Funded amount x Daily Interest %">Int/Day</th>
              <th className="px-2 py-2 text-right" title="Funded amount x Daily Interest % x Days">Interest</th>
              <th className="px-2 py-2 text-right" title="min(MTF Value x 0.3%, Rs 20) + Rs 15 pledge charge + 18% GST">Charges</th>
              <th className="px-2 py-2 text-right">Initial Margin</th>
              <th className="px-2 py-2 text-right">Funded</th>
              <th className="px-2 py-2 text-right">Margin %</th>
              <th className="px-2 py-2 text-right">Buy Date</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding) => {
              const metrics = mtfInterestMetrics(holding, dailyInterestRate);
              return (
                <tr key={holding.symbol} className="border-t border-terminal-line">
                  <td className="px-2 py-2 font-bold text-terminal-ink">{holding.symbol}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{holding.mtfQty}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatPrice(holding.mtfAvgPrice)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatMoney(holding.mtfValue)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatPrice(holding.ltp)}</td>
                  <td className={`px-2 py-2 text-right tabular-nums ${signedClass(holding.dayChangePct)}`}>
                    {formatPct(holding.dayChangePct)}
                  </td>
                  <td className={`px-2 py-2 text-right tabular-nums ${signedClass(holding.pnl)}`}>
                    {formatMoney(holding.pnl)}
                  </td>
                  <td className={`px-2 py-2 text-right tabular-nums ${signedClass(metrics.netPnl ?? 0)}`}>
                    {formatNullableMoney(metrics.netPnl)}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-terminal-near">{formatNullablePrice(metrics.breakeven)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{holding.holdingDays ?? "-"}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-terminal-near">{formatMoney(metrics.interestPerDay)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-terminal-near">{formatNullableMoney(metrics.interestSoFar)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatMoney(metrics.charges)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatMoney(holding.initialMargin)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatMoney(metrics.fundedAmount)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{formatNullablePct(metrics.marginPct)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{holding.buyDate || "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function mtfInterestMetrics(holding: MtfHolding, dailyInterestRate: number) {
  const fundedAmount = Math.max(holding.mtfValue - holding.initialMargin, 0);
  const marginPct = holding.mtfValue === 0 ? null : (holding.initialMargin / holding.mtfValue) * 100;
  const interestPerDay = fundedAmount * dailyInterestRate;
  const holdingDays = typeof holding.holdingDays === "number" && Number.isFinite(holding.holdingDays) ? holding.holdingDays : null;
  const interestSoFar = holdingDays === null ? null : interestPerDay * holdingDays;
  const charges = estimatedCurrentCharges(holding.mtfValue);
  const netPnl = interestSoFar === null ? null : holding.pnl - interestSoFar - charges;
  const breakeven = interestSoFar === null || holding.mtfQty === 0 ? null : holding.mtfAvgPrice + interestSoFar / holding.mtfQty;

  return {
    fundedAmount,
    marginPct,
    interestPerDay,
    interestSoFar,
    charges,
    netPnl,
    breakeven,
  };
}

function estimatedCurrentCharges(buyValue: number): number {
  const buyBrokerage = Math.min(buyValue * 0.003, 20);
  const pledgeCharge = 15 * 1.18;
  return buyBrokerage + pledgeCharge;
}

function parseNumber(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatNullableMoney(value: number | null): string {
  return value === null ? "-" : formatMoney(value);
}

function formatNullablePrice(value: number | null): string {
  return value === null ? "-" : formatPrice(value);
}

function formatNullablePct(value: number | null): string {
  return value === null ? "-" : formatPct(value);
}
