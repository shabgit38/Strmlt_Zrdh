import { ChevronDown } from "lucide-react";
import { useState } from "react";
import type { Holding, SectorGroup } from "../types";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";
import { BatchBreakdownPanel } from "./BatchBreakdownPanel";

type GroupedHoldingsProps = {
  sectors: SectorGroup[];
  selectedSector: string | null;
  selectedSymbol: string | null;
  onSelectHolding: (sector: string, holding: Holding) => void;
};

type SortKey =
  | "symbol"
  | "quantity"
  | "averagePrice"
  | "invested"
  | "weightPct"
  | "current"
  | "ltp"
  | "pnl"
  | "pnlPct"
  | "dayChangePct";

type SortDirection = "asc" | "desc";

const SORT_COLUMNS: Array<{ key: SortKey; label: string; align?: "right" }> = [
  { key: "symbol", label: "Symbol" },
  { key: "quantity", label: "Qty", align: "right" },
  { key: "averagePrice", label: "Avg", align: "right" },
  { key: "invested", label: "Invested", align: "right" },
  { key: "weightPct", label: "Weight", align: "right" },
  { key: "current", label: "Current", align: "right" },
  { key: "ltp", label: "LTP", align: "right" },
  { key: "pnl", label: "P&L", align: "right" },
  { key: "pnlPct", label: "P&L %", align: "right" },
  { key: "dayChangePct", label: "Day", align: "right" },
];

export function GroupedHoldings({
  sectors,
  selectedSector,
  selectedSymbol,
  onSelectHolding,
}: GroupedHoldingsProps) {
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  function handleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "symbol" ? "asc" : "desc");
  }

  return (
    <section className="space-y-3">
      {sectors.map((sector) => {
        const selectedHolding =
          sector.holdings.find((holding) => holding.symbol === selectedSymbol) ?? null;
        const sortedHoldings = sortHoldings(sector.holdings, sortKey, sortDirection);

        return (
          <details
            key={sector.sector}
            id={sectorAnchorId(sector.sector)}
            open
            className={`rounded-lg border bg-terminal-panel shadow-sm ${
              selectedSector === sector.sector ? "border-terminal-watch" : "border-terminal-line"
            }`}
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
                <span>Invested </span>
                <span className="font-bold text-terminal-ink">{formatMoney(sector.invested)}</span>
                <span> | Weight </span>
                <span className="font-bold text-terminal-ink">{formatPct(sector.weightPct)}</span>
                <span> | P&L </span>
                <span className={`font-bold ${signedClass(sector.pnl)}`}>{formatMoney(sector.pnl)}</span>
                <span> | P&L % </span>
                <span className={`font-bold ${signedClass(sector.pnlPct)}`}>
                  {formatPct(sector.pnlPct)}
                </span>
              </div>
            </summary>
            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,3fr)_minmax(18rem,0.9fr)]">
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
                              <span aria-hidden="true">{sortDirection === "asc" ? "↑" : "↓"}</span>
                            ) : null}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedHoldings.map((holding) => (
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

function sortHoldings(holdings: Holding[], sortKey: SortKey, direction: SortDirection): Holding[] {
  const multiplier = direction === "asc" ? 1 : -1;
  return [...holdings].sort((a, b) => {
    const left = a[sortKey];
    const right = b[sortKey];
    if (sortKey === "symbol") {
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

export function sectorAnchorId(sector: string): string {
  return `sector-${sector.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "unmapped"}`;
}
