// Isolated product-page fixture; its data provider is supplied by the browser driver.
import { createRoot } from "react-dom/client"
import { DataBrowserPage } from "../../src/components/data-browser-page"
import { AppearanceProvider } from "../../src/hooks/use-appearance"
import "../../src/index.css"
import "../../src/workbench-appearance.css"

createRoot(document.getElementById("root")!).render(
  <AppearanceProvider>
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <DataBrowserPage visible enabled defaultDirectory="" />
    </div>
  </AppearanceProvider>,
)
