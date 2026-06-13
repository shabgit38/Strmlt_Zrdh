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

const root = ReactDOM.createRoot(document.getElementById("root")!);

function renderApp(snapshot?: PortfolioSnapshot | null, streamlitMode = false) {
  root.render(
    <React.StrictMode>
      <App streamlitSnapshot={snapshot ?? null} streamlitMode={streamlitMode} />
    </React.StrictMode>,
  );
  window.setTimeout(() => setStreamlitFrameHeight(), 50);
}

if (isStreamlitComponent()) {
  subscribeToStreamlitRender((args) => {
    renderApp(args.snapshot as PortfolioSnapshot, true);
  });
} else {
  renderApp(null, false);
}
