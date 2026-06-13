import { ChevronDown } from "lucide-react";
import type { Holding, SectorGroup } from "../types";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";
import { BatchBreakdownPanel } from "./BatchBreakdownPanel";

type GroupedHoldingsProps = {
  sectors: SectorGroup[];
  selectedSymbol: string | null;
  onSelectHolding: (sector: string, holding: Holding) => void;
};

export function GroupedHoldings({
  sectors,
  selectedSymbol,
  onSelectHolding,
}: GroupedHoldingsProps) {
  return (
    <section className="space-y-3">
      {sectors.map((sector) => {
        const selectedHolding =
          sector.holdings.find((holding) => holding.symbol === selectedSymbol) ?? null;

        return (
          <details
            key={sector.sector}
            open
            className="rounded-lg border border-terminal-line bg-terminal-panel shadow-sm"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 border-b border-terminal-line px-4 py-3">
              <div className="flex items-center gap-2">
                <ChevronDown className="h-4 w-4 text-terminal-muted" />
                <span className="font-semibold text-terminal-ink">{sector.sector}</span>
                <span className="rounded-full bg-terminal-panel-alt px-2 py-0.5 text-xs font-semibold text-terminal-muted">
                  {sector.holdingsCount}
                </span>
              </div>
              <div className="text-sm text-terminal-muted">
                Invested {formatMoney(sector.invested)} | Weight {formatPct(sector.weightPct)}
              </div>
            </summary>
            <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,3fr)_minmax(22rem,1fr)]">
              <div className="overflow-hidden rounded-md border border-terminal-line">
                <table className="w-full border-collapse text-left text-sm">
                  <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                    <tr>
                      <th className="px-3 py-2">Symbol</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2 text-right">Avg</th>
                      <th className="px-3 py-2 text-right">Invested</th>
                      <th className="px-3 py-2 text-right">Weight</th>
                      <th className="px-3 py-2 text-right">Current</th>
                      <th className="px-3 py-2 text-right">LTP</th>
                      <th className="px-3 py-2 text-right">P&L</th>
                      <th className="px-3 py-2 text-right">P&L %</th>
                      <th className="px-3 py-2 text-right">Day</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sector.holdings.map((holding) => (
                      <tr
                        key={holding.symbol}
                        onClick={() => onSelectHolding(sector.sector, holding)}
                        className={`cursor-pointer border-t border-terminal-line transition hover:bg-terminal-hover ${
                          selectedSymbol === holding.symbol ? "bg-terminal-selected" : "bg-terminal-panel"
                        }`}
                      >
                        <td className="px-3 py-2 font-bold text-terminal-ink">{holding.symbol}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{holding.quantity}</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {formatPrice(holding.averagePrice)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {formatMoney(holding.invested)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {formatPct(holding.weightPct)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {formatMoney(holding.current)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{formatPrice(holding.ltp)}</td>
                        <td className={`px-3 py-2 text-right tabular-nums ${signedClass(holding.pnl)}`}>
                          {formatMoney(holding.pnl)}
                        </td>
                        <td className={`px-3 py-2 text-right tabular-nums ${signedClass(holding.pnlPct)}`}>
                          {formatPct(holding.pnlPct)}
                        </td>
                        <td
                          className={`px-3 py-2 text-right tabular-nums ${signedClass(
                            holding.dayChangePct
                          )}`}
                        >
                          {formatPct(holding.dayChangePct)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <BatchBreakdownPanel holding={selectedHolding} />
            </div>
          </details>
        );
      })}
    </section>
  );
}
