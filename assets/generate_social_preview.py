from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


WIDTH = 1280
HEIGHT = 640
OUT = Path(__file__).with_name("sessionanchor-social-preview.png")


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts") / name.lower(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def vertical_gradient(width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def draw_grid(draw: ImageDraw.ImageDraw) -> None:
    line_color = (255, 255, 255, 18)
    for x in range(80, WIDTH, 80):
        draw.line([(x, 0), (x, HEIGHT)], fill=line_color, width=1)
    for y in range(80, HEIGHT, 80):
        draw.line([(0, y), (WIDTH, y)], fill=line_color, width=1)


def text(draw: ImageDraw.ImageDraw, position: tuple[int, int], value: str, font, fill) -> tuple[int, int, int, int]:
    draw.text(position, value, font=font, fill=fill)
    return draw.textbbox(position, value, font=font)


def chip(draw: ImageDraw.ImageDraw, position: tuple[int, int], value: str, font) -> int:
    x, y = position
    bbox = draw.textbbox((0, 0), value, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    padding_x = 16
    padding_y = 10
    draw.rounded_rectangle(
        (x, y, x + width + padding_x * 2, y + height + padding_y * 2),
        radius=18,
        fill=(18, 31, 45, 220),
        outline=(110, 190, 255, 140),
        width=2,
    )
    draw.text((x + padding_x, y + padding_y - 1), value, font=font, fill=(231, 243, 255))
    return x + width + padding_x * 2 + 14


def main() -> None:
    image = vertical_gradient(WIDTH, HEIGHT, (7, 15, 27), (13, 34, 54)).convert("RGBA")

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((800, -120, 1320, 380), fill=(27, 173, 255, 72))
    glow_draw.ellipse((-160, 360, 300, 820), fill=(255, 163, 60, 40))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    image.alpha_composite(glow)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw_grid(draw)

    panel = (78, 64, 1202, 576)
    draw.rounded_rectangle(panel, radius=30, fill=(8, 16, 27, 196), outline=(95, 165, 220, 90), width=2)

    mono = load_font("consola.ttf", 28)
    label = load_font("arial.ttf", 26)
    title_font = load_font("arialbd.ttf", 86)
    subtitle_font = load_font("arial.ttf", 34)
    chip_font = load_font("arial.ttf", 24)

    text(draw, (122, 106), "sessionanchor init", mono, (160, 223, 255))
    text(draw, (122, 156), "Context continuity for Claude Code", label, (255, 195, 120))
    text(draw, (122, 228), "SessionAnchor", title_font, (245, 249, 255))
    text(draw, (122, 336), "Local SQLite memory with tiered retrieval,", subtitle_font, (214, 227, 241))
    text(draw, (122, 382), "automatic compaction, and measurable token savings.", subtitle_font, (214, 227, 241))

    next_x = 122
    next_x = chip(draw, (next_x, 470), "L0/L1/L2 memory", chip_font)
    next_x = chip(draw, (next_x, 470), "Zero dependencies", chip_font)
    chip(draw, (next_x, 470), "PyPI: sessionanchor", chip_font)

    code_box = (858, 128, 1136, 470)
    draw.rounded_rectangle(code_box, radius=24, fill=(6, 12, 20, 220), outline=(90, 154, 214, 90), width=2)
    code_lines = [
        ("pip install", (115, 203, 255)),
        ("sessionanchor", (244, 248, 255)),
        ("", (0, 0, 0)),
        ("sessionanchor init", (255, 195, 120)),
        ("sessionanchor boot", (255, 195, 120)),
        ("sessionanchor query \"auth\"", (255, 195, 120)),
    ]
    y = 164
    for line, color in code_lines:
        if not line:
            y += 18
            continue
        text(draw, (892, y), line, mono, color)
        y += 46

    image.alpha_composite(overlay)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(OUT, format="PNG", optimize=True)
    print(OUT)


if __name__ == "__main__":
    main()
