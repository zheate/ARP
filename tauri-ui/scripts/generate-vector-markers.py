"""Trace approved character contours without changing crops, poses or anchors.

Development-only tool: Pillow and vtracer 0.6.12. Product uses generated SVGs.
python tauri-ui/scripts/generate-vector-markers.py --tracer-path output/tools/svg-tracing
"""
import argparse
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--tracer-path", type=Path)
args = parser.parse_args()
if args.tracer_path:
    sys.path.insert(0, str(args.tracer_path.resolve()))
import vtracer

base = Path(__file__).resolve().parents[1] / "public" / "chart-markers"
namespace = "http://www.w3.org/2000/svg"
ET.register_namespace("", namespace)
tag = lambda name: f"{{{namespace}}}{name}"
dino_frames = [(8, 496, 310, 310), (320, 496, 310, 310), (636, 496, 310, 310), (947, 496, 310, 310)]
characters = {
    "bee": ("bee-flap-four-frames-97043142efa1.png", [(32, 72, 600, 600), (614, 80, 600, 600), (25, 671, 600, 600), (614, 672, 600, 600)]),
    "green-dinosaur": ("dino-walk-four-frames-9f95d27db3c1.png", dino_frames),
    "dinosaur": ("dino-red-tail-flame-aligned-four-frames-b7fb24b7b24a.png", dino_frames),
}

def save_strip(name, frames, temp):
    size = frames[0].width
    root = ET.Element(tag("svg"), {"viewBox": f"0 0 {size * 4} {size}", "width": str(size * 4), "height": str(size)})
    root.append(ET.Comment(" Approved source contours; VTracer 0.6.12, polygon mode. No embedded raster. "))
    for index, image in enumerate(frames):
        source, target = temp / "frame.png", temp / "frame.svg"
        # The tracer treats partial-alpha RGB as solid colour. Remove the
        # invisible source fringe before tracing; SVG antialiases its contour.
        image = image.copy()
        image.putalpha(image.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0))
        image.save(source)
        vtracer.convert_image_to_svg_py(str(source), str(target), colormode="color", hierarchical="stacked", mode="polygon", filter_speckle=4, color_precision=5, layer_difference=8, path_precision=2)
        cell = ET.SubElement(root, tag("svg"), {"x": str(index * size), "width": str(size), "height": str(size), "viewBox": f"0 0 {size} {size}"})
        cell.extend(ET.parse(target).getroot())
    path = base / f"{name}-vector.svg"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    print(path.name, path.stat().st_size)

with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    for style, (filename, crops) in characters.items():
        source = Image.open(base / filename).convert("RGBA")
        frames = []
        for index, (x, y, width, height) in enumerate(crops):
            image = source.crop((x, y, x + width, y + height))
            if style == "bee":
                # Same source-cell clipping as the approved Canvas renderer.
                left, top = (index % 2) * 627, (index // 2) * 627
                cell = Image.new("RGBA", source.size)
                cell.paste(source.crop((left, top, left + 627, top + 627)), (left, top))
                image = cell.crop((x, y, x + width, y + height))
            frames.append(image)
        if style == "dinosaur":
            flames, bodies = [], []
            for image in frames:
                flame = Image.new("RGBA", image.size)
                flame.paste(image.crop((0, 0, 76, 169)), (0, 0))
                body = image.copy()
                body.paste((0, 0, 0, 0), (0, 0, 76, 169))
                flames.append(flame)
                bodies.append(body)
            save_strip("dinosaur-flame", flames, temp)
            frames = bodies
        save_strip(style, frames, temp)
