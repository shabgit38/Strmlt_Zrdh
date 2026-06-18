export const INDEX_CONFIG = {
  NIFTY: { step: 50 },
  BANKNIFTY: { step: 100 },
  SENSEX: { step: 100 },
} as const;

export type IndexSymbol = keyof typeof INDEX_CONFIG;

export type TargetStrike = {
  distancePct: number;
  ceStrike: number;
  peStrike: number;
};

function roundToStep(value: number, step: number): number {
  return Math.round(value / step) * step;
}

export function calculateTargetStrikes(index: IndexSymbol, spot: number): TargetStrike[] {
  const step = INDEX_CONFIG[index].step;
  return [0.02, 0.03, 0.05].map((distance) => ({
    distancePct: distance * 100,
    ceStrike: roundToStep(spot * (1 + distance), step),
    peStrike: roundToStep(spot * (1 - distance), step),
  }));
}
