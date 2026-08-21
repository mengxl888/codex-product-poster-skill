#!/usr/bin/env python3
"""Overlay exact poster copy on a generated image.

This is an optional fallback for cases where an image model renders Chinese
characters incorrectly. It requires Pillow and a font with the needed glyphs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Iterable, List, Optional, Tuple


def fail(message: str) -> "NoReturn":
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_font(explicit: Optional[str]) -> str:
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    env_font = os.getenv("POSTER_FONT")
    if env_font:
        candidates.append(env_font)
    candidates.extend(
        [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    fail(
        "no CJK-capable font found; pass --font or set POSTER_FONT "
        "to a font such as Microsoft YaHei or Noto Sans CJK"
    )


def fit_font(ImageFont, font_path: str, text: str, max_width: int, preferred: int):
    for size in range(max(preferred, 12), 11, -1):
        try:
            font = ImageFont.truetype(font_path, size, index=0)
        except TypeError:
            font = ImageFont.truetype(font_path, size)
        if font.getbbox(text)[2] <= max_width:
            return font
    try:
        return ImageFont.truetype(font_path, 12, index=0)
    except TypeError:
        return ImageFont.truetype(font_path, 12)


def draw_centered(draw, text: str, font, y: int, width: int, fill) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    x = (width - text_width) // 2
    draw.text((x, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Overlay exact copy on a product poster")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title")
    parser.add_argument("--subtitle")
    parser.add_argument("--tagline")
    parser.add_argument("--cta")
    parser.add_argument("--font")
    parser.add_argument("--region", choices=("top", "bottom"), default="bottom")
    parser.add_argument("--cover", action="store_true", help="cover the selected text-safe band first")
    parser.add_argument("--margin", type=int, default=48)
    parser.add_argument("--title-size", type=int, default=56)
    parser.add_argument("--subtitle-size", type=int, default=30)
    parser.add_argument("--tagline-size", type=int, default=26)
    parser.add_argument("--cta-size", type=int, default=24)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not any((args.title, args.subtitle, args.tagline, args.cta)):
        fail("provide at least one of --title, --subtitle, --tagline, or --cta")
    if args.margin < 0:
        fail("--margin must be non-negative")
    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.is_file():
        fail(f"input image not found: {input_path}")
    if output_path.exists() and not args.force:
        fail(f"output already exists: {output_path} (use --force to overwrite)")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        fail("Pillow is required; install it with `python -m pip install Pillow`")

    font_path = find_font(args.font)
    image = Image.open(input_path).convert("RGBA")
    width, height = image.size
    band_height = max(180, min(int(height * 0.30), 360))
    if args.region == "top":
        band = (0, 0, width, band_height)
        y = args.margin
    else:
        band = (0, height - band_height, width, height)
        y = height - band_height + args.margin

    draw = ImageDraw.Draw(image, "RGBA")
    if args.cover:
        draw.rectangle(band, fill=(7, 10, 13, 235))
    max_width = max(80, width - args.margin * 2)
    lines: Iterable[Tuple[Optional[str], int, Tuple[int, int, int, int]]] = (
        (args.title, args.title_size, (248, 248, 248, 255)),
        (args.subtitle, args.subtitle_size, (230, 232, 235, 255)),
        (args.tagline, args.tagline_size, (210, 164, 78, 255)),
        (args.cta, args.cta_size, (210, 164, 78, 255)),
    )
    for text, preferred_size, color in lines:
        if not text:
            continue
        font = fit_font(ImageFont, font_path, text, max_width, preferred_size)
        line_height = draw_centered(draw, text, font, y, width, color)
        y += line_height + max(10, preferred_size // 3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        image.convert("RGB").save(output_path, format="JPEG", quality=95)
    elif output_path.suffix.lower() == ".webp":
        image.save(output_path, format="WEBP", quality=95)
    else:
        image.save(output_path, format="PNG")
    print(f"Wrote {output_path} ({width}x{height}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
