import { Clock3 } from "lucide-react";
import type { SectorGroup } from "../types";
import { formatMoney, formatPct, signedClass } from "../format";

type SectorSummaryTableProps = {
  sectors: SectorGroup[];
  selectedSector: string | null;
  onSelectSector: (sector: string) => void;
  asOf: string;
};

export function SectorSummaryTable({
  sectors,
  selectedSector,
  onSelectSector,
  asOf,
}: SectorSummaryTableProps) {
  return (
    <section className="h-full rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold text-terminal-ink">Sector Summary</h2>
          <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted">
            <Clock3 className="h-3.5 w-3.5" />
            As of {new Date(asOf).toLocaleString()}
          </span>
        </div>
        <span className="text-xs text-terminal-muted">{sectors.length} sectors</span>
      </div>
      <div className="overflow-hidden rounded-md border border-terminal-line">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-3 py-2">Sector</th>
              <th className="px-3 py-2 text-right">Count</th>
              <th className="px-3 py-2 text-right">Invested</th>
              <th className="px-3 py-2 text-right">Weight</th>
              <th className="px-3 py-2 text-right">P&L</th>
              <th className="px-3 py-2 text-right">P&L %</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((sector) => (
              <tr
                key={sector.sector}
                className={`cursor-pointer border-t border-terminal-line transition hover:bg-terminal-hover ${
                  selectedSector === sector.sector ? "bg-terminal-selected" : "bg-terminal-panel"
                }`}
                onClick={() => onSelectSector(sector.sector)}
              >
                <td className="px-3 py-2 font-semibold text-terminal-ink">{sector.sector}</td>
                <td className="px-3 py-2 text-right tabular-nums">{sector.holdingsCount}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMoney(sector.invested)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatPct(sector.weightPct)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${signedClass(sector.pnl)}`}>
                  {formatMoney(sector.pnl)}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${signedClass(sector.pnlPct)}`}>
                  {formatPct(sector.pnlPct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
