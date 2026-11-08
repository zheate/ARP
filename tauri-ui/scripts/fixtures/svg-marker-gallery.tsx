// Visual fixture only: all product markers at native and enlarged sizes.
import { createRoot } from "react-dom/client"
import { CharacterSpriteMarker } from "../../src/components/character-sprite-marker"
import { FlappyBirdMarker } from "../../src/components/flappy-bird-marker"
import { StaticLatestPointMarker } from "../../src/components/static-latest-point-marker"
import { LATEST_POINT_MARKER_OPTIONS, latestPointMarkerSize } from "../../src/lib/appearance"

createRoot(document.getElementById("root")!).render(<main style={{ fontFamily: "sans-serif", padding: 24 }}>
  <h1>SVG 末端光标 · 1× / 2× / 4×</h1>
  {["#202224", "#f7f5ef"].map((background, theme) => <section key={background} style={{ background, color: theme ? "#222" : "#eee", padding: 20, marginBottom: 20 }}>
    {LATEST_POINT_MARKER_OPTIONS.map(({ id, label }) => <div key={id} style={{ display: "grid", gridTemplateColumns: "160px repeat(3, 200px)", alignItems: "center", height: 144 }}>
      <span>{label}</span>
      {[1, 2, 4].map((scale) => {
        const size = latestPointMarkerSize(id) * scale
        const visible = (element: HTMLDivElement | null) => { if (element) element.style.visibility = "visible" }
        return <div key={scale} style={{ position: "relative", width: 160, height: 132 }}>
          {id === "bee" || id === "dinosaur" || id === "green-dinosaur"
            ? <CharacterSpriteMarker style={id} size={size} ref={visible} />
            : id === "flappy-bird" ? <FlappyBirdMarker size={size} ref={visible} />
              : <StaticLatestPointMarker line={{ key: id, label, latestPointStyle: id, color: "#ef792a" }} size={size} ref={visible} />}
        </div>
      })}
    </div>)}
  </section>)}
</main>)
