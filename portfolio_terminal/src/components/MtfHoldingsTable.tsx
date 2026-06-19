import type { MtfHolding } from "../types";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";

type MtfHoldingsTableProps = {
  holdings: MtfHolding[];
};

export function MtfHoldingsTable({ holdings }: MtfHoldingsTableProps) {
  if (holdings.length === 0) {
    return null;
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3 text-sm font-semibold uppercase tracking-wide text-terminal-muted">
        <span>MTF Holdings</span>
        <span className="text-xs">{holdings.length} symbols</span>
      </div>
      <div className="overflow-hidden rounded-lg border border-terminal-line bg-terminal-panel shadow-sm">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2 text-right">MTF Qty</th>
              <th className="px-3 py-2 text-right">MTF Avg</th>
              <th className="px-3 py-2 text-right">MTF Value</th>
              <th className="px-3 py-2 text-right">Initial Margin</th>
              <th className="px-3 py-2 text-right">LTP</th>
              <th className="px-3 py-2 text-right">P&L</th>
              <th className="px-3 py-2 text-right">Day</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding) => (
              <tr key={holding.symbol} className="border-t border-terminal-line">
                <td className="px-3 py-2 font-bold text-terminal-ink">{holding.symbol}</td>
                <td className="px-3 py-2 text-right tabular-nums">{holding.mtfQty}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatPrice(holding.mtfAvgPrice)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMoney(holding.mtfValue)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMoney(holding.initialMargin)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatPrice(holding.ltp)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${signedClass(holding.pnl)}`}>
                  {formatMoney(holding.pnl)}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${signedClass(holding.dayChangePct)}`}>
                  {formatPct(holding.dayChangePct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
