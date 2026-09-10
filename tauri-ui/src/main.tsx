import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "./components/data-browser.css";
import { installPersistentZoom } from "./lib/persistent-zoom";

installPersistentZoom();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <App />,
);
