export type OptionCalculatorRow = {
  id: string;
  symbol: string;
  openQty: string;
  avgPrice: string;
  ltp: string;
  spot: string;
  expiry: string;
  exitPrice: string;
};

export type CalculatedOptionRow = OptionCalculatorRow & {
  daysExpiry: number | null;
  breakeven: number | null;
  distSpot: string;
  intrinsic: number | null;
  timeValue: number | null;
  moneyness: "ITM" | "OTM" | "ATM" | "";
  alert: string;
  alertTone: "normal" | "review" | "warning" | "exit" | "hardExit";
  invested: number | null;
  current: number | null;
  pnl: number | null;
  pnlPct: number | null;
};

export type TradeCalculatorRow = {
  id: string;
  symbol: string;
  buy: string;
  qty: string;
  sell: string;
  entry: string;
  exit: string;
  sl: string;
  tgt: string;
};

export type CalculatedTradeRow = TradeCalculatorRow & {
  totalInvested: number | null;
  profit: number | null;
  profitPct: number | null;
  days: number | null;
};

export type TradeSummaryRow = {
  symbol: string;
  qty: number;
  avgBuy: number;
  totalInvested: number;
  profit: number | null;
  profitPct: number | null;
};

export type AvgCalculatorRow = {
  id: string;
  symbol: string;
  qty: string;
  avgPrice: string;
  ltp: string;
};

export type CalculatedAvgRow = AvgCalculatorRow & {
  invested: number | null;
  profit: number | null;
};

export type AvgSummaryRow = {
  symbol: string;
  totalQty: number;
  totalAveragePrice: number | null;
  breakeven: number | null;
  totalInvested: number;
  profit: number | null;
  profitPct: number | null;
};

export type IndexSpot = {
  symbol: string;
  spot: number | null;
  status: "Live" | "Missing" | "Error";
};

export type LiveOptionQuote = {
  symbol: string;
  ltp?: number;
  spot?: number;
  expiry?: string;
};

export type CalculatorsLiveData = {
  requestId?: string;
  fetchedAt?: string;
  spots?: IndexSpot[];
  options?: Record<string, LiveOptionQuote>;
  error?: string;
};

export type CalculatorsLiveRequest = {
  type: "marketData";
  requestId: string;
  symbols: string[];
  includeSpots: boolean;
};
