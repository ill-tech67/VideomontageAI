"""Генератор CAPTCHA: 4 буквы-цифры в шуме.

Ключевая идея: ответ капчи живёт только в памяти сервера и в виде HMAC-хеша
в базе. Изображение с капчей никогда не отправляется ни в одну языковую
модель и ни в какой сторонний сервис — поэтому бот (и ИИ внутри него) не
способен «прочитать» символы, даже если получит картинку.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.security.crypto import hash_captcha

# Без похожих символов (0/O, 1/l/I) — иначе люди ошибаются заметно чаще.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

CANVAS = (360, 140)
SCALE = 2

_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/trebucbd.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/georgiab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
)


@dataclass(slots=True)
class Captcha:
    """Результат генерации капчи."""

    image: bytes
    """PNG-байты с искажённой картинкой."""
    code: str
    """Правильный ответ. Только для серверной проверки — наружу не отдаём."""
    code_hash: str
    """HMAC-хеш ответа для хранения в БД."""

    def __post_init__(self) -> None:
        # Страховка от утечки: код не должен случайно оказаться в логах.
        self.code_hash = hash_captcha(self.code)


def _random_font(rng: random.Random) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    available = [p for p in _FONT_CANDIDATES if Path(p).exists()]
    if available:
        try:
            return ImageFont.truetype(rng.choice(available), size=rng.randint(48, 62))
        except OSError:
            pass
    for directory in _FONT_DIRS:
        if not directory.exists():
            continue
        fonts = sorted(directory.glob("*.ttf"))
        if fonts:
            try:
                return ImageFont.truetype(str(rng.choice(fonts)), size=rng.randint(48, 62))
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=rng.randint(46, 58))
    except TypeError:
        return ImageFont.load_default()


def _random_background(rng: random.Random, size: tuple[int, int]) -> Image.Image:
    """Пятнистый градиент, который не даёт выровнять фон под текст."""
    width, height = size
    base = Image.new("RGB", size, (12, 14, 22))
    draw = ImageDraw.Draw(base)
    top = (rng.randint(18, 60), rng.randint(18, 60), rng.randint(30, 80))
    bottom = (rng.randint(40, 110), rng.randint(30, 90), rng.randint(50, 130))
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    for _ in range(rng.randint(8, 16)):
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        radius = rng.randint(40, 180)
        shade = tuple(rng.randint(0, 120) for _ in range(3))
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=shade)
    return base.filter(ImageFilter.GaussianBlur(radius=rng.uniform(6, 14)))


def _draw_char(
    rng: random.Random,
    char: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> Image.Image:
    """Отрисовать один символ со случайным поворотом и цветом."""
    pad = 26
    box = font.getbbox(char)
    width = int(max(box[2] - box[0], 1) + pad * 2)
    height = int(max(box[3] - box[1], 1) + pad * 2)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    color = (
        rng.randint(190, 255),
        rng.randint(190, 255),
        rng.randint(190, 255),
        255,
    )
    # Разные начертания «подложки» под символом делают OCR заметно сложнее.
    draw.text((pad, pad), char, font=font, fill=color, stroke_width=rng.randint(0, 2),
              stroke_fill=(255, 255, 255, 90))

    # Сдвиг (shear) по горизонтали — символ становится «волнистым».
    shear = rng.uniform(-0.28, 0.28)
    layer = layer.transform(
        layer.size,
        Image.Transform.AFFINE,
        (1, shear, -shear * height * 0.5, 0, 1, 0),
        resample=Image.Resampling.BICUBIC,
    )

    angle = rng.uniform(-26, 26)
    return layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)


def _add_noise(
    rng: random.Random,
    image: Image.Image,
    code: str,
) -> Image.Image:
    """Наложить шум поверх символов: линии, дуги, точки, пятна."""
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for _ in range(rng.randint(4, 7)):
        color = (
            rng.randint(0, 255),
            rng.randint(0, 255),
            rng.randint(0, 255),
            rng.randint(70, 190),
        )
        width_px = rng.randint(1, 4)
        if rng.random() < 0.5:
            x1 = rng.randint(-40, width)
            y1 = rng.randint(0, height)
            x2 = rng.randint(-40, width)
            y2 = rng.randint(0, height)
            draw.line([(x1, y1), (x2, y2)], fill=color, width=width_px)
        else:
            x1, x2 = sorted((rng.randint(-40, width), rng.randint(-40, width)))
            y1, y2 = sorted((rng.randint(-30, height), rng.randint(-30, height)))
            start = rng.randint(0, 300)
            draw.arc(
                [x1, y1, x2, y2],
                start=start,
                end=start + rng.randint(60, 260),
                fill=color,
                width=width_px,
            )

    for _ in range(rng.randint(240, 520)):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255),
                 rng.randint(40, 170))
        radius = rng.choice((1, 1, 1, 2))
        draw.ellipse([x, y, x + radius, y + radius], fill=color)

    for _ in range(rng.randint(2, 4)):
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255),
                 rng.randint(35, 90))
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        size = rng.randint(30, 110)
        draw.ellipse([x, y, x + size, y + size // 2], fill=color)

    # Штриховка поверх — «мёртвые» пиксели вроде отсканированного документа.
    for _ in range(rng.randint(60, 140)):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        length = rng.randint(2, 9)
        draw.line([(x, y), (x + length, y)], fill=(255, 255, 255, rng.randint(20, 70)), width=1)

    noisy = Image.alpha_composite(image.convert("RGBA"), overlay)

    # Лёгкое размытие и повторное «солирование» — финальный барьер для OCR.
    noisy = noisy.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 0.9)))
    speckle = Image.new("RGBA", noisy.size, (0, 0, 0, 0))
    speckle_draw = ImageDraw.Draw(speckle)
    for _ in range(rng.randint(300, 700)):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        shade = rng.randint(0, 255)
        speckle_draw.point((x, y), fill=(shade, shade, shade, rng.randint(25, 120)))
    noisy = Image.alpha_composite(noisy, speckle)

    # Лёгкая JPEG-подобная деградация: перекодируем через JPEG и обратно.
    buffer = io.BytesIO()
    noisy.convert("RGB").save(buffer, format="JPEG", quality=rng.randint(45, 70))
    buffer.seek(0)
    degraded = Image.open(buffer).convert("RGB")
    _ = code  # код не используется: он не должен влиять на картинку
    return degraded


def generate_captcha(length: int = 4, seed: int | None = None) -> Captcha:
    """Сгенерировать капчу.

    Возвращает изображение, правильный ответ и его хеш. Ответ предназначен
    исключительно для серверной сверки и не должен попадать в сообщения,
    логи или запросы к внешним сервисам.
    """
    rng = random.Random(seed)
    code = "".join(rng.choice(ALPHABET) for _ in range(length))

    width, height = CANVAS[0] * SCALE, CANVAS[1] * SCALE
    background = _random_background(rng, (width, height)).convert("RGBA")

    font = _random_font(rng)
    layers = [_draw_char(rng, char, font) for char in code]

    spacing = int(width * 0.012)
    total_width = sum(layer.width for layer in layers) + spacing * (len(layers) - 1)
    x = max((width - total_width) // 2, 0)
    baseline = height // 2

    for layer in layers:
        jitter_y = rng.randint(-18, 18)
        background.alpha_composite(layer, (x, baseline - layer.height // 2 + jitter_y))
        x += layer.width + spacing

    final = _add_noise(rng, background, code)

    buffer = io.BytesIO()
    final.save(buffer, format="PNG", optimize=True)
    return Captcha(image=buffer.getvalue(), code=code, code_hash=hash_captcha(code))
