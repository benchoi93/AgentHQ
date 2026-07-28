import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { startVersionGuard } from "./versionGuard";

// Auto-reload this tab if a newer frontend build gets deployed, so an
// already-open tab can't keep running a stale bundle (see versionGuard.ts).
startVersionGuard();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
