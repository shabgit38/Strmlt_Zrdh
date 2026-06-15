import { useEffect, useState } from "react";
import { PieChart } from "lucide-react";
import { loadPortfolioSnapshot } from "./api/portfolioApi";
import { GroupedHoldings, sectorAnchorId } from "./components/GroupedHoldings";
import { MtfHoldingsTable } from "./components/MtfHoldingsTable";
import { SectorPieChart } from "./components/SectorPieChart";
import { SectorSummaryTable } from "./components/SectorSummaryTable";
import { formatMoney, formatPct, signedClass } from "./format";
import type { Holding, PortfolioSnapshot } from "./types";

type AppProps = {
  streamlitSnapshot?: PortfolioSnapshot | null;
  streamlitMode?: boolean;
};

export function App({ streamlitSnapshot, streamlitMode = false }: AppProps) {
  const [snapshot, setSnapshot] = useState<PortfolioSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedSector, setSelectedSector] = useState<string | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  useEffect(() => {
    if (streamlitMode) {
      setError(null);
      setSnapshot(streamlitSnapshot ?? null);
      const firstSector = streamlitSnapshot?.sectors[0];
      setSelectedSector(firstSector?.sector ?? null);
      setSelectedSymbol(firstSector?.holdings[0]?.symbol ?? null);
      return;
    }

    loadPortfolioSnapshot()
      .then((data) => {
        setSnapshot(data);
        const firstSector = data.sectors[0];
        setSelectedSector(firstSector?.sector ?? null);
        setSelectedSymbol(firstSector?.holdings[0]?.symbol ?? null);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Failed to load portfolio snapshot");
      });
  }, [streamlitMode, streamlitSnapshot]);

  function handleSelectHolding(sector: string, holding: Holding) {
    setSelectedSector(sector);
    setSelectedSymbol(holding.symbol);
  }

  function handleSelectSector(sector: string) {
    setSelectedSector(sector);
    window.requestAnimationFrame(() => {
      document.getElementById(sectorAnchorId(sector))?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-terminal-bg p-8">
        <div className="max-w-md rounded-lg border border-terminal-line bg-terminal-panel p-6 shadow-sm">
          <p className="text-sm text-terminal-avoid">{error}</p>
        </div>
      </main>
    );
  }

  if (!snapshot) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-terminal-bg p-8">
        <div className="rounded-lg border border-terminal-line bg-terminal-panel p-6 text-terminal-muted shadow-sm">
          Loading portfolio snapshot...
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-terminal-bg">
      <div className="mx-auto max-w-[1680px] space-y-5 px-5 py-5">
        <section className="grid gap-3 md:grid-cols-5">
          <Metric label="Invested" value={formatMoney(snapshot.totals.invested)} />
          <Metric label="Current" value={formatMoney(snapshot.totals.current)} />
          <Metric label="P&L" value={formatMoney(snapshot.totals.pnl)} tone={signedClass(snapshot.totals.pnl)} />
          <Metric label="P&L %" value={formatPct(snapshot.totals.pnlPct)} tone={signedClass(snapshot.totals.pnlPct)} />
          <Metric
            label="Day P&L"
            value={formatDayPnl(snapshot.totals.dayPnl, snapshot.totals.dayPnlPct)}
            tone={signedClass(snapshot.totals.dayPnl)}
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(24rem,0.9fr)_minmax(0,1.6fr)]">
          <SectorPieChart sectors={snapshot.sectors} />
          <SectorSummaryTable
            sectors={snapshot.sectors}
            selectedSector={selectedSector}
            onSelectSector={handleSelectSector}
            asOf={snapshot.asOf}
          />
        </section>

        <MtfHoldingsTable holdings={snapshot.mtfHoldings ?? []} />

        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-terminal-muted">
            <PieChart className="h-4 w-4" />
            Grouped Holdings
          </div>
          <GroupedHoldings
            sectors={snapshot.sectors}
            selectedSector={selectedSector}
            selectedSymbol={selectedSymbol}
            onSelectHolding={handleSelectHolding}
          />
        </section>
      </div>
    </main>
  );
}

type MetricProps = {
  label: string;
  value: string;
  tone?: string;
};

function Metric({ label, value, tone = "text-terminal-ink" }: MetricProps) {
  return (
    <div className="rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
      <div className="text-xs font-semibold uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}

function formatDayPnl(value: number, pct: number): string {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}${formatMoney(Math.abs(value))}[${formatPct(pct)}]`;
}
