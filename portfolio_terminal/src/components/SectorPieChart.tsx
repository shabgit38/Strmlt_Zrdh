import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import type { SectorGroup } from "../types";
import { formatMoney, formatPct } from "../format";

const COLORS = ["#0F766E", "#2563EB", "#D97706", "#64748B", "#BE123C", "#7C3AED", "#0891B2"];

type SectorPieChartProps = {
  sectors: SectorGroup[];
};

export function SectorPieChart({ sectors }: SectorPieChartProps) {
  const data = sectors.map((sector) => ({
    name: sector.sector,
    invested: sector.invested,
    weightPct: sector.weightPct,
  }));

  return (
    <section className="h-full rounded-lg border border-terminal-line bg-terminal-panel p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-terminal-ink">Sector Weightage</h2>
        <span className="text-xs text-terminal-muted">Invested allocation</span>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="invested"
              nameKey="name"
              innerRadius="52%"
              outerRadius="78%"
              paddingAngle={2}
              stroke="#FFFFFF"
              strokeWidth={2}
            >
              {data.map((entry, index) => (
                <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value, _name, item) => [
                `${formatMoney(Number(value))} (${formatPct(item.payload.weightPct)})`,
                item.payload.name,
              ]}
            />
            <Legend iconType="circle" layout="horizontal" verticalAlign="bottom" height={48} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
