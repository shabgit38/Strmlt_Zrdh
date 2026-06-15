export type Batch = {
  price: number;
  qty: number;
  age: string;
  profitPct: number;
};

export type Holding = {
  symbol: string;
  quantity: number;
  averagePrice: number;
  invested: number;
  weightPct: number;
  current: number;
  ltp: number;
  pnl: number;
  pnlPct: number;
  dayChangePct: number;
  batches: Batch[];
};

export type SectorGroup = {
  sector: string;
  holdingsCount: number;
  invested: number;
  weightPct: number;
  current: number;
  pnl: number;
  pnlPct: number;
  holdings: Holding[];
};

export type MtfHolding = {
  symbol: string;
  mtfQty: number;
  mtfAvgPrice: number;
  mtfValue: number;
  ltp: number;
  pnl: number;
  dayChangePct: number;
};

export type PortfolioSnapshot = {
  asOf: string;
  totals: {
    invested: number;
    current: number;
    pnl: number;
    pnlPct: number;
  };
  sectors: SectorGroup[];
  mtfHoldings: MtfHolding[];
};
