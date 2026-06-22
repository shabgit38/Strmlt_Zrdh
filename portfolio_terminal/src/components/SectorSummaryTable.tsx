import { Clock3 } from "lucide-react";
import { useState } from "react";
import type { SectorGroup } from "../types";
import { formatMoney, formatPct, signedClass } from "../format";

type SectorSummaryTableProps = {
  sectors: SectorGroup[];
  selectedSector: string | null;
  onSelectSector: (sector: string) => void;
  asOf: string;
};

type SortKey = "sector" | "holdingsCount" | "invested" | "weightPct" | "pnl" | "pnlPct";
type SortDirection = "asc" | "desc";

const SORT_COLUMNS: Array<{ key: SortKey; label: string; align?: "right" }> = [
  { key: "sector", label: "Sector" },
  { key: "holdingsCount", label: "Count", align: "right" },
  { key: "invested", label: "Invested", align: "right" },
  { key: "weightPct", label: "Weight", align: "right" },
  { key: "pnl", label: "P&L", align: "right" },
  { key: "pnlPct", label: "P&L %", align: "right" },
];

export function SectorSummaryTable({
  sectors,
  selectedSector,
  onSelectSector,
  asOf,
}: SectorSummaryTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("invested");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const sortedSectors = sortSectors(sectors, sortKey, sortDirection);

  function handleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "sector" ? "asc" : "desc");
  }

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
              {SORT_COLUMNS.map((column) => (
                <th
                  key={column.key}
                  className={`px-3 py-2 ${column.align === "right" ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => handleSort(column.key)}
                    className={`inline-flex w-full items-center gap-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted hover:text-terminal-ink ${
                      column.align === "right" ? "justify-end" : "justify-start"
                    }`}
                  >
                    <span>{column.label}</span>
                    {sortKey === column.key ? (
                      <span aria-hidden="true">{sortDirection === "asc" ? "^" : "v"}</span>
                    ) : null}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedSectors.map((sector) => (
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

function sortSectors(
  sectors: SectorGroup[],
  sortKey: SortKey,
  direction: SortDirection,
): SectorGroup[] {
  const multiplier = direction === "asc" ? 1 : -1;
  return [...sectors].sort((a, b) => {
    const left = a[sortKey];
    const right = b[sortKey];
    if (sortKey === "sector") {
      return String(left).localeCompare(String(right)) * multiplier;
    }
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    if (Number.isNaN(leftNumber) && Number.isNaN(rightNumber)) return 0;
    if (Number.isNaN(leftNumber)) return 1;
    if (Number.isNaN(rightNumber)) return -1;
    return (leftNumber - rightNumber) * multiplier;
  });
}
