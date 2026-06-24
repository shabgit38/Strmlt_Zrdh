import { Edit3, RefreshCw, Save, Trash2, X } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { formatPrice } from "../format";
import { setStreamlitComponentValue, setStreamlitFrameHeight } from "../streamlitBridge";
import type { AlertFormValues, AlertsData, AlertStatusFilter, KiteAlert } from "../alerts/types";

type SortKey = "status" | "symbol" | "name" | "ltp" | "alert_count" | "updated_at";
type SortDirection = "asc" | "desc";

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
  const [statusFilter, setStatusFilter] = useState<AlertStatusFilter>(data?.statusFilter ?? "all");
  const [sortKey, setSortKey] = useState<SortKey>("status");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [editingUuid, setEditingUuid] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<AlertFormValues>(EMPTY_FORM);

  useEffect(() => {
    setStatusFilter(data?.statusFilter ?? "all");
  }, [data?.statusFilter]);

  useEffect(() => {
    window.setTimeout(() => setStreamlitFrameHeight(), 50);
  }, [data, editingUuid, formValues]);

  const alerts = data?.alerts ?? [];
  const sortedAlerts = useMemo(
    () => [...alerts].sort((left, right) => compareAlerts(left, right, sortKey, sortDirection)),
    [alerts, sortKey, sortDirection],
  );

  function sendAction(action: "fetch" | "create" | "modify" | "delete", payload: Record<string, unknown> = {}) {
    setStreamlitComponentValue({
      type: "alerts",
      action,
      requestId: `${Date.now()}-${action}`,
      statusFilter,
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
        <section className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-terminal-muted">Alerts</h2>
            <div className="mt-1 text-xs text-terminal-muted">
              {data?.loaded ? `${alerts.length} alert${alerts.length === 1 ? "" : "s"} loaded` : "Fetch alerts to load Kite data"}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="rounded-md border border-terminal-line bg-terminal-panel px-3 py-2 text-sm font-semibold text-terminal-ink outline-none"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as AlertStatusFilter)}
            >
              <option value="all">All</option>
              <option value="disabled">Disabled</option>
              <option value="enabled">Enabled</option>
              <option value="deleted">Deleted</option>
            </select>
            <button
              className="inline-flex items-center gap-2 rounded-md border border-terminal-line px-3 py-2 text-sm font-semibold text-terminal-ink hover:bg-terminal-hover"
              type="button"
              onClick={() => sendAction("fetch")}
            >
              <RefreshCw className="h-4 w-4" />
              Get alerts
            </button>
          </div>
        </section>

        {data?.error ? <div className="rounded-md border border-terminal-avoid bg-terminal-panel p-3 text-sm font-semibold text-terminal-avoid">{data.error}</div> : null}
        {data?.message ? <div className="rounded-md border border-terminal-line bg-terminal-panel p-3 text-sm font-semibold text-terminal-entry">{data.message}</div> : null}
        {data?.debug ? (
          <div className="rounded-md border border-terminal-line bg-terminal-panel p-3 text-xs text-terminal-muted">
            <span className="font-semibold text-terminal-ink">{data.debug}</span>
            {data.fetchMeta?.pages?.length ? (
              <span className="ml-2">
                Pages: {data.fetchMeta.pages.map((page) => `${page.page}:${page.count}`).join(", ")}
              </span>
            ) : null}
          </div>
        ) : null}

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(22rem,0.7fr)]">
          <div className="overflow-auto rounded-md border border-terminal-line bg-terminal-panel">
            <table className="w-full min-w-[1080px] border-collapse text-left text-sm">
              <thead className="bg-terminal-panel-alt text-xs uppercase tracking-wide text-terminal-muted">
                <tr>
                  <SortableHeader sortKey="symbol" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Symbol</SortableHeader>
                  <SortableHeader sortKey="name" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Name</SortableHeader>
                  <HeaderCell align="right">LTP</HeaderCell>
                  <HeaderCell align="right">Trigger</HeaderCell>
                  <SortableHeader sortKey="status" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Status</SortableHeader>
                  <SortableHeader sortKey="alert_count" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort} align="right">Alert Count</SortableHeader>
                  <SortableHeader sortKey="updated_at" activeSortKey={sortKey} direction={sortDirection} onSort={handleSort}>Updated</SortableHeader>
                  <HeaderCell align="right"></HeaderCell>
                </tr>
              </thead>
              <tbody>
                {sortedAlerts.map((alert) => (
                  <tr key={alert.uuid} className={`border-t border-terminal-line ${editingUuid === alert.uuid ? "bg-terminal-selected" : ""}`}>
                    <td className="whitespace-nowrap px-3 py-2 font-semibold text-terminal-ink">{alert.lhs_tradingsymbol}</td>
                    <td className="px-3 py-2 text-terminal-ink">{alert.name}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-terminal-ink">{alert.ltp === null || alert.ltp === undefined ? "-" : formatPrice(Number(alert.ltp))}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-terminal-ink">
                      {alert.operator} {alert.rhs_type === "constant" ? formatTrigger(alert.rhs_constant) : alert.rhs_tradingsymbol || "-"}
                    </td>
                    <td className={`whitespace-nowrap px-3 py-2 font-semibold ${statusClass(alert.status)}`}>{alert.status}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-terminal-ink">{alert.alert_count ?? 0}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-terminal-muted">{alert.updated_at || "-"}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <div className="inline-flex items-center gap-1">
                        <button
                          className="rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover hover:text-terminal-ink disabled:cursor-default disabled:opacity-40"
                          type="button"
                          title={alert.type === "simple" ? "Edit" : "ATO edit disabled in phase 1"}
                          disabled={alert.type !== "simple"}
                          onClick={() => startEdit(alert)}
                        >
                          <Edit3 className="h-4 w-4" />
                        </button>
                        <button className="rounded-md border border-terminal-line p-2 text-terminal-muted hover:bg-terminal-hover hover:text-terminal-avoid" type="button" title="Delete" onClick={() => window.confirm(`Delete alert "${alert.name}"?`) && sendAction("delete", { uuid: alert.uuid })}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {data?.loaded && sortedAlerts.length === 0 ? (
                  <tr className="border-t border-terminal-line">
                    <td className="px-3 py-6 text-sm text-terminal-muted" colSpan={8}>No alerts returned for this filter.</td>
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
                onClick={submitForm}
              >
                <Save className="h-4 w-4" />
                {editingUuid ? "Modify" : "Create"}
              </button>
              <p className="text-xs leading-5 text-terminal-muted">Phase 1 supports simple constant-price alerts. ATO alerts are visible in the table but should be edited in Kite until basket support is added.</p>
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}

function HeaderCell({ children, align = "left" }: { children?: ReactNode; align?: "left" | "right" }) {
  return <th className={`whitespace-nowrap px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>{children}</th>;
}

function SortableHeader({
  children,
  sortKey,
  activeSortKey,
  direction,
  align = "left",
  onSort,
}: {
  children: ReactNode;
  sortKey: SortKey;
  activeSortKey: SortKey;
  direction: SortDirection;
  align?: "left" | "right";
  onSort: (sortKey: SortKey) => void;
}) {
  return (
    <th className={`whitespace-nowrap px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>
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
  if (sortKey === "ltp" || sortKey === "alert_count") {
    return (numericValue(left[sortKey]) - numericValue(right[sortKey])) * multiplier;
  }
  const leftValue = sortKey === "symbol" ? left.lhs_tradingsymbol : String(left[sortKey] ?? "");
  const rightValue = sortKey === "symbol" ? right.lhs_tradingsymbol : String(right[sortKey] ?? "");
  return leftValue.localeCompare(rightValue) * multiplier;
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

function operatorValue(value: string): AlertFormValues["operator"] {
  if (value === "<=" || value === ">=" || value === "<" || value === ">" || value === "==") return value;
  return ">=";
}
