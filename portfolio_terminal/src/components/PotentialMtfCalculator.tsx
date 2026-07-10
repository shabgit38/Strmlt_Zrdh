import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatMoney, formatPct, formatPrice, signedClass } from "../format";
import { setStreamlitComponentValue } from "../streamlitBridge";
import type { CalculatorsLiveData, CalculatorsLiveRequest } from "../calculators/types";

const DAILY_INTEREST_RATE = 0.0004;
const INITIAL_MARGIN_RATE = 0.2;
const DAY_MS = 86_400_000;

export function PotentialMtfCalculator({ liveData }: { liveData?: CalculatorsLiveData | null }) {
  const defaults = useMemo(defaultDates, []);
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [expectedReturn, setExpectedReturn] = useState("");
  const [entryDate, setEntryDate] = useState(defaults.entry);
  const [exitDate, setExitDate] = useState(defaults.exit);
  const requestedSymbol = useRef("");
  const normalizedSymbol = symbol.trim().toUpperCase();
  const livePrice = normalizedSymbol ? liveData?.equities?.[normalizedSymbol]?.ltp : undefined;

  useEffect(() => {
    if (livePrice !== undefined) setPrice(String(livePrice));
  }, [livePrice]);

  useEffect(() => {
    if (!normalizedSymbol || requestedSymbol.current === normalizedSymbol) return;
    const timeout = window.setTimeout(() => {
      requestedSymbol.current = normalizedSymbol;
      const request: CalculatorsLiveRequest = {
        type: "marketData",
        requestId: `${Date.now()}-mtf-${normalizedSymbol}`,
        symbols: [],
        equitySymbols: [normalizedSymbol],
        includeSpots: false,
      };
      setStreamlitComponentValue(request);
    }, 750);
    return () => window.clearTimeout(timeout);
  }, [normalizedSymbol]);

  const metrics = calculatePotentialMtf(quantity, price, expectedReturn, entryDate, exitDate);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm font-semibold uppercase tracking-wide text-terminal-muted">
        <span>Potential MTF Trade Calculator</span>
        <span className="text-xs normal-case font-normal">20% margin · 0.04% daily interest</span>
      </div>
      <div className="rounded-lg border border-terminal-line bg-terminal-panel p-3 shadow-sm">
        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Symbol"><input value={symbol} onChange={(event) => { setSymbol(event.target.value.toUpperCase()); requestedSymbol.current = ""; }} placeholder="e.g. INFY" /></Field>
          <Field label="MTF Qty"><input type="number" min="0" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></Field>
          <Field label="MTF Avg"><input type="number" min="0" step="0.05" value={price} onChange={(event) => setPrice(event.target.value)} placeholder="Live / editable" /></Field>
          <Field label="Exp Ret%"><input type="number" step="0.1" value={expectedReturn} onChange={(event) => setExpectedReturn(event.target.value)} /></Field>
          <Field label="Buy Date"><input type="date" value={entryDate} onChange={(event) => setEntryDate(event.target.value)} /></Field>
          <Field label="Exit Date"><input type="date" value={exitDate} onChange={(event) => setExitDate(event.target.value)} /></Field>
        </div>
        {entryDate && exitDate && metrics.days === null ? <p className="mt-2 text-xs font-semibold text-terminal-avoid">Exit date must be on or after entry date.</p> : null}
        <div className="mt-2 grid gap-1.5 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-12">
          <Metric label="Days" value={metrics.days === null ? "-" : String(metrics.days)} />
          <Metric label="MTF Value" value={money(metrics.buyValue)} />
          <Metric label="Initial Margin" value={money(metrics.initialMargin)} />
          <Metric label="Funded" value={money(metrics.fundedAmount)} />
          <Metric label="Int/Day" value={money(metrics.interestPerDay)} />
          <Metric label="Interest" value={money(metrics.interest)} tone="text-terminal-near" />
          <Metric label="Exit Price" value={priceValue(metrics.exitPrice)} />
          <Metric label="P&L" value={money(metrics.grossPnl)} tone={tone(metrics.grossPnl)} />
          <Metric label="Charges" value={money(metrics.charges)} />
          <Metric label="Net P&L" value={money(metrics.netPnl)} tone={tone(metrics.netPnl)} />
          <Metric label="Net Ret%" value={pct(metrics.netReturnPct)} tone={tone(metrics.netReturnPct)} />
          <Metric label="Breakeven" value={priceValue(metrics.breakeven)} tone="text-terminal-near" />
        </div>
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactElement<{ className?: string }> }) {
  return <label className="min-w-0 space-y-0.5 text-[11px] font-semibold uppercase tracking-wide text-terminal-muted"><span>{label}</span><span className="block [&>input]:w-full [&>input]:min-w-0 [&>input]:rounded-md [&>input]:border [&>input]:border-terminal-line [&>input]:bg-terminal-panel-alt [&>input]:px-2 [&>input]:py-1.5 [&>input]:text-xs [&>input]:text-terminal-ink [&>input]:outline-none focus:[&>input]:border-terminal-watch">{children}</span></label>;
}

function Metric({ label, value, tone = "text-terminal-ink" }: { label: string; value: string; tone?: string }) {
  return <div className="min-w-0 rounded-md border border-terminal-line bg-terminal-panel-alt px-1.5 py-2"><div className="truncate text-[9px] font-semibold uppercase tracking-wide text-terminal-muted" title={label}>{label}</div><div className={`mt-0.5 truncate text-xs font-bold tabular-nums ${tone}`} title={value}>{value}</div></div>;
}

function calculatePotentialMtf(qtyText: string, priceText: string, returnText: string, entry: string, exit: string) {
  const qty = positiveNumber(qtyText);
  const entryPrice = positiveNumber(priceText);
  const returnPct = finiteNumber(returnText);
  const days = calendarDays(entry, exit);
  const ready = qty !== null && entryPrice !== null && returnPct !== null && days !== null;
  if (!ready) return emptyMetrics(days);
  const buyValue = qty * entryPrice;
  const initialMargin = buyValue * INITIAL_MARGIN_RATE;
  const fundedAmount = buyValue - initialMargin;
  const interestPerDay = fundedAmount * DAILY_INTEREST_RATE;
  const interest = interestPerDay * days;
  const exitPrice = entryPrice * (1 + returnPct / 100);
  const grossPnl = buyValue * returnPct / 100;
  const charges = estimatedRoundTripCharges(buyValue, qty * exitPrice);
  const netPnl = grossPnl - interest - charges;
  const netReturnPct = initialMargin === 0 ? null : netPnl / initialMargin * 100;
  const breakeven = qty === 0 ? null : entryPrice + (interest + charges) / qty;
  return { days, buyValue, initialMargin, fundedAmount, interestPerDay, interest, exitPrice, grossPnl, charges, netPnl, netReturnPct, breakeven };
}

function estimatedRoundTripCharges(buyValue: number, sellValue: number) {
  const brokerage = Math.min(buyValue * 0.003, 20) + Math.min(sellValue * 0.003, 20);
  const pledgeAndUnpledge = 30 * 1.18;
  return brokerage + pledgeAndUnpledge;
}

function calendarDays(entry: string, exit: string) {
  if (!entry || !exit) return null;
  const start = Date.parse(`${entry}T00:00:00Z`);
  const end = Date.parse(`${exit}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return Math.round((end - start) / DAY_MS);
}

function defaultDates() {
  const now = new Date();
  const exit = new Date(now);
  exit.setDate(exit.getDate() + 60);
  return { entry: localDate(now), exit: localDate(exit) };
}

function localDate(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function finiteNumber(value: string) { const parsed = Number(value); return value.trim() && Number.isFinite(parsed) ? parsed : null; }
function positiveNumber(value: string) { const parsed = finiteNumber(value); return parsed !== null && parsed > 0 ? parsed : null; }
function emptyMetrics(days: number | null) { return { days, buyValue: null, initialMargin: null, fundedAmount: null, interestPerDay: null, interest: null, exitPrice: null, grossPnl: null, charges: null, netPnl: null, netReturnPct: null, breakeven: null }; }
function money(value: number | null) { return value === null ? "-" : formatMoney(value); }
function priceValue(value: number | null) { return value === null ? "-" : formatPrice(value); }
function pct(value: number | null) { return value === null ? "-" : formatPct(value); }
function tone(value: number | null) { return value === null ? "text-terminal-ink" : signedClass(value); }
