import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "./workbench-appearance.css";
import "./components/data-browser.css";
import "./components/shipping-report.css";
import { installPersistentZoom } from "./lib/persistent-zoom";

installPersistentZoom();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <App />,
);
