import type { PositionChartPoint } from "../types";
import { formatPrice } from "../format";

type PositionLineChartProps = {
  points?: PositionChartPoint[];
};

export function PositionLineChart({ points = [] }: PositionLineChartProps) {
  if (points.length === 0) return null;

  return (
    <div className="col-span-10 overflow-x-auto px-1 pb-2 pt-1">
      <div
        className="grid min-w-max"
        style={{ gridTemplateColumns: `repeat(${points.length}, minmax(4.25rem, 1fr))` }}
      >
        {points.map((point, index) => {
          const current = point.label === "LTP" || point.label === "Latest Close";
          const endpoint = point.label === "Upper Rng" || point.label === "Lower Rng";
          const distanceValue = point.distance ? Number(point.distance.replace("%", "")) : 0;
          const color = current
            ? "#FFB15C"
            : endpoint
              ? "#5EA6D1"
              : distanceValue > 0
                ? "#7DCE9B"
                : distanceValue < 0
                  ? "#BE123C"
                  : "#64748B";

          return (
            <div
              key={`${point.label}-${point.value}`}
              className={`grid grid-rows-[0.9rem_0.5rem_auto] items-center rounded text-center ${
                current ? "bg-amber-100/10" : ""
              }`}
              title={`${point.label} ${formatPrice(point.value)}${point.distance ? ` ${point.distance}` : ""}`}
            >
              <span className="whitespace-nowrap text-[10px] leading-none text-terminal-ink">
                {point.label}
              </span>
              <span className="flex w-full items-center">
                <span className={`h-0.5 flex-1 ${index === 0 ? "bg-transparent" : "bg-terminal-muted/60"}`} />
                <span className="h-1.5 w-1.5 flex-none rounded-full border" style={{ backgroundColor: color }} />
                <span className={`h-0.5 flex-1 ${index === points.length - 1 ? "bg-transparent" : "bg-terminal-muted/60"}`} />
              </span>
              <span className="grid text-[10px] leading-none">
                <span className="font-bold" style={{ color }}>{formatPrice(point.value)}</span>
                <span className="mt-0.5 font-bold" style={{ color }}>{point.distance ?? "\u00a0"}</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
