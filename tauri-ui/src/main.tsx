import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "./workbench-appearance.css";
import "./operation-motion.css";
import { installPersistentZoom } from "./lib/persistent-zoom";

installPersistentZoom();

// Suppress the WebView's browser menu throughout the operator interface.
window.addEventListener("contextmenu", (event) => event.preventDefault(), { capture: true });

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <App />,
);
