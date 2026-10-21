"""Генератор баннера главного меню (Pillow → PNG)."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.config import get_settings
from app.services.fonts import find_font

WIDTH = 1024
HEIGHT = 512

ACCENT = (124, 92, 255)
ACCENT_2 = (0, 214, 178)
BG_TOP = (12, 14, 24)
BG_BOTTOM = (26, 20, 48)


def _gradient() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / max(HEIGHT - 1, 1)
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    return image


def _decorations(image: Image.Image, rng: random.Random) -> Image.Image:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Мягкие цветные пятна.
    for _ in range(9):
        color = ACCENT if rng.random() < 0.6 else ACCENT_2
        x = rng.randint(-100, WIDTH)
        y = rng.randint(-80, HEIGHT)
        radius = rng.randint(90, 320)
        alpha = rng.randint(25, 70)
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=(*color, alpha),
        )
    layer = layer.filter(ImageFilter.GaussianBlur(radius=60))
    image = Image.alpha_composite(image.convert("RGBA"), layer)

    # Тонкая сетка — «монтажная» фактура.
    grid = Image.new("RGBA", image.size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    for x in range(0, WIDTH, 64):
        grid_draw.line([(x, 0), (x, HEIGHT)], fill=(255, 255, 255, 12))
    for y in range(0, HEIGHT, 64):
        grid_draw.line([(0, y), (WIDTH, y)], fill=(255, 255, 255, 12))
    image = Image.alpha_composite(image, grid)
    return image


def _play_badge(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int) -> None:
    cx, cy = center
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(32, 28, 62),
        outline=ACCENT,
        width=5,
    )
    size = radius // 2
    draw.polygon(
        [
            (cx - size // 2, cy - size),
            (cx - size // 2, cy + size),
            (cx + size, cy),
        ],
        fill=(255, 255, 255),
    )


def build_banner(path: Path, title: str, tagline: str) -> Path:
    """Нарисовать баннер и сохранить в path."""
    rng = random.Random(7)
    image = _gradient()
    image = _decorations(image, rng)

    draw = ImageDraw.Draw(image)
    title_font = find_font(92, bold=True)
    tag_font = find_font(34, bold=False)
    small_font = find_font(24, bold=True)

    _play_badge(draw, (WIDTH - 150, 130), 62)

    draw.text((80, 150), title, font=title_font, fill=(255, 255, 255, 255))
    draw.text((80, 268), tagline, font=tag_font, fill=(215, 210, 235, 255))

    # Подсветка акцентом.
    draw.rounded_rectangle([80, 330, 80 + 260, 338], radius=4, fill=(*ACCENT, 255))

    draw.text(
        (80, 372),
        "HTML → MP4  ·  GPT  ·  Claude  ·  Gemini  ·  ElevenLabs  ·  HyperFrames",
        font=small_font,
        fill=(170, 165, 200, 255),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG", optimize=True)
    return path


def get_banner() -> Path:
    """Баннер (с кэшем на диске)."""
    settings = get_settings()
    target = settings.cache_dir / "banner.png"
    if target.exists() and target.stat().st_mtime > 0:
        return target
    return build_banner(target, "VIDEO TG", "Нейросеть монтирует видео по вашему тексту")
