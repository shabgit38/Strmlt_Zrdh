export type OptionCalculatorRow = {
  id: string;
  symbol: string;
  openQty: string;
  avgPrice: string;
  ltp: string;
  spot: string;
  expiry: string;
  strike: string;
  optionType: string;
  exitPrice: string;
};

export type CalculatedOptionRow = OptionCalculatorRow & {
  daysExpiry: number | null;
  breakeven: number | null;
  distSpot: string;
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
  dayChange?: number | null;
  dayChangePct?: number | null;
  status: "Live" | "Missing" | "Error";
};

export type LiveOptionQuote = {
  symbol: string;
  ltp?: number;
  spot?: number;
  expiry?: string;
  strike?: number;
  optionType?: "CE" | "PE";
  lotSize?: number;
};

export type OptionContract = {
  symbol: string;
  expiry: string;
  strike: number;
  optionType: "CE" | "PE";
  lotSize: number;
  exchange: string;
  segment: string;
  instrumentToken: number;
};

export type TargetOptionContracts = {
  index: string;
  strike: number;
  ce?: OptionContract;
  pe?: OptionContract;
  ceContracts?: OptionContract[];
  peContracts?: OptionContract[];
};

export type ExistingOptionPosition = {
  symbol: string;
  quantity: number;
  averagePrice: number;
  lastPrice: number;
  pnl: number;
  spot?: number;
  expiry?: string;
  strike?: number;
  optionType?: "CE" | "PE";
  lotSize?: number;
};

export type CalculatorsLiveData = {
  requestId?: string;
  fetchedAt?: string;
  spots?: IndexSpot[];
  options?: Record<string, LiveOptionQuote>;
  equities?: Record<string, { symbol: string; ltp?: number }>;
  targetOptions?: Record<string, TargetOptionContracts[]>;
  positions?: ExistingOptionPosition[];
  error?: string;
};

export type CalculatorsLiveRequest = {
  type: "marketData";
  requestId: string;
  symbols: string[];
  includeSpots: boolean;
  equitySymbols?: string[];
};
