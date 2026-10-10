"""Create a generic integrated-circuit application icon at build time."""
from pathlib import Path


def prepare():
    # Build-only dependency; Pillow is excluded from the customer EXE.
    from PIL import Image, ImageDraw
    target = Path(__file__).resolve().parent / "resources"
    target.mkdir(exist_ok=True)
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 48, 976, 976), radius=190, fill="#244a9e")
    for offset in (320, 448, 576, 704):
        for pin in ((offset-22, 188, offset+22, 300), (offset-22, 724, offset+22, 836), (188, offset-22, 300, offset+22), (724, offset-22, 836, offset+22)):
            draw.rounded_rectangle(pin, radius=16, fill="#c8d9fa")
    draw.rounded_rectangle((280, 280, 744, 744), radius=65, fill="#ffffff")
    draw.rounded_rectangle((348, 348, 676, 676), radius=30, fill="#244a9e")
    draw.ellipse((378, 378, 430, 430), fill="#ffffff")
    image.save(target / "controller.ico", sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)])


if __name__ == "__main__":
    prepare()
