import { Edit3, RefreshCw, Save, Trash2, X } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { formatPrice } from "../format";
import { setStreamlitComponentValue, setStreamlitFrameHeight } from "../streamlitBridge";
import type { AlertFormValues, AlertsData, AlertStatusFilter, KiteAlert } from "../alerts/types";

type SortKey = "status" | "symbol" | "name" | "ltp" | "updated_at";
type SortDirection = "asc" | "desc";
type AlertAction = "fetch" | "create" | "modify" | "delete";

const EMPTY_FORM: AlertFormValues = {
  name: "",
  type: "simple",
  lhs_exchange: "NSE",
  lhs_tradingsymbol: "",
  lhs_attribute: "LastTradedPrice",
  operator: ">=",
  rhs_type: "constant",
  rhs_constant: "",
  rhs_exchange: "",
  rhs_tradingsymbol: "",
  rhs_attribute: "",
};

export function AlertsScreen({ data }: { data?: AlertsData | null }) {
  const [statusFilter, setStatusFilter] = useState<AlertStatusFilter>(data?.statusFilter ?? "active");
  const [sortKey, setSortKey] = useState<SortKey>("status");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [editingUuid, setEditingUuid] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<AlertFormValues>(EMPTY_FORM);
  const [searchText, setSearchText] = useState("");
  const [pendingAction, setPendingAction] = useState<AlertAction | null>(null);

  useEffect(() => {
    setStatusFilter(data?.statusFilter ?? "active");
  }, [data?.statusFilter]);

  useEffect(() => {
    window.setTimeout(() => setStreamlitFrameHeight(), 50);
  }, [data, editingUuid, formValues]);

  useEffect(() => {
    if (data?.lastAction !== "modify" || data.error) return;
    setEditingUuid(null);
    setFormValues(EMPTY_FORM);
  }, [data?.lastAction, data?.lastRequestId, data?.error]);

  useEffect(() => {
    if (!data?.lastRequestId) return;
    setPendingAction(null);
  }, [data?.lastRequestId]);

  const alerts = data?.alerts ?? [];
  const uniqueAlerts = useMemo(() => dedupeAlertsByUuid(alerts), [alerts]);
  const visibleAlerts = useMemo(
    () => uniqueAlerts.filter((alert) => matchesStatusFilter(alert, statusFilter)).filter((alert) => matchesSearch(alert, searchText)),
    [uniqueAlerts, statusFilter, searchText],
  );
  const sortedAlerts = useMemo(
    () => [...visibleAlerts].sort((left, right) => compareAlerts(left, right, sortKey, sortDirection)),
    [visibleAlerts, sortKey, sortDirection],
  );

  function sendAction(action: AlertAction, payload: Record<string, unknown> = {}) {
    const requestId = `${Date.now()}-${action}`;
    setPendingAction(action);
    setStreamlitComponentValue({
      type: "alerts",
      action,
      requestId,
      statusFilter: "active",
      payload,
    });
  }

  function handleSort(nextSortKey: SortKey) {
    if (nextSortKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextSortKey);
    setSortDirection(nextSortKey === "status" || nextSortKey === "symbol" || nextSortKey === "name" ? "asc" : "desc");
  }

  function startCreate() {
    setEditingUuid(null);
    setFormValues(EMPTY_FORM);
  }

  function startEdit(alert: KiteAlert) {
    setEditingUuid(alert.uuid);
    setFormValues({
      uuid: alert.uuid,
      name: alert.name ?? "",
      type: "simple",
      lhs_exchange: alert.lhs_exchange ?? "NSE",
      lhs_tradingsymbol: alert.lhs_tradingsymbol ?? "",
      lhs_attribute: alert.lhs_attribute || "LastTradedPrice",
      operator: operatorValue(alert.operator),
      rhs_type: "constant",
      rhs_constant: alert.rhs_constant === undefined || alert.rhs_constant === null ? "" : String(alert.rhs_constant),
      rhs_exchange: alert.rhs_exchange ?? "",
      rhs_tradingsymbol: alert.rhs_tradingsymbol ?? "",
      rhs_attribute: alert.rhs_attribute ?? "",
    });
  }

  function submitForm() {
    if (editingUuid) {
      sendAction("modify", { ...formValues, uuid: editingUuid });
      return;
    }
    sendAction("create", formValues);
  }

  return (
    <main className="min-h-screen bg-terminal-bg">
      <div className="mx-auto max-w-[1680px] space-y-4 px-5 py-5">
        <section className="grid gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">Alerts</h2>
            <div className="mt-1 text-xs text-terminal-muted">
              {data?.loaded
                ? `${sortedAlerts.length} shown from ${uniqueAlerts.length} unique alert${uniqueAlerts.length === 1 ? "" : "s"}`
                : "Fetch alerts to load Kite data"}
            </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="rounded-md border border-terminal-line bg-terminal-panel px-3 py-2 text-sm font-semibold text-terminal-ink outline-none"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as AlertStatusFilter)}
              >
                <option value="active">Active</option>
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </select>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-terminal-line px-3 py-2 text-sm font-semibold text-terminal-ink hover:bg-terminal-hover"
                type="button"
                disabled={pendingAction !== null}
                onClick={() => sendAction("fetch")}
              >
                <RefreshCw className={`h-4 w-4 ${pendingAction === "fetch" ? "animate-spin text-terminal-watch" : ""}`} />
                {pendingAction === "fetch" ? "Fetching..." : "Get alerts"}
              </button>
            </div>
          </div>
          {data?.disabledSymbolsText ? (
            <div className="whitespace-normal break-words rounded-md border border-terminal-line bg-terminal-panel px-3 py-2 text-xs font-semibold leading-5 text-terminal-near" title={data.disabledSymbolsText}>
              Disabled: {data.disabledSymbolsText}
            </div>
          ) : null}
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(22rem,0.7fr)]">
          <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
            <div className="border-b border-terminal-line bg-terminal-panel-alt px-3 py-2">
              <input
                className="w-full rounded-md border border-terminal-line bg-terminal-panel px-3 py-2 text-sm font-semibold text-terminal-ink outline-none focus:border-terminal-watch"
                placeholder="Search alerts by symbol, name, status, trigger, or UUID"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
            </div>
            <table className="w-max min-w-full border-collapse text-left text-xs">
              <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                <tr>
                  <SortableHeader sortKey="symbol" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Symbol</SortableHeader>
                  <SortableHeader className="w-32 max-w-32" sortKey="name" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Name</SortableHeader>
                  <HeaderCell align="right" className="w-20 max-w-20">LTP</HeaderCell>
                  <HeaderCell className="w-32 max-w-32">Position</HeaderCell>
                  <HeaderCell align="right" className="w-20 max-w-20">Trigger</HeaderCell>
                  <SortableHeader className="w-20 max-w-20" sortKey="status" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Status</SortableHeader>
                  <HeaderCell align="right" className="w-20 max-w-20"></HeaderCell>
                  <SortableHeader className="w-28 max-w-28" sortKey="updated_at" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Updated</SortableHeader>
                </tr>
              </thead>
              <tbody>
                {sortedAlerts.map((alert) => (
                  <tr key={alert.uuid} className={`border-t border-terminal-line ${editingUuid === alert.uuid ? "bg-terminal-selected" : ""}`}>
                    <td className="whitespace-nowrap px-3 py-2 text-xs font-semibold text-terminal-ink">{alert.lhs_tradingsymbol}</td>
                    <td className="max-w-32 truncate whitespace-nowrap px-3 py-2 text-xs text-terminal-ink" title={alert.name}>{alert.name}</td>
                    <td className="w-20 max-w-20 whitespace-nowrap px-2 py-2 text-right text-xs tabular-nums text-terminal-ink">{alert.ltp === null || alert.ltp === undefined ? "-" : formatPrice(Number(alert.ltp))}</td>
                    <td className="w-32 max-w-32 px-2 py-2 text-xs leading-4 text-terminal-muted" title={alert.price_context || "-"}>
                      <span className="line-clamp-2 whitespace-normal break-words tabular-nums">{alert.price_context || "-"}</span>
                    </td>
                    <td className="w-20 max-w-20 truncate whitespace-nowrap px-2 py-2 text-right text-xs tabular-nums text-terminal-ink" title={triggerText(alert)}>
                      {triggerText(alert)}
                    </td>
                    <td className={`w-20 max-w-20 truncate whitespace-nowrap px-2 py-2 text-xs font-semibold ${statusClass(alert.status)}`} title={alert.status}>{alert.status}</td>
                    <td className="w-20 max-w-20 whitespace-nowrap px-2 py-2 text-right">
                      <div className="inline-flex items-center gap-1">
                        <button
                          className="rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover hover:text-terminal-ink disabled:cursor-default disabled:opacity-40"
                          type="button"
                          title={alert.type === "simple" ? "Edit" : "ATO edit disabled in phase 1"}
                          disabled={alert.type !== "simple" || pendingAction !== null}
                          onClick={() => startEdit(alert)}
                        >
                          <Edit3 className="h-4 w-4" />
                        </button>
                        <button className="rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover hover:text-terminal-avoid disabled:cursor-default disabled:opacity-40" type="button" title="Delete" disabled={pendingAction !== null} onClick={() => window.confirm(`Delete alert "${alert.name}"?`) && sendAction("delete", { uuid: alert.uuid })}>
                          <Trash2 className={`h-4 w-4 ${pendingAction === "delete" ? "animate-pulse text-terminal-near" : ""}`} />
                        </button>
                      </div>
                    </td>
                    <td className="w-28 max-w-28 truncate whitespace-nowrap px-2 py-2 text-xs text-terminal-muted" title={alert.updated_at || "-"}>
                      {formatUpdatedDate(alert.updated_at)}
                    </td>
                  </tr>
                ))}
                {data?.loaded && sortedAlerts.length === 0 ? (
                  <tr className="border-t border-terminal-line">
                    <td className="px-3 py-6 text-xs text-terminal-muted" colSpan={8}>No alerts returned for this filter.</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <aside className="rounded-md border border-terminal-line bg-terminal-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">{editingUuid ? "Modify Alert" : "Create Alert"}</h3>
              {editingUuid ? (
                <button className="rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover" type="button" title="Clear edit" onClick={startCreate}>
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>
            {data?.error ? <div className="mb-3 rounded-md border border-terminal-avoid bg-terminal-panel-alt p-3 text-sm font-semibold text-terminal-avoid">{data.error}</div> : null}
            {data?.message ? <div className="mb-3 rounded-md border border-terminal-line bg-terminal-panel-alt p-3 text-sm font-semibold text-terminal-entry">{data.message}</div> : null}
            <div className="grid gap-3">
              <TextField label="Name" value={formValues.name} onChange={(value) => setFormValues((current) => ({ ...current, name: value }))} />
              <div className="grid grid-cols-[0.8fr_1.2fr] gap-2">
                <SelectField label="Exchange" value={formValues.lhs_exchange} options={["NSE", "BSE", "NFO", "CDS", "BCD", "MCX", "INDICES"]} onChange={(value) => setFormValues((current) => ({ ...current, lhs_exchange: value }))} />
                <TextField label="Symbol" value={formValues.lhs_tradingsymbol} onChange={(value) => setFormValues((current) => ({ ...current, lhs_tradingsymbol: value.toUpperCase() }))} />
              </div>
              <div className="grid grid-cols-[0.8fr_1.2fr] gap-2">
                <SelectField label="Operator" value={formValues.operator} options={[">=", "<=", ">", "<", "=="]} onChange={(value) => setFormValues((current) => ({ ...current, operator: operatorValue(value) }))} />
                <TextField label="Trigger" value={formValues.rhs_constant} onChange={(value) => setFormValues((current) => ({ ...current, rhs_constant: value }))} />
              </div>
              <button
                className="inline-flex items-center justify-center gap-2 rounded-md border border-terminal-line bg-terminal-panel-alt px-3 py-2 text-sm font-semibold text-terminal-ink hover:bg-terminal-hover"
                type="button"
                disabled={pendingAction !== null}
                onClick={submitForm}
              >
                <Save className={`h-4 w-4 ${pendingAction === "create" || pendingAction === "modify" ? "animate-pulse text-terminal-near" : ""}`} />
                {pendingAction === "modify" ? "Modifying..." : pendingAction === "create" ? "Creating..." : editingUuid ? "Modify" : "Create"}
              </button>
              <p className="text-xs leading-5 text-terminal-muted">Phase 1 supports simple constant-price alerts. ATO alerts are visible in the table but should be edited in Kite until basket support is added.</p>
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}

function HeaderCell({ children, align = "left", className = "" }: { children?: ReactNode; align?: "left" | "right"; className?: string }) {
  return <th className={`whitespace-nowrap px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${className}`}>{children}</th>;
}

function SortableHeader({
  children,
  sortKey,
  activeSortKey,
  direction,
  align = "left",
  className = "",
  onSort,
}: {
  children: ReactNode;
  sortKey: SortKey;
  activeSortKey: SortKey;
  direction: SortDirection;
  align?: "left" | "right";
  className?: string;
  onSort: (sortKey: SortKey) => void;
}) {
  return (
    <th className={`whitespace-nowrap px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${className}`}>
      <button className={`inline-flex w-full items-center gap-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted hover:text-terminal-ink ${align === "right" ? "justify-end" : "justify-start"}`} type="button" onClick={() => onSort(sortKey)}>
        <span>{children}</span>
        {activeSortKey === sortKey ? <span aria-hidden="true">{direction === "asc" ? "^" : "v"}</span> : null}
      </button>
    </th>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted">
      <span>{label}</span>
      <input className="rounded-md border border-terminal-line bg-terminal-panel-alt px-2 py-2 text-sm font-semibold normal-case tracking-normal text-terminal-ink outline-none focus:border-terminal-watch" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-terminal-muted">
      <span>{label}</span>
      <select className="rounded-md border border-terminal-line bg-terminal-panel-alt px-2 py-2 text-sm font-semibold normal-case tracking-normal text-terminal-ink outline-none focus:border-terminal-watch" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  );
}

function compareAlerts(left: KiteAlert, right: KiteAlert, sortKey: SortKey, direction: SortDirection) {
  const multiplier = direction === "asc" ? 1 : -1;
  if (sortKey === "status") return (statusRank(left.status) - statusRank(right.status)) * multiplier;
  if (sortKey === "ltp") {
    return (numericValue(left[sortKey]) - numericValue(right[sortKey])) * multiplier;
  }
  const leftValue = sortKey === "symbol" ? left.lhs_tradingsymbol : String(left[sortKey] ?? "");
  const rightValue = sortKey === "symbol" ? right.lhs_tradingsymbol : String(right[sortKey] ?? "");
  return leftValue.localeCompare(rightValue) * multiplier;
}

function dedupeAlertsByUuid(alerts: KiteAlert[]) {
  const seenKeys = new Set<string>();
  return alerts.filter((alert) => {
    const dedupeKey = alertDedupeKey(alert);
    if (seenKeys.has(dedupeKey)) return false;
    seenKeys.add(dedupeKey);
    return true;
  });
}

function alertDedupeKey(alert: KiteAlert) {
  const uuid = String(alert.uuid ?? "").trim();
  if (uuid) return `uuid:${uuid}`;
  return [
    alert.name,
    alert.status,
    alert.lhs_exchange,
    alert.lhs_tradingsymbol,
    alert.lhs_attribute,
    alert.operator,
    alert.rhs_type,
    alert.rhs_constant,
    alert.rhs_exchange,
    alert.rhs_tradingsymbol,
    alert.rhs_attribute,
    alert.price_context,
  ]
    .map((value) => String(value ?? "").trim().toLowerCase())
    .join("|");
}

function matchesStatusFilter(alert: KiteAlert, statusFilter: AlertStatusFilter) {
  const status = String(alert.status ?? "").toLowerCase().trim();
  if (statusFilter === "active") return status === "enabled" || status === "disabled";
  return status === statusFilter;
}

function matchesSearch(alert: KiteAlert, searchText: string) {
  const query = searchText.trim().toLowerCase();
  if (!query) return true;
  return [
    alert.uuid,
    alert.name,
    alert.status,
    alert.lhs_exchange,
    alert.lhs_tradingsymbol,
    alert.operator,
    alert.rhs_type,
    alert.rhs_constant,
    alert.rhs_exchange,
    alert.rhs_tradingsymbol,
    alert.rhs_attribute,
  ]
    .map((value) => String(value ?? "").toLowerCase())
    .some((value) => value.includes(query));
}

function statusRank(status: string) {
  if (status === "disabled") return 0;
  if (status === "enabled") return 1;
  if (status === "deleted") return 2;
  return 3;
}

function numericValue(value: unknown) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : Number.NEGATIVE_INFINITY;
}

function statusClass(status: string) {
  if (status === "enabled") return "text-terminal-entry";
  if (status === "disabled") return "text-terminal-near";
  if (status === "deleted") return "text-terminal-avoid";
  return "text-terminal-muted";
}

function formatTrigger(value: unknown) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? formatPrice(numberValue) : String(value ?? "-");
}

function triggerText(alert: KiteAlert) {
  return `${alert.operator} ${alert.rhs_type === "constant" ? formatTrigger(alert.rhs_constant) : alert.rhs_tradingsymbol || "-"}`;
}

function formatUpdatedDate(value: string | undefined) {
  if (!value) return "-";
  const datePart = value.split(/[T ]/, 1)[0];
  return datePart || value;
}

function operatorValue(value: string): AlertFormValues["operator"] {
  if (value === "<=" || value === ">=" || value === "<" || value === ">" || value === "==") return value;
  return ">=";
}
