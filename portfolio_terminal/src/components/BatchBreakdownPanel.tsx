import type { Holding } from "../types";
import { formatPct, formatPrice, signedClass } from "../format";

type BatchBreakdownPanelProps = {
  holding: Holding | null;
};

export function BatchBreakdownPanel({ holding }: BatchBreakdownPanelProps) {
  if (!holding) {
    return (
      <aside className="rounded-lg border border-dashed border-terminal-line bg-terminal-panel p-4 text-sm text-terminal-muted">
        Select a holding row to view batch details.
      </aside>
    );
  }

  return (
    <aside className="rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
      <table className="w-full border-collapse text-sm">
        <thead className="text-xs uppercase tracking-wide text-terminal-muted">
          <tr>
            <th className="px-2 py-2 text-right">Price</th>
            <th className="px-2 py-2 text-right">Qty</th>
            <th className="px-2 py-2 text-left">Age</th>
            <th className="px-2 py-2 text-right">Profit %</th>
          </tr>
        </thead>
        <tbody>
          {holding.batches.map((batch, index) => (
            <tr key={`${holding.symbol}-${index}`} className="border-t border-terminal-line">
              <td className="px-2 py-2 text-right tabular-nums">{formatPrice(batch.price)}</td>
              <td className="px-2 py-2 text-right tabular-nums">{batch.qty}</td>
              <td className="px-2 py-2 text-xs text-terminal-muted">{batch.age}</td>
              <td className={`px-2 py-2 text-right tabular-nums ${signedClass(batch.profitPct)}`}>
                {formatPct(batch.profitPct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </aside>
  );
}
