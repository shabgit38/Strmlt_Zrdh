import { Calculator, Plus, Trash2 } from "lucide-react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
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
import { generateExitAlert } from "../calculators/alertEngine";
import { formatManualSpotDistance } from "../calculators/optionMetrics";
import type {
  AvgCalculatorRow,
  CalculatorsLiveData,
  CalculatorsLiveRequest,
  ExistingOptionPosition,
  IndexSpot,
  OptionContract,
  OptionCalculatorRow,
  TargetOptionContracts,
  TradeCalculatorRow,
} from "../calculators/types";

type OptionField = keyof OptionCalculatorRow;
type TradeField = keyof TradeCalculatorRow;
type AvgField = keyof AvgCalculatorRow;

export function CalculatorsScreen({ liveData }: { liveData?: CalculatorsLiveData | null }) {
  const [optionRows, setOptionRows] = useState<OptionCalculatorRow[]>(() => [emptyOptionRow()]);
  const [tradeRows, setTradeRows] = useState<TradeCalculatorRow[]>(() => [emptyTradeRow()]);
  const [avgRows, setAvgRows] = useState<AvgCalculatorRow[]>(() => [emptyAvgRow()]);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [avgEnabled, setAvgEnabled] = useState(false);
  const [spots, setSpots] = useState<IndexSpot[]>([]);
  const [targetOptions, setTargetOptions] = useState<Record<string, TargetOptionContracts[]>>({});
  const [existingPositions, setExistingPositions] = useState<ExistingOptionPosition[]>([]);
  const [checkedGeneratedSymbols, setCheckedGeneratedSymbols] = useState<Set<string>>(() => new Set());
  const [selectedCardContracts, setSelectedCardContracts] = useState<Record<string, string>>({});
  const generatedRowIdsRef = useRef(new Map<string, string>());
  const fetchedSymbolsRef = useRef(new Set<string>());
  const lastLiveRequestIdRef = useRef<string | null>(null);

  const calculatedOptionRows = useMemo(() => calculateOptionRows(optionRows), [optionRows]);
  const calculatedTradeRows = useMemo(() => calculateTradeRows(tradeRows), [tradeRows]);
  const tradeSummaryRows = useMemo(() => summarizeTrades(tradeRows), [tradeRows]);
  const calculatedAvgRows = useMemo(() => calculateAvgRows(avgRows), [avgRows]);
  const avgSummaryRows = useMemo(() => summarizeAverage(avgRows), [avgRows]);

  useEffect(() => {
    if (!liveData) return;
    if (liveData.spots) {
      setSpots(liveData.spots);
    }
    if (liveData.targetOptions) {
      setTargetOptions(liveData.targetOptions);
    }
    if (liveData.positions) {
      setExistingPositions(liveData.positions);
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
            avgPrice: row.avgPrice || (quote.ltp === undefined ? "" : String(quote.ltp)),
            spot: row.spot || (quote.spot === undefined ? "" : String(quote.spot)),
            expiry: row.expiry || quote.expiry || "",
            strike: row.strike || (quote.strike === undefined ? "" : String(quote.strike)),
            optionType: row.optionType || quote.optionType || "",
            openQty: row.openQty || (quote.lotSize === undefined ? "" : String(quote.lotSize)),
          };
        }),
      );
    }
  }, [liveData]);

  useEffect(() => {
    const symbols = optionRows
      .map((row) => row.symbol.trim().toUpperCase())
      .filter((symbol) => symbol && !fetchedSymbolsRef.current.has(symbol));

    const uniqueSymbols = Array.from(new Set(symbols));
    const shouldFetchSpots = displaySpots(spots).some((spot) => spot.spot === null || spot.status !== "Live");
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
  }, [optionRows, spots]);

  function updateOptionRow(id: string, field: OptionField, value: string) {
    setOptionRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function addOptionRow(row: OptionCalculatorRow, generatedSymbol?: string) {
    setOptionRows((rows) => {
      if (rows.some((existingRow) => existingRow.symbol.trim().toUpperCase() === row.symbol.trim().toUpperCase())) return rows;
      const blankRowIndex = rows.findIndex(isBlankOptionRow);
      if (blankRowIndex >= 0) {
        if (generatedSymbol) {
          generatedRowIdsRef.current.set(generatedSymbol, rows[blankRowIndex].id);
        }
        return rows.map((existingRow, index) => (index === blankRowIndex ? { ...row, id: existingRow.id } : existingRow));
      }
      if (generatedSymbol) {
        generatedRowIdsRef.current.set(generatedSymbol, row.id);
      }
      return [...rows, row];
    });
  }

  function handleGeneratedOptionToggle({
    checked,
    spot,
    contract,
  }: {
    checked: boolean;
    spot: number;
    contract: OptionContract;
  }) {
    const symbol = contract.symbol;
    setCheckedGeneratedSymbols((previous) => {
      const next = new Set(previous);
      if (checked) next.add(symbol);
      else next.delete(symbol);
      return next;
    });

    if (checked) {
      addOptionRow(
        {
          ...emptyOptionRow(),
          symbol,
          openQty: String(contract.lotSize),
          spot: String(spot),
          expiry: contract.expiry,
          strike: String(contract.strike),
          optionType: contract.optionType,
        },
        symbol,
      );
      return;
    }

    const generatedRowId = generatedRowIdsRef.current.get(symbol);
    generatedRowIdsRef.current.delete(symbol);
    setOptionRows((rows) =>
      generatedRowId ? rows.filter((row) => row.id !== generatedRowId) : rows,
    );
  }

  function addExistingPosition(position: ExistingOptionPosition) {
    addOptionRow({
      ...emptyOptionRow(),
      symbol: position.symbol,
      openQty: String(position.quantity),
      avgPrice: position.averagePrice ? String(position.averagePrice) : "",
      ltp: position.lastPrice ? String(position.lastPrice) : "",
      spot: position.spot === undefined ? "" : String(position.spot),
      expiry: position.expiry ?? "",
      strike: position.strike === undefined ? "" : String(position.strike),
      optionType: position.optionType ?? "",
    });
  }

  function updateTradeRow(id: string, field: TradeField, value: string) {
    setTradeRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function updateAvgRow(id: string, field: AvgField, value: string) {
    setAvgRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function removeOptionRow(id: string) {
    setOptionRows((rows) => {
      const removedRow = rows.find((row) => row.id === id);
      const removedSymbol = removedRow?.symbol.trim().toUpperCase();
      if (removedSymbol) {
        setCheckedGeneratedSymbols((previous) => {
          if (!previous.has(removedSymbol)) return previous;
          const next = new Set(previous);
          next.delete(removedSymbol);
          return next;
        });
        generatedRowIdsRef.current.delete(removedSymbol);
      }
      return rows.filter((row) => row.id !== id);
    });
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
            <IndexSpotCard
              key={spot.symbol}
              checkedSymbols={checkedGeneratedSymbols}
              onToggle={handleGeneratedOptionToggle}
              selectedContracts={selectedCardContracts}
              setSelectedContracts={setSelectedCardContracts}
              spot={spot}
              targetOptions={targetOptions[spot.symbol] ?? []}
            />
          ))}
        </section>

        {existingPositions.length > 0 ? (
          <ExistingPositionsSection
            existingSymbols={new Set(optionRows.map((row) => row.symbol.trim().toUpperCase()).filter(Boolean))}
            onAdd={addExistingPosition}
            positions={existingPositions}
          />
        ) : null}

        <section className="space-y-3">
          <SectionHeader title="Option Calculator" onAdd={() => setOptionRows((rows) => [...rows, emptyOptionRow()])} />
          <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
            <table className="w-full min-w-[1160px] border-collapse text-left text-sm">
              <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                <tr>
                  <HeaderCell>Symbol</HeaderCell>
                  <HeaderCell align="right">Open Qty</HeaderCell>
                  <HeaderCell align="right">Avg Price</HeaderCell>
                  <HeaderCell align="right">LTP</HeaderCell>
                  <HeaderCell align="right">Spot</HeaderCell>
                  <HeaderCell align="right">DTE</HeaderCell>
                  <HeaderCell align="right">Breakeven</HeaderCell>
                  <HeaderCell align="right">Dist Spot</HeaderCell>
                  <HeaderCell>Alert</HeaderCell>
                  <HeaderCell align="right">Invested</HeaderCell>
                  <HeaderCell align="right">Exit</HeaderCell>
                  <HeaderCell align="right">Current</HeaderCell>
                  <HeaderCell align="right">P&L</HeaderCell>
                  <HeaderCell align="right">P&L %</HeaderCell>
                  <HeaderCell align="right"></HeaderCell>
                </tr>
              </thead>
              <tbody>
                {calculatedOptionRows.map((row) => (
                  <tr key={row.id} className="border-t border-terminal-line">
                    <InputCell widthClass="min-w-44" value={row.symbol} onChange={(value) => updateOptionRow(row.id, "symbol", value)} />
                    <InputCell align="right" widthClass="w-16" value={row.openQty} onChange={(value) => updateOptionRow(row.id, "openQty", value)} />
                    <InputCell align="right" widthClass="w-20" value={row.avgPrice} onChange={(value) => updateOptionRow(row.id, "avgPrice", value)} />
                    <ValueCell align="right" value={row.ltp || "-"} />
                    <InputCell align="right" widthClass="w-20" value={row.spot} onChange={(value) => updateOptionRow(row.id, "spot", value)} />
                    <ValueCell align="right" value={formatInteger(row.daysExpiry)} />
                    <ValueCell align="right" value={formatNullablePrice(row.breakeven)} />
                    <ValueCell align="right" value={row.distSpot || "-"} />
                    <td className={`px-3 py-2 text-sm font-semibold ${alertClass(row.alertTone)}`}>{row.alert}</td>
                    <ValueCell align="right" value={formatNullableMoney(row.invested)} />
                    <InputCell align="right" widthClass="w-20" value={row.exitPrice} onChange={(value) => updateOptionRow(row.id, "exitPrice", value)} />
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

function ExistingPositionsSection({
  existingSymbols,
  onAdd,
  positions,
}: {
  existingSymbols: Set<string>;
  onAdd: (position: ExistingOptionPosition) => void;
  positions: ExistingOptionPosition[];
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">Existing Positions</h2>
      <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
        <table className="w-full min-w-[920px] border-collapse text-left text-sm">
          <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
            <tr>
              <HeaderCell>Symbol</HeaderCell>
              <HeaderCell align="right">Qty</HeaderCell>
              <HeaderCell align="right">Avg</HeaderCell>
              <HeaderCell align="right">LTP</HeaderCell>
              <HeaderCell align="right">Spot</HeaderCell>
              <HeaderCell align="right">Strike</HeaderCell>
              <HeaderCell align="right">Breakeven</HeaderCell>
              <HeaderCell align="right">Dist Spot</HeaderCell>
              <HeaderCell>DTE</HeaderCell>
              <HeaderCell align="right">P&L</HeaderCell>
              <HeaderCell align="right">P&L %</HeaderCell>
              <HeaderCell>Alert</HeaderCell>
              <HeaderCell align="right"></HeaderCell>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => {
              const symbol = position.symbol.trim().toUpperCase();
              const alreadyAdded = existingSymbols.has(symbol);
              const metrics = existingPositionMetrics(position);
              return (
                <tr key={symbol} className="border-t border-terminal-line">
                  <ValueCell value={symbol} />
                  <ValueCell align="right" value={position.quantity.toString()} />
                  <ValueCell align="right" value={formatPrice(position.averagePrice)} />
                  <ValueCell align="right" value={formatPrice(position.lastPrice)} />
                  <ValueCell align="right" value={position.spot === undefined ? "-" : formatPrice(position.spot)} />
                  <ValueCell align="right" value={position.strike === undefined ? "-" : formatPrice(position.strike)} />
                  <ValueCell align="right" value={formatNullablePrice(metrics.breakeven)} />
                  <ValueCell align="right" value={metrics.distSpot || "-"} />
                  <ValueCell align="right" value={metrics.dte === null ? "-" : String(metrics.dte)} />
                  <ValueCell align="right" value={formatMoney(position.pnl)} tone={position.pnl} />
                  <ValueCell align="right" value={formatNullablePct(metrics.pnlPct)} tone={metrics.pnlPct} />
                  <td className={`px-3 py-2 text-sm font-semibold ${alertClass(metrics.alert.tone)}`}>
                    {metrics.alert.label}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      className="rounded-md border border-terminal-line px-3 py-1 text-sm font-semibold text-terminal-ink hover:bg-terminal-hover disabled:cursor-default disabled:opacity-40"
                      disabled={alreadyAdded}
                      type="button"
                      onClick={() => onAdd(position)}
                    >
                      {alreadyAdded ? "Added" : "Add"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function IndexSpotCard({
  checkedSymbols,
  onToggle,
  selectedContracts,
  setSelectedContracts,
  spot,
  targetOptions,
}: {
  checkedSymbols: Set<string>;
  onToggle: (payload: { checked: boolean; contract: OptionContract; spot: number }) => void;
  selectedContracts: Record<string, string>;
  setSelectedContracts: Dispatch<SetStateAction<Record<string, string>>>;
  spot: IndexSpot;
  targetOptions: TargetOptionContracts[];
}) {
  const lotSize = firstLotSize(targetOptions);

  return (
    <div className="rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-terminal-muted">{spot.symbol}</div>
          <div className="mt-1 text-2xl font-bold tabular-nums text-terminal-ink">
            {spot.spot === null ? "-" : formatPrice(spot.spot)}
          </div>
        </div>
        <div className="text-right text-xs font-semibold uppercase tracking-wide text-terminal-muted">
          Lot {lotSize === null ? "-" : lotSize}
        </div>
      </div>

      <div className="mt-3 overflow-hidden rounded-md border border-terminal-line">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
            <tr>
              <HeaderCell>Dist</HeaderCell>
              <HeaderCell align="right">CE</HeaderCell>
              <HeaderCell align="right">PE</HeaderCell>
            </tr>
          </thead>
          <tbody>
            {targetOptions.length > 0 && spot.spot !== null ? (
              targetOptions.map((strike) => {
                return (
                  <tr key={`${spot.symbol}-${strike.distancePct}`} className="border-t border-terminal-line">
                    <ValueCell value={`${strike.distancePct.toFixed(0)}%`} />
                    <ContractPicker
                      checkedSymbols={checkedSymbols}
                      contracts={strike.ceContracts ?? (strike.ce ? [strike.ce] : [])}
                      pickerKey={`${spot.symbol}-${strike.distancePct}-CE`}
                      selectedContracts={selectedContracts}
                      setSelectedContracts={setSelectedContracts}
                      onChange={(checked) =>
                        selectedContractForKey(
                          strike.ceContracts ?? (strike.ce ? [strike.ce] : []),
                          selectedContracts[`${spot.symbol}-${strike.distancePct}-CE`],
                        ) &&
                        onToggle({
                          checked,
                          contract: selectedContractForKey(
                            strike.ceContracts ?? (strike.ce ? [strike.ce] : []),
                            selectedContracts[`${spot.symbol}-${strike.distancePct}-CE`],
                          )!,
                          spot: spot.spot ?? 0,
                        })
                      }
                    />
                    <ContractPicker
                      checkedSymbols={checkedSymbols}
                      contracts={strike.peContracts ?? (strike.pe ? [strike.pe] : [])}
                      pickerKey={`${spot.symbol}-${strike.distancePct}-PE`}
                      selectedContracts={selectedContracts}
                      setSelectedContracts={setSelectedContracts}
                      onChange={(checked) =>
                        selectedContractForKey(
                          strike.peContracts ?? (strike.pe ? [strike.pe] : []),
                          selectedContracts[`${spot.symbol}-${strike.distancePct}-PE`],
                        ) &&
                        onToggle({
                          checked,
                          contract: selectedContractForKey(
                            strike.peContracts ?? (strike.pe ? [strike.pe] : []),
                            selectedContracts[`${spot.symbol}-${strike.distancePct}-PE`],
                          )!,
                          spot: spot.spot ?? 0,
                        })
                      }
                    />
                  </tr>
                );
              })
            ) : (
              <tr className="border-t border-terminal-line">
                <td className="px-3 py-3 text-sm text-terminal-muted" colSpan={3}>
                  Waiting for live spot
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ContractPicker({
  checkedSymbols,
  contracts,
  onChange,
  pickerKey,
  selectedContracts,
  setSelectedContracts,
}: {
  checkedSymbols: Set<string>;
  contracts: OptionContract[];
  onChange: (checked: boolean) => void;
  pickerKey: string;
  selectedContracts: Record<string, string>;
  setSelectedContracts: Dispatch<SetStateAction<Record<string, string>>>;
}) {
  const selectedContract = selectedContractForKey(contracts, selectedContracts[pickerKey]);
  const checked = selectedContract ? checkedSymbols.has(selectedContract.symbol) : false;

  return (
    <td className="px-3 py-2 text-right">
      <label className="inline-flex w-full items-start justify-end gap-2">
        <span className="flex flex-col items-end gap-1">
          <span className="tabular-nums">{selectedContract ? formatPrice(selectedContract.strike) : "-"}</span>
          <select
            className="max-w-28 rounded-md border border-terminal-line bg-terminal-panel-alt px-1 py-1 text-xs text-terminal-ink outline-none"
            disabled={contracts.length === 0}
            value={selectedContract?.symbol ?? ""}
            onChange={(event) => {
              if (checked) {
                onChange(false);
              }
              setSelectedContracts((previous) => ({ ...previous, [pickerKey]: event.target.value }));
            }}
          >
            {contracts.length === 0 ? (
              <option value="">-</option>
            ) : (
              contracts.map((contract) => (
                <option key={contract.symbol} value={contract.symbol}>
                  {contract.expiry}
                </option>
              ))
            )}
          </select>
        </span>
        <input
          className="mt-0.5 h-4 w-4 accent-terminal-watch"
          checked={checked}
          disabled={!selectedContract}
          type="checkbox"
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
    </td>
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
  return <th className={`whitespace-nowrap px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>{children}</th>;
}

function InputCell({
  value,
  onChange,
  align = "left",
  type = "text",
  widthClass = "w-24",
}: {
  value: string;
  onChange: (value: string) => void;
  align?: "left" | "right";
  type?: "text" | "date";
  widthClass?: string;
}) {
  return (
    <td className="px-2 py-1">
      <input
        className={`${widthClass} rounded-md border border-terminal-line bg-terminal-panel-alt px-2 py-1 text-sm text-terminal-ink outline-none focus:border-terminal-watch ${
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
      className={`whitespace-nowrap px-3 py-2 tabular-nums ${align === "right" ? "text-right" : "text-left"} ${
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

function selectedContractForKey(contracts: OptionContract[], selectedSymbol: string | undefined): OptionContract | null {
  if (contracts.length === 0) return null;
  return contracts.find((contract) => contract.symbol === selectedSymbol) ?? contracts[0];
}

function firstLotSize(targetOptions: TargetOptionContracts[]): number | null {
  for (const target of targetOptions) {
    const contract = target.ce ?? target.pe ?? target.ceContracts?.[0] ?? target.peContracts?.[0];
    if (contract) return contract.lotSize;
  }
  return null;
}

function isBlankOptionRow(row: OptionCalculatorRow): boolean {
  return !row.symbol && !row.openQty && !row.avgPrice && !row.ltp && !row.spot && !row.expiry && !row.exitPrice;
}

function daysToExpiryText(expiry?: string): string {
  if (!expiry) return "-";
  const expiryDate = new Date(expiry);
  if (!Number.isFinite(expiryDate.getTime())) return "-";
  const today = new Date();
  const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const expiryUtc = Date.UTC(expiryDate.getFullYear(), expiryDate.getMonth(), expiryDate.getDate());
  return String(Math.round((expiryUtc - todayUtc) / 86400000));
}

function existingPositionMetrics(position: ExistingOptionPosition) {
  const breakeven =
    position.strike === undefined || position.optionType === undefined
      ? null
      : position.optionType === "PE"
        ? position.strike - position.averagePrice
        : position.strike + position.averagePrice;
  const dteText = daysToExpiryText(position.expiry);
  const dte = dteText === "-" ? null : Number(dteText);
  const invested = Math.abs(position.quantity) * position.averagePrice;
  const pnlPct = invested === 0 ? null : (position.pnl / invested) * 100;
  const isOtm =
    position.spot !== undefined &&
    position.strike !== undefined &&
    position.optionType !== undefined &&
    ((position.optionType === "CE" && position.spot < position.strike) ||
      (position.optionType === "PE" && position.spot > position.strike));

  return {
    breakeven,
    distSpot: formatManualSpotDistance(breakeven, position.spot ?? null),
    dte,
    pnlPct,
    alert: generateExitAlert({
      entryPrice: position.averagePrice,
      currentLtp: position.lastPrice,
      dte,
      isOtm,
    }),
  };
}
