export function formatMoney(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatPrice(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPct(value: number): string {
  return `${value.toFixed(2)}%`;
}

export function signedClass(value: number): string {
  if (value > 0) return "text-terminal-entry";
  if (value < 0) return "text-terminal-avoid";
  return "text-terminal-muted";
}
