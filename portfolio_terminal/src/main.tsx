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

const root = ReactDOM.createRoot(document.getElementById("root")!);
type Screen = "portfolio" | "calculators";

function renderApp(
  snapshot?: PortfolioSnapshot | null,
  streamlitMode = false,
  screen: Screen = "portfolio",
  liveData?: CalculatorsLiveData | null,
) {
  root.render(
    <React.StrictMode>
      <App streamlitSnapshot={snapshot ?? null} streamlitMode={streamlitMode} screen={screen} liveData={liveData ?? null} />
    </React.StrictMode>,
  );
  window.setTimeout(() => setStreamlitFrameHeight(), 50);
}

if (isStreamlitComponent()) {
  subscribeToStreamlitRender((args) => {
    renderApp(
      args.snapshot as PortfolioSnapshot,
      true,
      args.screen === "calculators" ? "calculators" : "portfolio",
      args.liveData as CalculatorsLiveData,
    );
  });
} else {
  renderApp(null, false);
}
