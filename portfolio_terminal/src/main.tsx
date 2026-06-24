import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import {
  isStreamlitComponent,
  setStreamlitFrameHeight,
  subscribeToStreamlitRender,
} from "./streamlitBridge";
import type { PortfolioSnapshot } from "./types";
import type { CalculatorsLiveData } from "./calculators/types";
import type { AlertsData } from "./alerts/types";

const root = ReactDOM.createRoot(document.getElementById("root")!);
type Screen = "portfolio" | "calculators" | "alerts";

function renderApp(
  snapshot?: PortfolioSnapshot | null,
  streamlitMode = false,
  screen: Screen = "portfolio",
  liveData?: CalculatorsLiveData | null,
  alertsData?: AlertsData | null,
) {
  root.render(
    <React.StrictMode>
      <App
        streamlitSnapshot={snapshot ?? null}
        streamlitMode={streamlitMode}
        screen={screen}
        liveData={liveData ?? null}
        alertsData={alertsData ?? null}
      />
    </React.StrictMode>,
  );
  window.setTimeout(() => setStreamlitFrameHeight(), 50);
}

if (isStreamlitComponent()) {
  subscribeToStreamlitRender((args) => {
    renderApp(
      args.snapshot as PortfolioSnapshot,
      true,
      args.screen === "calculators" ? "calculators" : args.screen === "alerts" ? "alerts" : "portfolio",
      args.liveData as CalculatorsLiveData,
      args.alertsData as AlertsData,
    );
  });
} else {
  renderApp(null, false);
}
