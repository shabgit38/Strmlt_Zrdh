export const INDEX_CONFIG = {
  NIFTY: { step: 50 },
  BANKNIFTY: { step: 100 },
  SENSEX: { step: 100 },
} as const;

export type IndexSymbol = keyof typeof INDEX_CONFIG;

export type TargetStrike = {
  strike: number;
};

function strikeBounds(spot: number, step: number): { lower: number; upper: number } {
  return {
    lower: Math.floor((spot * 0.95) / step) * step,
    upper: Math.ceil((spot * 1.05) / step) * step,
  };
}

export function calculateTargetStrikes(index: IndexSymbol, spot: number): TargetStrike[] {
  const step = Math.max(INDEX_CONFIG[index].step, 100);
  const { lower, upper } = strikeBounds(spot, step);
  const strikes: TargetStrike[] = [];
  for (let strike = lower; strike <= upper; strike += step) {
    strikes.push({ strike });
  }
  return strikes;
}
