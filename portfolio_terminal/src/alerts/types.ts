export type AlertStatusFilter = "active" | "enabled" | "disabled";

export type KiteAlert = {
  uuid: string;
  name: string;
  type: "simple" | "ato" | string;
  status: "enabled" | "disabled" | "deleted" | string;
  disabled_reason?: string;
  lhs_exchange: string;
  lhs_tradingsymbol: string;
  lhs_attribute: string;
  operator: "<=" | ">=" | "<" | ">" | "==" | string;
  rhs_type: "constant" | "instrument" | string;
  rhs_constant?: number | string | null;
  rhs_exchange?: string;
  rhs_tradingsymbol?: string;
  rhs_attribute?: string;
  alert_count: number;
  ltp?: number | null;
  created_at?: string;
  updated_at?: string;
};

export type AlertsData = {
  alerts: KiteAlert[];
  statusFilter?: AlertStatusFilter;
  loaded?: boolean;
  message?: string;
  error?: string;
  lastAction?: string;
  lastRequestId?: string;
};

export type AlertFormValues = {
  uuid?: string;
  name: string;
  type: "simple";
  lhs_exchange: string;
  lhs_tradingsymbol: string;
  lhs_attribute: string;
  operator: "<=" | ">=" | "<" | ">" | "==";
  rhs_type: "constant";
  rhs_constant: string;
  rhs_exchange: string;
  rhs_tradingsymbol: string;
  rhs_attribute: string;
};
