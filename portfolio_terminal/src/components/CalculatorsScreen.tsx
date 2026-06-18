import { Calculator, Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";
import { setStreamlitComponentValue } from "../streamlitBridge";
import {
  calculateAvgRows,
  calculateOptionRows,
  calculateTradeRows,
  emptyAvgRow,
  emptyOptionRow,
  emptyTradeRow,
  seedAverageRowsFromTrade,
  summarizeAverage,
  summarizeTrades,
} from "../calculators/logic";
import { calculateTargetStrikes, type IndexSymbol } from "../calculators/optionSelector";
import { parseOptionSymbol } from "../calculators/optionMetrics";
import type {
  AvgCalculatorRow,
  CalculatorsLiveData,
  CalculatorsLiveRequest,
  IndexSpot,
  OptionCalculatorRow,
  TradeCalculatorRow,
} from "../calculators/types";

type OptionField = keyof OptionCalculatorRow;
type TradeField = keyof TradeCalculatorRow;
type AvgField = keyof AvgCalculatorRow;
const INDEX_CONFIG_KEYS: Record<string, true> = { NIFTY: true, BANKNIFTY: true, SENSEX: true };

export function CalculatorsScreen({ liveData }: { liveData?: CalculatorsLiveData | null }) {
  const [optionRows, setOptionRows] = useState<OptionCalculatorRow[]>(() => [emptyOptionRow()]);
  const [tradeRows, setTradeRows] = useState<TradeCalculatorRow[]>(() => [emptyTradeRow()]);
  const [avgRows, setAvgRows] = useState<AvgCalculatorRow[]>(() => [emptyAvgRow()]);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [avgEnabled, setAvgEnabled] = useState(false);
  const [spots, setSpots] = useState<IndexSpot[]>([]);
  const fetchedSymbolsRef = useRef(new Set<string>());
  const lastLiveRequestIdRef = useRef<string | null>(null);

  const calculatedOptionRows = useMemo(() => calculateOptionRows(optionRows), [optionRows]);
  const calculatedTradeRows = useMemo(() => calculateTradeRows(tradeRows), [tradeRows]);
  const tradeSummaryRows = useMemo(() => summarizeTrades(tradeRows), [tradeRows]);
  const calculatedAvgRows = useMemo(() => calculateAvgRows(avgRows), [avgRows]);
  const avgSummaryRows = useMemo(() => summarizeAverage(avgRows), [avgRows]);
  const targetStrikeRows = useMemo(() => {
    return spots.flatMap((spot) => {
      if (spot.spot === null || !(spot.symbol in INDEX_CONFIG_KEYS)) return [];
      return calculateTargetStrikes(spot.symbol as IndexSymbol, spot.spot).map((strike) => ({
        index: spot.symbol,
        spot: spot.spot,
        ...strike,
      }));
    });
  }, [spots]);

  useEffect(() => {
    if (!liveData) return;
    if (liveData.spots) {
      setSpots(liveData.spots);
    }
    if (liveData.options) {
      const incomingOptions = liveData.options;
      Object.keys(incomingOptions).forEach((symbol) => fetchedSymbolsRef.current.add(symbol));
      setOptionRows((rows) =>
        rows.map((row) => {
          const symbol = row.symbol.trim().toUpperCase();
          const quote = incomingOptions[symbol];
          if (!quote) return row;
          return {
            ...row,
            ltp: row.ltp || (quote.ltp === undefined ? "" : String(quote.ltp)),
            spot: row.spot || (quote.spot === undefined ? "" : String(quote.spot)),
            expiry: row.expiry || quote.expiry || "",
          };
        }),
      );
    }
  }, [liveData]);

  useEffect(() => {
    const symbols = optionRows
      .map((row) => row.symbol.trim().toUpperCase())
      .filter((symbol) => symbol && parseOptionSymbol(symbol) && !fetchedSymbolsRef.current.has(symbol));

    const uniqueSymbols = Array.from(new Set(symbols));
    const shouldFetchSpots = spots.length === 0;
    if (uniqueSymbols.length === 0 && !shouldFetchSpots) return;

    const requestId = `${Date.now()}-${uniqueSymbols.join(",") || "spots"}`;
    const timeout = window.setTimeout(() => {
      if (lastLiveRequestIdRef.current === requestId) return;
      lastLiveRequestIdRef.current = requestId;
      const request: CalculatorsLiveRequest = {
        type: "marketData",
        requestId,
        symbols: uniqueSymbols,
        includeSpots: shouldFetchSpots,
      };
      setStreamlitComponentValue(request);
    }, 750);

    return () => window.clearTimeout(timeout);
  }, [optionRows, spots.length]);

  function updateOptionRow(id: string, field: OptionField, value: string) {
    setOptionRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function updateTradeRow(id: string, field: TradeField, value: string) {
    setTradeRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function updateAvgRow(id: string, field: AvgField, value: string) {
    setAvgRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function removeOptionRow(id: string) {
    setOptionRows((rows) => rows.filter((row) => row.id !== id));
  }

  function removeTradeRow(id: string) {
    setTradeRows((rows) => rows.filter((row) => row.id !== id));
    if (selectedTradeId === id) {
      setSelectedTradeId(null);
    }
  }

  function removeAvgRow(id: string) {
    setAvgRows((rows) => rows.filter((row) => row.id !== id));
  }

  function handleTradeRowSelect(id: string) {
    setSelectedTradeId(id);
    if (avgEnabled) {
      setAvgRows(seedAverageRowsFromTrade(tradeRows, id));
    }
  }

  function handleAvgToggle(enabled: boolean) {
    setAvgEnabled(enabled);
    if (enabled) {
      const nextSelectedId = selectedTradeId ?? calculatedTradeRows.find((row) => row.symbol)?.id ?? null;
      setSelectedTradeId(nextSelectedId);
      setAvgRows(seedAverageRowsFromTrade(tradeRows, nextSelectedId));
    }
  }

  return (
    <main className="min-h-screen bg-terminal-bg">
      <div className="mx-auto max-w-[1680px] space-y-5 px-5 py-5">
        <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-terminal-muted">
          <Calculator className="h-4 w-4" />
          Calculators
        </div>

        {liveData?.error ? (
          <div className="rounded-md border border-terminal-avoid bg-terminal-panel p-3 text-sm font-semibold text-terminal-avoid">
            Live fetch failed: {liveData.error}
          </div>
        ) : null}

        <section className="grid gap-3 md:grid-cols-3">
          {displaySpots(spots).map((spot) => (
            <div key={spot.symbol} className="rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
              <div className="text-xs font-semibold uppercase tracking-wide text-terminal-muted">{spot.symbol}</div>
              <div className="mt-1 text-2xl font-bold tabular-nums text-terminal-ink">
                {spot.spot === null ? "-" : formatPrice(spot.spot)}
              </div>
              <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted">{spot.status}</div>
            </div>
          ))}
        </section>

        {targetStrikeRows.length > 0 ? (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">Target Strikes</h2>
            <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
              <table className="w-full border-collapse text-left text-sm">
                <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                  <tr>
                    <HeaderCell>Index</HeaderCell>
                    <HeaderCell align="right">Spot</HeaderCell>
                    <HeaderCell align="right">Dist %</HeaderCell>
                    <HeaderCell align="right">CE Strike</HeaderCell>
                    <HeaderCell align="right">PE Strike</HeaderCell>
                  </tr>
                </thead>
                <tbody>
                  {targetStrikeRows.map((row) => (
                    <tr key={`${row.index}-${row.distancePct}`} className="border-t border-terminal-line">
                      <ValueCell value={row.index} />
                      <ValueCell align="right" value={formatPrice(row.spot ?? 0)} />
                      <ValueCell align="right" value={`${row.distancePct.toFixed(0)}%`} />
                      <ValueCell align="right" value={formatPrice(row.ceStrike)} />
                      <ValueCell align="right" value={formatPrice(row.peStrike)} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        <section className="space-y-3">
          <SectionHeader title="Option Calculator" onAdd={() => setOptionRows((rows) => [...rows, emptyOptionRow()])} />
          <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
            <table className="w-full min-w-[1480px] border-collapse text-left text-sm">
              <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                <tr>
                  <HeaderCell>Symbol</HeaderCell>
                  <HeaderCell align="right">Open Qty</HeaderCell>
                  <HeaderCell align="right">Avg Price</HeaderCell>
                  <HeaderCell align="right">LTP</HeaderCell>
                  <HeaderCell align="right">Spot</HeaderCell>
                  <HeaderCell>Expiry</HeaderCell>
                  <HeaderCell align="right">Exit</HeaderCell>
                  <HeaderCell align="right">Days</HeaderCell>
                  <HeaderCell align="right">Breakeven</HeaderCell>
                  <HeaderCell align="right">Dist Spot</HeaderCell>
                  <HeaderCell align="right">Intrinsic</HeaderCell>
                  <HeaderCell align="right">Time Value</HeaderCell>
                  <HeaderCell>Moneyness</HeaderCell>
                  <HeaderCell>Alert</HeaderCell>
                  <HeaderCell align="right">Invested</HeaderCell>
                  <HeaderCell align="right">Current</HeaderCell>
                  <HeaderCell align="right">P&L</HeaderCell>
                  <HeaderCell align="right">P&L %</HeaderCell>
                  <HeaderCell align="right"></HeaderCell>
                </tr>
              </thead>
              <tbody>
                {calculatedOptionRows.map((row) => (
                  <tr key={row.id} className="border-t border-terminal-line">
                    <InputCell value={row.symbol} onChange={(value) => updateOptionRow(row.id, "symbol", value)} />
                    <InputCell align="right" value={row.openQty} onChange={(value) => updateOptionRow(row.id, "openQty", value)} />
                    <InputCell align="right" value={row.avgPrice} onChange={(value) => updateOptionRow(row.id, "avgPrice", value)} />
                    <InputCell align="right" value={row.ltp} onChange={(value) => updateOptionRow(row.id, "ltp", value)} />
                    <InputCell align="right" value={row.spot} onChange={(value) => updateOptionRow(row.id, "spot", value)} />
                    <InputCell type="date" value={row.expiry} onChange={(value) => updateOptionRow(row.id, "expiry", value)} />
                    <InputCell align="right" value={row.exitPrice} onChange={(value) => updateOptionRow(row.id, "exitPrice", value)} />
                    <ValueCell align="right" value={formatInteger(row.daysExpiry)} />
                    <ValueCell align="right" value={formatNullablePrice(row.breakeven)} />
                    <ValueCell align="right" value={row.distSpot || "-"} />
                    <ValueCell align="right" value={formatNullablePrice(row.intrinsic)} />
                    <ValueCell align="right" value={formatNullablePrice(row.timeValue)} />
                    <ValueCell value={row.moneyness || "-"} />
                    <td className={`px-3 py-2 text-sm font-semibold ${alertClass(row.alertTone)}`}>{row.alert}</td>
                    <ValueCell align="right" value={formatNullableMoney(row.invested)} />
                    <ValueCell align="right" value={formatNullableMoney(row.current)} />
                    <ValueCell align="right" value={formatNullableMoney(row.pnl)} tone={row.pnl} />
                    <ValueCell align="right" value={formatNullablePct(row.pnlPct)} tone={row.pnlPct} />
                    <ActionCell onRemove={() => removeOptionRow(row.id)} disabled={optionRows.length <= 1} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SectionHeader title="Trade Calculator" onAdd={() => setTradeRows((rows) => [...rows, emptyTradeRow()])} />
            <label className="inline-flex items-center gap-2 text-sm font-semibold text-terminal-ink">
              <input
                className="h-4 w-4 accent-terminal-watch"
                type="checkbox"
                checked={avgEnabled}
                onChange={(event) => handleAvgToggle(event.target.checked)}
              />
              avg calc
            </label>
          </div>
          <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
            <table className="w-full min-w-[1120px] border-collapse text-left text-sm">
              <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                <tr>
                  <HeaderCell></HeaderCell>
                  <HeaderCell>Symbol</HeaderCell>
                  <HeaderCell align="right">Buy</HeaderCell>
                  <HeaderCell align="right">Qty</HeaderCell>
                  <HeaderCell align="right">Invested</HeaderCell>
                  <HeaderCell align="right">Sell</HeaderCell>
                  <HeaderCell align="right">Profit</HeaderCell>
                  <HeaderCell align="right">Profit %</HeaderCell>
                  <HeaderCell>Entry</HeaderCell>
                  <HeaderCell>Exit</HeaderCell>
                  <HeaderCell align="right">Days</HeaderCell>
                  <HeaderCell align="right">SL</HeaderCell>
                  <HeaderCell align="right">TGT</HeaderCell>
                  <HeaderCell align="right"></HeaderCell>
                </tr>
              </thead>
              <tbody>
                {calculatedTradeRows.map((row) => (
                  <tr
                    key={row.id}
                    className={`border-t border-terminal-line ${selectedTradeId === row.id ? "bg-terminal-selected" : ""}`}
                    onClick={() => handleTradeRowSelect(row.id)}
                  >
                    <td className="px-3 py-2">
                      <input type="radio" checked={selectedTradeId === row.id} onChange={() => handleTradeRowSelect(row.id)} />
                    </td>
                    <InputCell value={row.symbol} onChange={(value) => updateTradeRow(row.id, "symbol", value)} />
                    <InputCell align="right" value={row.buy} onChange={(value) => updateTradeRow(row.id, "buy", value)} />
                    <InputCell align="right" value={row.qty} onChange={(value) => updateTradeRow(row.id, "qty", value)} />
                    <ValueCell align="right" value={formatNullableMoney(row.totalInvested)} />
                    <InputCell align="right" value={row.sell} onChange={(value) => updateTradeRow(row.id, "sell", value)} />
                    <ValueCell align="right" value={formatNullableMoney(row.profit)} tone={row.profit} />
                    <ValueCell align="right" value={formatNullablePct(row.profitPct)} tone={row.profitPct} />
                    <InputCell type="date" value={row.entry} onChange={(value) => updateTradeRow(row.id, "entry", value)} />
                    <InputCell type="date" value={row.exit} onChange={(value) => updateTradeRow(row.id, "exit", value)} />
                    <ValueCell align="right" value={formatInteger(row.days)} />
                    <InputCell align="right" value={row.sl} onChange={(value) => updateTradeRow(row.id, "sl", value)} />
                    <InputCell align="right" value={row.tgt} onChange={(value) => updateTradeRow(row.id, "tgt", value)} />
                    <ActionCell onRemove={() => removeTradeRow(row.id)} disabled={tradeRows.length <= 1} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {tradeSummaryRows.length > 0 ? (
            <SummaryTable
              headers={["Symbol", "Qty", "Avg Buy", "Invested", "Profit", "Profit %"]}
              rows={tradeSummaryRows.map((row) => [
                row.symbol,
                row.qty.toString(),
                formatPrice(row.avgBuy),
                formatMoney(row.totalInvested),
                formatNullableMoney(row.profit),
                formatNullablePct(row.profitPct),
              ])}
              toneColumns={[4, 5]}
              toneValues={tradeSummaryRows.map((row) => [row.profit, row.profitPct])}
            />
          ) : null}
        </section>

        {avgEnabled ? (
          <section className="space-y-3">
            <SectionHeader title="Average Calculator" onAdd={() => setAvgRows((rows) => [...rows, emptyAvgRow()])} />
            <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
              <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                  <tr>
                    <HeaderCell>Symbol</HeaderCell>
                    <HeaderCell align="right">Qty</HeaderCell>
                    <HeaderCell align="right">Avg Price</HeaderCell>
                    <HeaderCell align="right">LTP</HeaderCell>
                    <HeaderCell align="right">Invested</HeaderCell>
                    <HeaderCell align="right">Profit</HeaderCell>
                    <HeaderCell align="right"></HeaderCell>
                  </tr>
                </thead>
                <tbody>
                  {calculatedAvgRows.map((row) => (
                    <tr key={row.id} className="border-t border-terminal-line">
                      <InputCell value={row.symbol} onChange={(value) => updateAvgRow(row.id, "symbol", value)} />
                      <InputCell align="right" value={row.qty} onChange={(value) => updateAvgRow(row.id, "qty", value)} />
                      <InputCell align="right" value={row.avgPrice} onChange={(value) => updateAvgRow(row.id, "avgPrice", value)} />
                      <InputCell align="right" value={row.ltp} onChange={(value) => updateAvgRow(row.id, "ltp", value)} />
                      <ValueCell align="right" value={formatNullableMoney(row.invested)} />
                      <ValueCell align="right" value={formatNullableMoney(row.profit)} tone={row.profit} />
                      <ActionCell onRemove={() => removeAvgRow(row.id)} disabled={avgRows.length <= 1} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {avgSummaryRows.length > 0 ? (
              <SummaryTable
                headers={["Symbol", "Total Qty", "Total Avg", "Breakeven", "Invested", "Profit", "Profit %"]}
                rows={avgSummaryRows.map((row) => [
                  row.symbol,
                  row.totalQty.toString(),
                  formatNullablePrice(row.totalAveragePrice),
                  formatNullablePrice(row.breakeven),
                  formatMoney(row.totalInvested),
                  formatNullableMoney(row.profit),
                  formatNullablePct(row.profitPct),
                ])}
                toneColumns={[5, 6]}
                toneValues={avgSummaryRows.map((row) => [row.profit, row.profitPct])}
              />
            ) : null}
          </section>
        ) : null}
      </div>
    </main>
  );
}

function SectionHeader({ title, onAdd }: { title: string; onAdd: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">{title}</h2>
      <button
        className="inline-flex items-center gap-1 rounded-md border border-terminal-line px-3 py-2 text-sm font-semibold text-terminal-ink hover:bg-terminal-hover"
        type="button"
        onClick={onAdd}
      >
        <Plus className="h-4 w-4" />
        Add
      </button>
    </div>
  );
}

function HeaderCell({ children = null, align = "left" }: { children?: ReactNode; align?: "left" | "right" }) {
  return <th className={`px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>{children}</th>;
}

function InputCell({
  value,
  onChange,
  align = "left",
  type = "text",
}: {
  value: string;
  onChange: (value: string) => void;
  align?: "left" | "right";
  type?: "text" | "date";
}) {
  return (
    <td className="px-2 py-1">
      <input
        className={`w-full rounded-md border border-terminal-line bg-terminal-panel-alt px-2 py-1 text-sm text-terminal-ink outline-none focus:border-terminal-watch ${
          align === "right" ? "text-right tabular-nums" : ""
        }`}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onClick={(event) => event.stopPropagation()}
      />
    </td>
  );
}

function ValueCell({
  value,
  align = "left",
  tone,
}: {
  value: string;
  align?: "left" | "right";
  tone?: number | null;
}) {
  return (
    <td
      className={`px-3 py-2 tabular-nums ${align === "right" ? "text-right" : "text-left"} ${
        tone === undefined || tone === null ? "text-terminal-ink" : signedClass(tone)
      }`}
    >
      {value}
    </td>
  );
}

function ActionCell({ onRemove, disabled }: { onRemove: () => void; disabled: boolean }) {
  return (
    <td className="px-3 py-2 text-right">
      <button
        className="inline-flex rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover hover:text-terminal-avoid disabled:cursor-default disabled:opacity-40"
        type="button"
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          onRemove();
        }}
        title="Remove"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </td>
  );
}

function SummaryTable({
  headers,
  rows,
  toneColumns,
  toneValues,
}: {
  headers: string[];
  rows: string[][];
  toneColumns: number[];
  toneValues: Array<Array<number | null>>;
}) {
  return (
    <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
          <tr>
            {headers.map((header, index) => (
              <HeaderCell key={header} align={index === 0 ? "left" : "right"}>
                {header}
              </HeaderCell>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${row[0]}-${rowIndex}`} className="border-t border-terminal-line">
              {row.map((value, columnIndex) => {
                const toneIndex = toneColumns.indexOf(columnIndex);
                return (
                  <ValueCell
                    key={`${row[0]}-${columnIndex}`}
                    align={columnIndex === 0 ? "left" : "right"}
                    value={value}
                    tone={toneIndex >= 0 ? toneValues[rowIndex][toneIndex] : undefined}
                  />
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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

function formatInteger(value: number | null): string {
  return value === null ? "-" : value.toString();
}

function displaySpots(spots: IndexSpot[]): IndexSpot[] {
  const bySymbol = new Map(spots.map((spot) => [spot.symbol, spot]));
  return ["NIFTY", "BANKNIFTY", "SENSEX"].map(
    (symbol) => bySymbol.get(symbol) ?? { symbol, spot: null, status: "Missing" },
  );
}

function alertClass(tone: "normal" | "review" | "warning" | "exit" | "hardExit"): string {
  if (tone === "hardExit") return "text-red-900";
  if (tone === "exit") return "text-terminal-avoid";
  if (tone === "warning") return "text-orange-600";
  if (tone === "review") return "text-terminal-near";
  return "text-terminal-muted";
}
