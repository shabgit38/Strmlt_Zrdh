import type { PortfolioSnapshot } from "../types";

export async function loadPortfolioSnapshot(): Promise<PortfolioSnapshot> {
  const response = await fetch("/portfolio_snapshot.json", { cache: "no-store" });
  if (!response.ok) {
    let detail = `Failed to load portfolio snapshot: ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Keep the status-based error if the backend did not return JSON.
    }
    throw new Error(detail);
  }
  return response.json();
}
