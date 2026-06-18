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

  if (entryPrice !== null && entryPrice > 0 && currentLtp !== null) {
    const lossPct = ((entryPrice - currentLtp) / entryPrice) * 100;
    if (lossPct >= 50) alerts.push({ label: "Exit: Premium down 50%", tone: "exit" });
    else if (lossPct >= 35) alerts.push({ label: "Warning: Premium down 35%", tone: "warning" });
    else if (lossPct >= 25) alerts.push({ label: "Review: Premium down 25%", tone: "review" });
  }

  if (dte !== null && isOtm && dte <= 10) alerts.push({ label: "Hard Exit: OTM <10 DTE", tone: "hardExit" });
  else if (dte !== null && isOtm && dte <= 15) alerts.push({ label: "Exit Risk: OTM <15 DTE", tone: "exit" });

  if (!thesisValid) alerts.push({ label: "Exit: Thesis invalidated", tone: "exit" });

  return alerts[0] ?? { label: "Normal", tone: "normal" };
}
