import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createServer } from "vite";

const vite = await createServer({
  appType: "custom",
  logLevel: "error",
  server: { middlewareMode: true },
});
const { calculateOptionRows } = await vite.ssrLoadModule("/src/calculators/logic.ts");

after(async () => {
  await vite.close();
});

test("recognized option symbols override stale saved contract metadata", () => {
  const [row] = calculateOptionRows([
    {
      id: "option-test",
      symbol: "NIFTY27MAR21000PE",
      openQty: "65",
      avgPrice: "215.30",
      ltp: "215.30",
      spot: "22742",
      expiry: "2026-12-29",
      strike: "22500",
      optionType: "CE",
      exitPrice: "",
      avgSelected: false,
    },
  ]);

  assert.equal(row.expiry, "2027-03-30");
  assert.equal(row.strike, "21000");
  assert.equal(row.optionType, "PE");
  assert.equal(row.breakeven, 20784.7);
  const today = new Date();
  const expectedDte = Math.round(
    (Date.UTC(2027, 2, 30) -
      Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) /
      86400000,
  );
  assert.equal(row.daysExpiry, expectedDte);
  assert.equal(row.symbolError, null);
});

test("unparseable option symbols clear contract metadata and show an error", () => {
  const [row] = calculateOptionRows([
    {
      id: "option-test",
      symbol: "NIFTY_MARCH_21000_PUT",
      openQty: "65",
      avgPrice: "215.30",
      ltp: "215.30",
      spot: "22742",
      expiry: "2026-12-29",
      strike: "22500",
      optionType: "CE",
      exitPrice: "",
      avgSelected: false,
    },
  ]);

  assert.equal(row.expiry, "");
  assert.equal(row.strike, "");
  assert.equal(row.optionType, "");
  assert.equal(row.daysExpiry, null);
  assert.equal(row.breakeven, null);
  assert.equal(row.distSpot, "");
  assert.equal(row.symbolError, "Symbol can't be parsed");
  assert.equal(row.pnl, 0);
});
