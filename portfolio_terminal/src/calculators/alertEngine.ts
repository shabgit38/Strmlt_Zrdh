export type AlertTone = "normal" | "review" | "warning" | "exit" | "hardExit";

export type AlertResult = {
  label: string;
  tone: AlertTone;
};

export function generateExitAlert({
  entryPrice,
  currentLtp,
  dte,
  isOtm,
  thesisValid = true,
}: {
  entryPrice: number | null;
  currentLtp: number | null;
  dte: number | null;
  isOtm: boolean;
  thesisValid?: boolean;
}): AlertResult {
  const alerts: AlertResult[] = [];
  const hasPremiumValues =
    entryPrice !== null &&
    entryPrice > 0 &&
    Number.isFinite(entryPrice) &&
    currentLtp !== null &&
    Number.isFinite(currentLtp);
  const premiumChangePct = hasPremiumValues ? ((currentLtp - entryPrice) / entryPrice) * 100 : null;

  if (premiumChangePct !== null) {
    const lossPct = -premiumChangePct;
    if (lossPct >= 50) alerts.push({ label: "Exit: Premium down 50%", tone: "exit" });
    else if (lossPct >= 35) alerts.push({ label: "Warning: Premium down 35%", tone: "warning" });
    else if (lossPct >= 25) alerts.push({ label: "Review: Premium down 25%", tone: "review" });
  }

  if (dte !== null && isOtm && dte <= 10) alerts.push({ label: "Hard Exit: OTM <10 DTE", tone: "hardExit" });
  else if (dte !== null && isOtm && dte <= 15) alerts.push({ label: "Exit Risk: OTM <15 DTE", tone: "exit" });

  if (!thesisValid) alerts.push({ label: "Exit: Thesis invalidated", tone: "exit" });

  return {
    label: formatPremiumChange(premiumChangePct),
    tone: alerts[0]?.tone ?? "normal",
  };
}

function formatPremiumChange(changePct: number | null): string {
  if (changePct === null) return "-";
  if (changePct > 0) return `↑ ${changePct.toFixed(2)}%`;
  if (changePct < 0) return `↓ ${Math.abs(changePct).toFixed(2)}%`;
  return "0.00%";
}
