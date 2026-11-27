"""Красивая карточка товара (PNG 1200x630) для пересылки: фото, название, цена, бренд."""
import io
import os

from config import BASE_DIR

BURGUNDY, CREAM, DARK = (128, 0, 32), (255, 245, 225), (60, 8, 20)
_FONTS = BASE_DIR / "static" / "fonts"
W, H = 1200, 630


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    path = _FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,") + "…"
    return lines


def render_card(photo: bytes | None, title: str, price: str, meta: str, brand: str | None) -> bytes:
    from PIL import Image, ImageDraw, ImageOps
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)
    left = 560
    if photo:
        try:
            ph = Image.open(io.BytesIO(photo)).convert("RGB")
            ph = ImageOps.fit(ph, (left, H), method=Image.LANCZOS)
            img.paste(ph, (0, 0))
        except Exception:
            photo = None
    if not photo:
        d.rectangle((0, 0, left, H), fill=BURGUNDY)
        d.text((left // 2, H // 2), "ORIENT", font=_font(72, True), fill=CREAM, anchor="mm")
    x, maxw = left + 60, W - left - 110
    d.text((x, 56), "ORIENT", font=_font(30, True), fill=BURGUNDY)
    y = 140
    if brand:
        d.text((x, y), brand.upper(), font=_font(26, True), fill=(150, 90, 100))
        y += 46
    tf = _font(48, True)
    for line in _wrap(d, title, tf, maxw, 3):
        d.text((x, y), line, font=tf, fill=DARK)
        y += 62
    d.text((x, H - 190), price, font=_font(64, True), fill=BURGUNDY)
    if meta:
        d.text((x, H - 112), meta, font=_font(26), fill=(120, 80, 88))
    d.rounded_rectangle((x, H - 76, x + 330, H - 24), radius=26, fill=BURGUNDY)
    d.text((x + 165, H - 50), "Открыть в Orient", font=_font(24, True), fill=CREAM, anchor="mm")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
