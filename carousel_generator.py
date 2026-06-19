import os
import re
import math
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path

FONTS_DIR = Path(__file__).parent / "fonts"

THEMES = {
    # ── 1. Fuchsia — нежный блаш + горячий розовый акцент (основная, как в референсе) ──
    "fuchsia": {
        "bg": "#FBD5E8",
        "text": "#2D0A1E",
        "text_dim": "#B06090",
        "accent": "#E8187A",
        "card_bg": "#F5C0DA",
        "card_border": "#ECA8CA",
        "grid": "#000000",
        "grid_alpha": 0,
        "badge_bg": "#E8187A",
        "badge_text": "#FFFFFF",
        "badge_style": "square",
        "label_color": "#B06090",
        "strikethrough_color": "#DDA0C0",
        "tag_crit_bg": "#E8187A",
        "tag_fixed_bg": "#7A4A7A",
    },
    # ── 2. Hot — горячий розовый фон + белый текст (акцентный слайд) ──
    "hot": {
        "bg": "#E8187A",
        "text": "#FFFFFF",
        "text_dim": "#FFB8D8",
        "accent": "#FFFFFF",
        "card_bg": "#D01068",
        "card_border": "#B80858",
        "grid": "#FFFFFF",
        "grid_alpha": 6,
        "badge_bg": "#FFFFFF",
        "badge_text": "#E8187A",
        "badge_style": "square",
        "label_color": "#FFB8D8",
        "strikethrough_color": "#F080B0",
        "tag_crit_bg": "#FFFFFF",
        "tag_fixed_bg": "#904870",
    },
    # ── 3. Blush — кремово-белый + горячий розовый, минималистик ──
    "blush": {
        "bg": "#FFF5F8",
        "text": "#2D0A1E",
        "text_dim": "#C080A0",
        "accent": "#E8187A",
        "card_bg": "#FFE8F2",
        "card_border": "#F5D0E4",
        "grid": "#000000",
        "grid_alpha": 0,
        "badge_bg": "#2D0A1E",
        "badge_text": "#FFF5F8",
        "badge_style": "circle",
        "label_color": "#C080A0",
        "strikethrough_color": "#E8B0CC",
        "tag_crit_bg": "#E8187A",
        "tag_fixed_bg": "#7A4A7A",
    },
    # ── 4. Pearl — чисто белый + малиновый, самый лёгкий ──
    "pearl": {
        "bg": "#FFFFFF",
        "text": "#2D0A1E",
        "text_dim": "#C898B0",
        "accent": "#C8006A",
        "card_bg": "#FFF0F5",
        "card_border": "#F5D8E8",
        "grid": "#000000",
        "grid_alpha": 0,
        "badge_bg": "#2D0A1E",
        "badge_text": "#FFFFFF",
        "badge_style": "square",
        "label_color": "#C898B0",
        "strikethrough_color": "#E8C0D4",
        "tag_crit_bg": "#C8006A",
        "tag_fixed_bg": "#7A4878",
    },
    # ── 5. Plum — тёмно-сливовый фон + розовый неон ──
    "plum": {
        "bg": "#2D0A1E",
        "text": "#FFE8F2",
        "text_dim": "#C070A0",
        "accent": "#FF5BAA",
        "card_bg": "#3D1228",
        "card_border": "#541A38",
        "grid": "#FFFFFF",
        "grid_alpha": 5,
        "badge_bg": "#FF5BAA",
        "badge_text": "#2D0A1E",
        "badge_style": "square",
        "label_color": "#C070A0",
        "strikethrough_color": "#7A3050",
        "tag_crit_bg": "#FF5BAA",
        "tag_fixed_bg": "#6A3878",
    },
}

W, H = 1080, 1350
PAD = 72


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _norm_text(s):
    """Нормализуем строку для сравнения: только буквы/цифры в нижнем регистре."""
    return re.sub(r"[^0-9a-zA-Zа-яёА-ЯЁ]+", "", str(s).lower())


def _collect_strings(obj):
    """Рекурсивно собираем все строки из visual_data."""
    out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_strings(v))
    return out


def sanitize_text(text):
    """Убираем emoji и спецсимволы которые Inter не рендерит — они дают квадратики."""
    import unicodedata
    result = []
    for ch in str(text):
        cp = ord(ch)
        # пропускаем emoji-диапазоны и прочие pictographic блоки
        if (0x1F300 <= cp <= 0x1FAFF or   # Misc Symbols, Emoji
            0x2600 <= cp <= 0x27BF or      # Misc Symbols, Dingbats
            0xFE00 <= cp <= 0xFE0F or      # Variation Selectors
            0x200B <= cp <= 0x200F or      # Zero-width chars
            cp == 0xFEFF):                 # BOM
            continue
        cat = unicodedata.category(ch)
        if cat.startswith('C') and cat != 'Co':  # Control chars (кроме PUA)
            continue
        result.append(ch)
    return "".join(result)


def dedupe_body(body_lines, visual_data):
    """Убираем из body_lines строки, которые уже показаны внутри визуала."""
    vis_norms = [n for n in (_norm_text(s) for s in _collect_strings(visual_data)) if n]
    kept = []
    for line in body_lines:
        nb = _norm_text(line)
        if not nb:
            kept.append(line)
            continue
        dup = False
        for nv in vis_norms:
            if nb == nv or (len(nb) >= 12 and (nb in nv or nv in nb)):
                dup = True
                break
        if not dup:
            kept.append(line)
    return kept


def load_font(name, size):
    candidates = [
        FONTS_DIR / f"{name}.ttf",
        FONTS_DIR / f"{name}.otf",
    ]
    for p in candidates:
        if p.exists():
            return ImageFont.truetype(str(p), size)

    bold_map = {
        "Inter-Bold": ["Arial Bold", "Helvetica-Bold"],
        "Inter-Regular": ["Arial", "Helvetica"],
        "Inter-Medium": ["Arial", "Helvetica"],
    }
    for sys_name in bold_map.get(name, []):
        for ext in [".ttf", ".otf"]:
            for prefix in ["/Library/Fonts/", "/System/Library/Fonts/Supplemental/"]:
                p = f"{prefix}{sys_name}{ext}"
                if os.path.exists(p):
                    return ImageFont.truetype(p, size)

    return ImageFont.load_default(size=size)


class CarouselGenerator:
    def __init__(self):
        self._font_cache = {}

    def _font(self, name, size):
        key = (name, size)
        if key not in self._font_cache:
            self._font_cache[key] = load_font(name, size)
        return self._font_cache[key]

    def _bold(self, size): return self._font("Inter-Bold", size)
    def _reg(self, size): return self._font("Inter-Regular", size)
    def _med(self, size): return self._font("Inter-Medium", size)

    def _playfair(self, size):
        """Playfair Display — serif для редакционных заголовков."""
        key = ("PlayfairDisplay", size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(str(FONTS_DIR / "PlayfairDisplay.ttf"), size)
            except Exception:
                self._font_cache[key] = self._bold(size)
        return self._font_cache[key]

    def _playfair_italic(self, size):
        """Playfair Display Italic — главный голос заголовков зеленова-стайл."""
        key = ("PlayfairDisplay-Italic", size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(str(FONTS_DIR / "PlayfairDisplay-Italic.ttf"), size)
            except Exception:
                self._font_cache[key] = self._bold(size)
        return self._font_cache[key]

    def _oswald(self, size, weight="Bold"):
        """Узкая дисплейная гарнитура для крупных акцентов (пара к Inter)."""
        key = ("Oswald", size, weight)
        if key not in self._font_cache:
            try:
                f = ImageFont.truetype(str(FONTS_DIR / "Oswald.ttf"), size)
                f.set_variation_by_name(weight)
            except Exception:
                f = self._bold(size)  # запасной вариант
            self._font_cache[key] = f
        return self._font_cache[key]

    def _wrap(self, draw, text, font, max_w):
        words = text.split()
        lines, cur = [], []
        for w in words:
            test = " ".join(cur + [w])
            if self._tw(draw, test, font) > max_w and cur:
                lines.append(" ".join(cur)); cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return lines

    def _draw_grid(self, img, theme):
        t = THEMES[theme]
        if t["grid_alpha"] == 0:
            return
        grid_color = hex_to_rgb(t["grid"]) + (t["grid_alpha"],)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        step = 60
        for x in range(0, W + step, step):
            d.line([(x, 0), (x, H)], fill=grid_color, width=1)
        for y in range(0, H + step, step):
            d.line([(0, y), (W, y)], fill=grid_color, width=1)
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    # ── фоновые украшения (порт дизайна threads-carousel) ──────────────────────

    def _composite(self, img, overlay):
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    def _pick_decoration(self, slide_num):
        rotation = ["glow", "dotgrid", "bignumber", "lines", "blobs", "ruled"]
        return rotation[(slide_num - 1) % len(rotation)]

    def _draw_decoration(self, img, theme, deco, slide_num):
        if deco in (None, "none"):
            return
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        text = hex_to_rgb(t["text"])
        if deco == "dotgrid":
            self._dec_dotgrid(img, accent)
        elif deco == "lines":
            self._dec_diaglines(img, accent)
        elif deco == "ruled":
            self._dec_ruled(img, text)
        elif deco == "bignumber":
            self._dec_bignumber(img, accent, slide_num)
        elif deco == "glow":
            self._dec_glow(img, accent, slide_num)
        elif deco == "blobs":
            self._dec_blobs(img, accent, slide_num)

    def _dec_dotgrid(self, img, accent):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        col = accent + (36,)
        r = 3
        for y in range(30, H, 60):
            for x in range(30, W, 60):
                d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        self._composite(img, overlay)

    def _dec_diaglines(self, img, accent):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        col = accent + (22,)
        dx = int(H * 0.7)  # наклон ~ -35°
        for off in range(-dx, W + dx, 74):
            d.line([(off, 0), (off + dx, H)], fill=col, width=3)
        self._composite(img, overlay)

    def _dec_ruled(self, img, text):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        line_col = text + (30,)
        margin_col = text + (56,)
        for y in range(128, H, 64):
            d.line([(0, y), (W, y)], fill=line_col, width=2)
        d.line([(140, 0), (140, H)], fill=margin_col, width=2)
        self._composite(img, overlay)

    def _dec_bignumber(self, img, accent, slide_num):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        col = accent + (16,)
        font = self._bold(720)
        txt = f"{slide_num:02d}"
        bbox = d.textbbox((0, 0), txt, font=font)
        tw = bbox[2] - bbox[0]
        d.text((W - tw + 80, H - 640), txt, font=font, fill=col)
        self._composite(img, overlay)

    def _dec_glow(self, img, accent, slide_num):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        size = 900
        corners = [(-200, -200), (W - 700, H - 700), (-200, H - 700), (W - 700, -200)]
        cx, cy = corners[(slide_num - 1) % len(corners)]
        d.ellipse([cx, cy, cx + size, cy + size], fill=accent + (60,))
        overlay = overlay.filter(ImageFilter.GaussianBlur(70))
        self._composite(img, overlay)

    def _dec_blobs(self, img, accent, slide_num):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        spots = [(W - 360, -160, 520), (-180, H - 420, 440)]
        if slide_num % 2 == 0:
            spots = [(-200, -180, 480), (W - 300, H - 360, 420)]
        for (x, y, s) in spots:
            d.ellipse([x, y, x + s, y + s], fill=accent + (40,))
        overlay = overlay.filter(ImageFilter.GaussianBlur(55))
        self._composite(img, overlay)

    def _tw(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _th(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _draw_tracked(self, draw, xy, text, font, fill, tracking=3):
        """Текст с разрядкой между буквами — журнальная деталь для капса."""
        x, y = xy
        for ch in text:
            draw.text((x, y), ch, font=font, fill=fill)
            x += self._tw(draw, ch, font) + tracking
        return x

    def _tracked_w(self, draw, text, font, tracking=3):
        if not text:
            return 0
        w = 0
        for ch in text:
            w += self._tw(draw, ch, font) + tracking
        return w - tracking

    def _draw_top_bar(self, draw, theme, username, topic, right_label=""):
        t = THEMES[theme]
        dim = hex_to_rgb(t["text_dim"])
        border = hex_to_rgb(t["card_border"])

        # мелкие капсы — лёгкие и воздушные, как в референсе
        lbl_font = self._med(22)
        user_text = username.upper()
        self._draw_tracked(draw, (PAD, 58), user_text, lbl_font, dim, 3)

        right_text = (right_label or topic or "").upper()
        if right_text:
            rw = self._tracked_w(draw, right_text, lbl_font, 3)
            # обрезаем если не влезает
            while rw > W - PAD * 2 - 200 and len(right_text) > 3:
                right_text = right_text[:-1]
                rw = self._tracked_w(draw, right_text, lbl_font, 3)
            self._draw_tracked(draw, (W - PAD - rw, 58), right_text, lbl_font, dim, 3)

        draw.line([(PAD, 108), (W - PAD, 108)], fill=border, width=1)

    def _draw_bottom_bar(self, draw, theme, slide_num, total, is_last=False, username=""):
        t = THEMES[theme]
        dim = hex_to_rgb(t["text_dim"])
        border = hex_to_rgb(t["card_border"])
        font = self._med(22)

        draw.line([(PAD, H - 106), (W - PAD, H - 106)], fill=border, width=1)

        # username слева — капсом
        left_text = username.upper() if username else f"{slide_num:02d} / {total:02d}"
        self._draw_tracked(draw, (PAD, H - 74), left_text, font, dim, 3)

        cta = "СОХРАНИ ♡" if is_last else "ЛИСТАЙ →"
        cw = self._tracked_w(draw, cta, font, 3)
        self._draw_tracked(draw, (W - PAD - cw, H - 74), cta, font, dim, 3)

    def _draw_badge(self, draw, theme, number, label, y):
        t = THEMES[theme]
        badge_bg = hex_to_rgb(t["badge_bg"])
        badge_text = hex_to_rgb(t["badge_text"])
        label_color = hex_to_rgb(t["label_color"])
        style = t["badge_style"]

        num_font = self._bold(26)
        label_font = self._med(25)
        num_text = str(number).zfill(2)

        if style == "circle":
            r = 27
            cx, cy = PAD + r, y + r
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=badge_bg)
            nw = self._tw(draw, num_text, num_font)
            nh = self._th(draw, num_text, num_font)
            draw.text((cx - nw // 2, cy - nh // 2 - 2), num_text, font=num_font, fill=badge_text)
            lx = PAD + r * 2 + 20
            ly = cy - 12
        else:
            nw = self._tw(draw, num_text, num_font)
            pad_x = 16
            bw = nw + pad_x * 2
            bh = 46
            draw.rectangle([PAD, y, PAD + bw, y + bh], fill=badge_bg)
            draw.text((PAD + pad_x, y + (bh - 34) // 2 - 2), num_text, font=num_font, fill=badge_text)
            lx = PAD + bw + 20
            ly = y + (bh - 25) // 2

        if label:
            self._draw_tracked(draw, (lx, ly), label.upper(), label_font, label_color, 2)

    def _draw_headline(self, draw, theme, title, accent_word, y, max_width, font_size=88, x=None, use_display=False):
        t = THEMES[theme]
        # Playfair Italic — редакционный заголовок; для обложки крупнее
        font = self._playfair_italic(font_size) if use_display else self._playfair_italic(font_size)
        main = hex_to_rgb(t["text"])
        accent = hex_to_rgb(t["accent"])
        if x is None:
            x = PAD

        words = title.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            if self._tw(draw, test, font) > max_width and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))

        # 1.18 даёт заголовку воздух при переносе (был 1.06 — слишком тесно)
        line_h = int(font_size * 1.18)
        for line in lines:
            if accent_word and accent_word.lower() in line.lower():
                idx = line.lower().find(accent_word.lower())
                before = line[:idx]
                acc = line[idx:idx + len(accent_word)]
                after = line[idx + len(accent_word):]
                cx = x
                if before:
                    draw.text((cx, y), before, font=font, fill=main)
                    cx += self._tw(draw, before, font)
                draw.text((cx, y), acc, font=font, fill=accent)
                cx += self._tw(draw, acc, font)
                if after:
                    draw.text((cx, y), after, font=font, fill=main)
            else:
                draw.text((x, y), line, font=font, fill=main)
            y += line_h

        return y

    def _count_wrapped_lines(self, draw, lines, font, max_w):
        total = 0
        for line in lines:
            words = line.split()
            cur = []
            n = 0
            for word in words:
                test = " ".join(cur + [word])
                if self._tw(draw, test, font) > max_w and cur:
                    n += 1
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                n += 1
            total += max(n, 1)
        return total

    def _draw_body(self, draw, theme, lines, y, font_size=42, max_y=None):
        t = THEMES[theme]
        main = hex_to_rgb(t["text"])
        max_w = W - PAD * 2

        # авто-подбор размера шрифта, чтобы весь текст влез (без обрезки)
        # Шкала ×1.15 — каждый шаг ощутим (≥15%), убраны 2px-микропромежутки
        chosen = font_size
        if max_y:
            avail = max_y - y
            for fs in (44, 38, 34, 30, 26, 23, 21):
                f = self._reg(fs)
                lh = int(fs * 1.38)
                n = self._count_wrapped_lines(draw, lines, f, max_w)
                if n * lh <= avail:
                    chosen = fs
                    break
            else:
                chosen = 22

        font = self._reg(chosen)
        line_h = int(chosen * 1.38)
        for line in lines:
            # wrap long lines
            words = line.split()
            cur = []
            for word in words:
                test = " ".join(cur + [word])
                if self._tw(draw, test, font) > max_w and cur:
                    draw.text((PAD, y), " ".join(cur), font=font, fill=main)
                    y += line_h
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                draw.text((PAD, y), " ".join(cur), font=font, fill=main)
                y += line_h
        return y

    # ── visual: table ──────────────────────────────────────────────────────────

    def _draw_table(self, draw, theme, rows, y):
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        label_font = self._reg(32)
        value_font = self._bold(40)

        cp = 32
        row_h = 72
        card_h = cp + len(rows) * row_h + cp
        card_w = W - PAD * 2
        r = 16

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

        cy = y + cp
        for i, row in enumerate(rows):
            is_accent = row.get("accent", False)
            value_color = accent if is_accent else main
            draw.text((PAD + cp, cy + 14), row["label"], font=label_font, fill=dim)
            val = str(row["value"])
            vw = self._tw(draw, val, value_font)
            draw.text((PAD + card_w - cp - vw, cy + 8), val, font=value_font, fill=value_color)
            if i < len(rows) - 1:
                ly = cy + row_h - 1
                draw.line([(PAD + cp, ly), (PAD + card_w - cp, ly)], fill=card_border, width=1)
            cy += row_h

        return y + card_h + 32

    # ── visual: cards 2×2 ─────────────────────────────────────────────────────

    def _draw_cards_2x2(self, draw, theme, cards, y):
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        num_font = self._bold(24)
        title_font = self._bold(34)
        body_font = self._reg(28)

        gap = 16
        card_w = (W - PAD * 2 - gap) // 2
        card_h = 200
        r = 16

        for i, card in enumerate(cards[:4]):
            col = i % 2
            row = i // 2
            cx = PAD + col * (card_w + gap)
            cy = y + row * (card_h + gap)
            draw.rounded_rectangle([cx, cy, cx + card_w, cy + card_h], radius=r, fill=card_bg, outline=card_border, width=1)
            num = card.get("number", f"0{i+1}")
            nw = self._tw(draw, num, num_font)
            draw.text((cx + card_w - 28 - nw, cy + 20), num, font=num_font, fill=accent)
            draw.text((cx + 24, cy + 52), card["title"], font=title_font, fill=main)
            draw.text((cx + 24, cy + 100), card.get("text", ""), font=body_font, fill=dim)

        rows = math.ceil(len(cards[:4]) / 2)
        return y + rows * (card_h + gap)

    # ── visual: comparison ────────────────────────────────────────────────────

    def _draw_comparison(self, draw, theme, left, right, y):
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        title_font = self._bold(28)
        item_font = self._reg(30)
        result_font = self._bold(52)

        gap = 16
        card_w = (W - PAD * 2 - gap) // 2
        card_h = 260
        r = 16

        for side_idx, side in enumerate([left, right]):
            cx = PAD + side_idx * (card_w + gap)
            draw.rounded_rectangle([cx, y, cx + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)
            title_color = accent if side_idx == 1 else dim
            draw.text((cx + 24, y + 24), side["title"].upper(), font=title_font, fill=title_color)
            iy = y + 74
            for item in side.get("items", [])[:3]:
                draw.text((cx + 24, iy), item, font=item_font, fill=main)
                iy += 46
            result = side.get("result", "")
            result_color = accent if side_idx == 1 else dim
            draw.text((cx + 24, y + card_h - 72), result, font=result_font, fill=result_color)

        return y + card_h + 32

    # ── visual: terminal ──────────────────────────────────────────────────────

    def _draw_terminal(self, draw, theme, title, lines, y):
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        accent = hex_to_rgb(t["accent"])

        title_font = self._reg(26)
        line_font = self._reg(32)

        dot_colors = [(255, 95, 87), (255, 189, 46), (40, 200, 64)]
        cp = 28
        line_h = 50
        card_h = 62 + len(lines) * line_h + cp
        card_w = W - PAD * 2
        r = 16

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

        for i, color in enumerate(dot_colors):
            draw.ellipse([PAD + cp + i * 22, y + 18, PAD + cp + i * 22 + 12, y + 30], fill=color)

        tx = PAD + card_w // 2 - self._tw(draw, title, title_font) // 2
        draw.text((tx, y + 16), title, font=title_font, fill=dim)

        ly = y + 62
        for line in lines:
            prefix = line.get("prefix", "")
            text = line.get("text", "")
            lt = line.get("type", "normal")
            prefix_color = (40, 200, 64) if lt == "success" else (accent if lt == "command" else dim)
            text_color = main if lt in ("command", "normal") else dim

            draw.text((PAD + cp, ly), prefix, font=line_font, fill=prefix_color)
            px = PAD + cp + self._tw(draw, prefix + " ", line_font)
            draw.text((px, ly), text, font=line_font, fill=text_color)
            ly += line_h

        return y + card_h + 32

    # ── visual: checklist ─────────────────────────────────────────────────────

    def _draw_checklist(self, draw, theme, title, items, y):
        """Items: {text, done, tag, tag_color}"""
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        strike_color = hex_to_rgb(t["strikethrough_color"])
        accent = hex_to_rgb(t["accent"])

        title_font = self._reg(26)
        item_font = self._med(34)
        tag_font = self._bold(24)

        dot_colors = [(255, 95, 87), (255, 189, 46), (40, 200, 64)]
        cp = 28
        cr = 18
        line_h = 42
        header_h = 60
        card_w = W - PAD * 2
        text_x = PAD + cp + cr * 2 + 20
        text_max_w = card_w - cp - cr * 2 - 20 - cp  # учитываем отступы слева и справа

        # предварительно считаем высоту каждого пункта с учётом переноса
        item_wrapped = []
        for item in items:
            wlines = self._wrap(draw, item.get("text", ""), item_font, text_max_w)
            item_wrapped.append(wlines)

        row_heights = [max(line_h * len(wl), line_h) + 24 for wl in item_wrapped]
        card_h = header_h + sum(row_heights) + cp
        r = 16

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

        for i, color in enumerate(dot_colors):
            draw.ellipse([PAD + cp + i * 22, y + 18, PAD + cp + i * 22 + 12, y + 30], fill=color)
        tx = PAD + card_w // 2 - self._tw(draw, title, title_font) // 2
        draw.text((tx, y + 16), title, font=title_font, fill=dim)

        iy = y + header_h
        for idx, item in enumerate(items):
            text = item.get("text", "")
            done = item.get("done", False)
            tag = item.get("tag", "")
            tag_col_hex = item.get("tag_color", "#E53E3E")
            wlines = item_wrapped[idx]
            rh = row_heights[idx]

            # circle checkbox — по центру высоты пункта
            ccx, ccy = PAD + cp + cr, iy + rh // 2
            if done:
                draw.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], fill=accent)
                draw.text((ccx - 9, ccy - 12), "✓", font=self._bold(26), fill=(255, 255, 255))
            else:
                draw.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], outline=dim, width=2)

            text_color = strike_color if done else main
            ty = iy + (rh - line_h * len(wlines)) // 2
            for wl in wlines:
                draw.text((text_x, ty), wl, font=item_font, fill=text_color)
                if done:
                    tw = self._tw(draw, wl, item_font)
                    draw.line([(text_x, ty + 17), (text_x + tw, ty + 17)], fill=strike_color, width=2)
                ty += line_h

            if tag:
                tag_bg = hex_to_rgb(tag_col_hex)
                tag_text = tag.upper()
                tw_tag = self._tw(draw, tag_text, tag_font)
                tag_pad = 14
                tag_w = tw_tag + tag_pad * 2
                tag_h = 40
                tag_x = PAD + card_w - cp - tag_w
                tag_y = iy + (rh - tag_h) // 2
                draw.rounded_rectangle([tag_x, tag_y, tag_x + tag_w, tag_y + tag_h], radius=8, fill=tag_bg)
                draw.text((tag_x + tag_pad, tag_y + 8), tag_text, font=tag_font, fill=(255, 255, 255))

            if items.index(item) < len(items) - 1:
                draw.line([(PAD + cp, iy + rh - 1), (PAD + card_w - cp, iy + rh - 1)], fill=card_border, width=1)

            iy += rh

        return y + card_h + 32

    # ── visual: numbered_list ─────────────────────────────────────────────────

    def _draw_numbered_list(self, draw, theme, items, y):
        """items: [{number, text}]"""
        t = THEMES[theme]
        main = hex_to_rgb(t["text"])
        accent = hex_to_rgb(t["accent"])

        num_font = self._bold(38)
        text_font = self._reg(38)
        num_col_w = 72
        max_text_w = W - PAD * 2 - num_col_w
        line_h = 56

        for item in items:
            num = str(item.get("number", "")).zfill(2)
            text = item.get("text", "")
            draw.text((PAD, y), num, font=num_font, fill=accent)

            # wrap text
            words = text.split()
            lines = []
            cur = []
            for word in words:
                test = " ".join(cur + [word])
                if self._tw(draw, test, text_font) > max_text_w and cur:
                    lines.append(" ".join(cur))
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                lines.append(" ".join(cur))

            ty = y
            for line in lines:
                draw.text((PAD + num_col_w, ty), line, font=text_font, fill=main)
                ty += line_h
            y = ty + 20

        return y

    # ── visual: bullet_list ───────────────────────────────────────────────────

    def _draw_bullet_list(self, draw, theme, items, y, bullet_color=None):
        """items: [str] — переносит длинные строки, ничего не обрезает"""
        t = THEMES[theme]
        main = hex_to_rgb(t["text"])
        bc = hex_to_rgb(bullet_color) if bullet_color else hex_to_rgb(t["accent"])

        text_font = self._reg(38)
        line_h = 54
        r = 8
        text_x = PAD + r * 2 + 24
        max_w = W - text_x - PAD

        for item in items:
            # маркер на уровне первой строки
            draw.ellipse([PAD, y + 14, PAD + r * 2, y + 14 + r * 2], fill=bc)
            words = item.split()
            cur = []
            for word in words:
                test = " ".join(cur + [word])
                if self._tw(draw, test, text_font) > max_w and cur:
                    draw.text((text_x, y), " ".join(cur), font=text_font, fill=main)
                    y += line_h
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                draw.text((text_x, y), " ".join(cur), font=text_font, fill=main)
                y += line_h
            y += 16  # промежуток между пунктами

        return y

    # ── visual: progress_bar ──────────────────────────────────────────────────

    def _draw_progress_bar(self, draw, theme, label_before, value_before, label_after, value_after, y):
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        accent = hex_to_rgb(t["accent"])

        label_font = self._med(30)
        val_font = self._bold(36)

        card_w = W - PAD * 2
        card_h = 220
        r = 16
        cp = 32

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

        # before row
        draw.text((PAD + cp, y + cp), label_before, font=label_font, fill=dim)
        bar_x = PAD + cp
        bar_y = y + cp + 44
        bar_w = card_w - cp * 2
        bar_h = 44
        draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=bar_h // 2, fill=hex_to_rgb(t["card_border"]))
        vw = self._tw(draw, str(value_before), val_font)
        draw.text((bar_x + bar_w - vw - cp, bar_y + 6), str(value_before), font=val_font, fill=dim)

        # after row
        draw.text((PAD + cp, y + cp + bar_h + 60), label_after, font=label_font, fill=dim)
        bar_y2 = y + cp + bar_h + 60 + 44
        after_bar_w = bar_w // 4
        bar_accent = accent if accent != (17, 17, 17) else (200, 140, 180)
        draw.rounded_rectangle([bar_x, bar_y2, bar_x + after_bar_w, bar_y2 + bar_h], radius=bar_h // 2, fill=bar_accent)
        vw2 = self._tw(draw, str(value_after), val_font)
        draw.text((bar_x + after_bar_w + 20, bar_y2 + 6), str(value_after), font=val_font, fill=main)

        return y + card_h + 32

    # ── visual: file_tree ─────────────────────────────────────────────────────

    def _draw_file_tree(self, draw, theme, header, badge, rows, y):
        """rows: [{indent, icon, text, value, value_accent}]"""
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        accent = hex_to_rgb(t["accent"])

        mono_font = self._reg(30)
        header_font = self._med(26)
        badge_font = self._bold(26)

        dot_colors = [(255, 95, 87), (255, 189, 46), (40, 200, 64)]
        cp = 28
        row_h = 52
        header_h = 62
        card_h = header_h + len(rows) * row_h + cp
        card_w = W - PAD * 2
        r = 16

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

        for i, color in enumerate(dot_colors):
            draw.ellipse([PAD + cp + i * 22, y + 18, PAD + cp + i * 22 + 12, y + 30], fill=color)

        draw.text((PAD + cp + 80, y + 16), header, font=header_font, fill=dim)

        if badge:
            bw = self._tw(draw, badge.upper(), badge_font)
            bx = PAD + card_w - cp - bw
            draw.text((bx, y + 16), badge.upper(), font=badge_font, fill=accent)

        ly = y + header_h
        for i, row in enumerate(rows):
            indent = row.get("indent", 0)
            icon = row.get("icon", "")
            text = row.get("text", "")
            value = row.get("value", "")
            va = row.get("value_accent", False)

            tx = PAD + cp + indent * 36
            line_text = f"{icon} {text}" if icon else text
            draw.text((tx, ly + 10), line_text, font=mono_font, fill=main)

            if value:
                vw = self._tw(draw, value, mono_font)
                vc = accent if va else dim
                draw.text((PAD + card_w - cp - vw, ly + 10), value, font=mono_font, fill=vc)

            if i < len(rows) - 1:
                sep_y = ly + row_h - 1
                draw.line([(PAD + cp, sep_y), (PAD + card_w - cp, sep_y)], fill=card_border, width=1)

            ly += row_h

        return y + card_h + 32

    # ── visual: big_stat ─────────────────────────────────────────────────────

    def _draw_divider(self, draw, theme, y, x=PAD, w=96):
        """Короткая акцентная черта под заголовком (как в threads-carousel)."""
        accent = hex_to_rgb(THEMES[theme]["accent"])
        draw.rectangle([x, y, x + w, y + 5], fill=accent)
        return y + 5

    def _draw_hero_number(self, draw, theme, big_number, caption, y):
        """Огромная цифра-герой ('17', '5K+', '№1') + подпись."""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        dim = hex_to_rgb(t["text_dim"])

        num = str(big_number)
        # Oswald Bold: конденсированный дисплей — мощнее Inter-Bold на крупных размерах
        num_font = self._oswald(300, "Bold")
        num_top = y - 20
        draw.text((PAD - 6, num_top), num, font=num_font, fill=accent)
        bbox = draw.textbbox((PAD - 6, num_top), num, font=num_font)
        yy = bbox[3] + 44  # подпись строго под нижним краем цифры

        if caption:
            cap_font = self._med(44)
            max_w = W - PAD * 2
            words = caption.split()
            cur = []
            for word in words:
                test = " ".join(cur + [word])
                if self._tw(draw, test, cap_font) > max_w and cur:
                    draw.text((PAD, yy), " ".join(cur), font=cap_font, fill=dim)
                    yy += 56
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                draw.text((PAD, yy), " ".join(cur), font=cap_font, fill=dim)
                yy += 56
        return yy + 16

    def _draw_big_stat(self, draw, theme, stats, y):
        """stats: [{number, label}] — 1-3 больших цифры"""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        count = len(stats[:3])
        col_w = (W - PAD * 2) // count
        num_font = self._bold(120)
        lbl_font = self._reg(34)

        for i, stat in enumerate(stats[:3]):
            cx = PAD + i * col_w + col_w // 2
            num = str(stat.get("number", ""))
            lbl = stat.get("label", "")
            nw = self._tw(draw, num, num_font)
            draw.text((cx - nw // 2, y), num, font=num_font, fill=accent)
            lw = self._tw(draw, lbl, lbl_font)
            draw.text((cx - lw // 2, y + 128), lbl, font=lbl_font, fill=dim)

        return y + 200

    # ── visual: quote ─────────────────────────────────────────────────────────

    def _draw_quote(self, draw, theme, text, author, y):
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        quote_font = self._bold(52)
        author_font = self._med(32)

        # accent bar
        draw.rectangle([PAD, y, PAD + 6, y + 220], fill=accent)

        # wrap quote text
        max_w = W - PAD * 2 - 40
        words = text.split()
        lines = []
        cur = []
        for word in words:
            test = " ".join(cur + [word])
            if self._tw(draw, test, quote_font) > max_w and cur:
                lines.append(" ".join(cur))
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(" ".join(cur))

        ty = y
        for line in lines:
            draw.text((PAD + 40, ty), line, font=quote_font, fill=main)
            ty += 66

        if author:
            draw.text((PAD + 40, ty + 16), f"— {author}", font=author_font, fill=dim)
            ty += 56

        return ty + 32

    # ── visual: timeline ──────────────────────────────────────────────────────

    def _draw_timeline(self, draw, theme, steps, y):
        """steps: [{number, title, text}]"""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        card_bg = hex_to_rgb(t["card_bg"])

        num_font = self._bold(30)
        title_font = self._bold(36)
        body_font = self._reg(30)

        dot_r = 24
        dot_x = PAD + dot_r
        line_x = dot_x
        step_h = 110

        for i, step in enumerate(steps):
            cy = y + i * step_h + dot_r

            # vertical line between dots
            if i < len(steps) - 1:
                draw.line([(line_x, cy + dot_r), (line_x, cy + step_h - dot_r)], fill=hex_to_rgb(t["card_border"]), width=2)

            # dot
            draw.ellipse([dot_x - dot_r, cy - dot_r, dot_x + dot_r, cy + dot_r], fill=accent)
            num = str(step.get("number", i + 1))
            nw = self._tw(draw, num, num_font)
            nh = self._th(draw, num, num_font)
            draw.text((dot_x - nw // 2, cy - nh // 2 - 2), num, font=num_font, fill=hex_to_rgb(t["badge_text"]) if t["badge_style"] == "square" else (255,255,255))

            # text
            tx = dot_x + dot_r + 28
            draw.text((tx, cy - dot_r + 4), step.get("title", ""), font=title_font, fill=main)
            if step.get("text"):
                draw.text((tx, cy - dot_r + 48), step["text"], font=body_font, fill=dim)

        return y + len(steps) * step_h + 16

    # ── visual: two_col ───────────────────────────────────────────────────────

    def _draw_two_col(self, draw, theme, left_title, left_items, right_title, right_items, y):
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        title_font = self._bold(32)
        item_font = self._reg(32)
        dot_r = 6
        gap = 32
        col_w = (W - PAD * 2 - gap) // 2
        line_h = 52

        col_bottoms = []
        for col_idx, (title, items) in enumerate([(left_title, left_items), (right_title, right_items)]):
            cx = PAD + col_idx * (col_w + gap)
            draw.text((cx, y), title.upper(), font=title_font, fill=accent)
            iy = y + 52
            text_x = cx + dot_r * 2 + 16
            max_w = col_w - (dot_r * 2 + 16)
            for item in items:
                draw.ellipse([cx, iy + 12, cx + dot_r * 2, iy + 12 + dot_r * 2], fill=dim)
                words = item.split()
                cur = []
                for word in words:
                    test = " ".join(cur + [word])
                    if self._tw(draw, test, item_font) > max_w and cur:
                        draw.text((text_x, iy), " ".join(cur), font=item_font, fill=main)
                        iy += 44
                        cur = [word]
                    else:
                        cur.append(word)
                if cur:
                    draw.text((text_x, iy), " ".join(cur), font=item_font, fill=main)
                    iy += 44
                iy += 12
            col_bottoms.append(iy)

        return max(col_bottoms) + 24

    # ── visual: pull_quote ────────────────────────────────────────────────────

    def _draw_pull_quote(self, draw, theme, text, author, y):
        """Редакционная цитата: декоративный знак + Playfair Italic + автор."""
        t = THEMES[theme]
        bg = hex_to_rgb(t["bg"])
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        # декоративный знак кавычки — смешиваем акцент с фоном для эффекта водяного знака
        a = 0.14
        mark_col = tuple(int(bg[i] * (1 - a) + accent[i] * a) for i in range(3))
        mark_font = self._playfair(280)
        draw.text((PAD - 16, y - 40), "“", font=mark_font, fill=mark_col)

        # тонкая вертикальная черта слева — журнальный приём
        draw.rectangle([PAD, y + 24, PAD + 4, y + 24 + min(len(text.split()) * 18, 260)], fill=accent)

        # цитата в Playfair Italic — сердце шаблона
        quote_font = self._playfair_italic(58)
        max_w = W - PAD * 2 - 40
        wrapped = self._wrap(draw, text, quote_font, max_w)
        line_h = int(58 * 1.32)
        ty = y + 24
        for line in wrapped:
            draw.text((PAD + 40, ty), line, font=quote_font, fill=main)
            ty += line_h

        if author:
            ty += 16
            auth_font = self._med(30)
            draw.text((PAD + 40, ty), f"— {author}", font=auth_font, fill=dim)
            ty += 44

        return ty + 24

    # ── visual: stat_grid ─────────────────────────────────────────────────────

    def _draw_stat_grid(self, draw, theme, stats, y):
        """2×2 bento-сетка: большие цифры Playfair + метки Inter. 2 или 4 стата."""
        t = THEMES[theme]
        card_bg = hex_to_rgb(t["card_bg"])
        card_border = hex_to_rgb(t["card_border"])
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])

        count = min(len(stats), 4)
        cols = 2
        rows = math.ceil(count / cols)
        gap = 20
        card_w = (W - PAD * 2 - gap) // 2
        card_h = 220
        r = 20
        num_font = self._playfair(84)
        lbl_font = self._med(28)
        trend_font = self._bold(26)

        for i, stat in enumerate(stats[:count]):
            col = i % cols
            row = i // cols
            cx = PAD + col * (card_w + gap)
            cy = y + row * (card_h + gap)

            draw.rounded_rectangle([cx, cy, cx + card_w, cy + card_h], radius=r, fill=card_bg, outline=card_border, width=1)

            num = str(stat.get("number", ""))
            lbl = str(stat.get("label", ""))
            trend = str(stat.get("trend", ""))

            nw = self._tw(draw, num, num_font)
            nh = self._th(draw, num, num_font)
            nx = cx + (card_w - nw) // 2
            ny = cy + (card_h - nh) // 2 - 20
            draw.text((nx, ny), num, font=num_font, fill=accent)

            if lbl:
                lw = self._tw(draw, lbl, lbl_font)
                draw.text((cx + (card_w - lw) // 2, cy + card_h - 60), lbl, font=lbl_font, fill=dim)

            if trend:
                trend_col = accent if not trend.startswith("-") else dim
                tw_val = self._tw(draw, trend, trend_font)
                draw.text((cx + card_w - tw_val - 20, cy + 20), trend, font=trend_font, fill=trend_col)

        return y + rows * (card_h + gap)

    # ── visual: highlight_card ────────────────────────────────────────────────

    def _draw_highlight_card(self, draw, theme, heading, subtext, y):
        """Полноширинная акцентная карточка — визуальный удар для ключевой мысли."""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        bg_col = hex_to_rgb(t["bg"])
        card_bg = hex_to_rgb(t["card_bg"])

        # если тема уже тёмная/яркая (hot/plum) — карточка светлая, иначе — акцентная
        is_dark_theme = sum(hex_to_rgb(t["bg"])) < 400 or t["bg"] in ("#E8187A",)
        box_fill = card_bg if is_dark_theme else accent
        text_col = hex_to_rgb(t["text"]) if is_dark_theme else (255, 255, 255)
        sub_col = hex_to_rgb(t["text_dim"]) if is_dark_theme else (255, 220, 238)

        card_w = W - PAD * 2
        h_font = self._playfair_italic(76)
        s_font = self._reg(34)
        max_w = card_w - 64

        h_lines = self._wrap(draw, heading, h_font, max_w)
        s_lines = self._wrap(draw, subtext, s_font, max_w) if subtext else []

        line_h = int(76 * 1.22)
        total_h = len(h_lines) * line_h + (len(s_lines) * 46 + 28 if s_lines else 0) + 80
        card_h = max(total_h, 260)

        draw.rounded_rectangle([PAD, y, PAD + card_w, y + card_h], radius=24, fill=box_fill)

        ty = y + 40
        for line in h_lines:
            lw = self._tw(draw, line, h_font)
            draw.text((PAD + (card_w - lw) // 2, ty), line, font=h_font, fill=text_col)
            ty += line_h

        if s_lines:
            ty += 12
            for line in s_lines:
                lw = self._tw(draw, line, s_font)
                draw.text((PAD + (card_w - lw) // 2, ty), line, font=s_font, fill=sub_col)
                ty += 46

        return y + card_h + 32

    # ── visual: steps_flow ────────────────────────────────────────────────────

    def _draw_steps_flow(self, draw, theme, steps, y):
        """3-4 горизонтальных шага с соединяющими стрелками. Схема 'как это работает'."""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        card_bg = hex_to_rgb(t["card_bg"])
        badge_text = hex_to_rgb(t["badge_text"]) if "badge_text" in t else (255, 255, 255)

        count = min(len(steps), 4)
        gap = 20
        card_w = (W - PAD * 2 - gap * (count - 1)) // count
        num_font = self._bold(32)
        title_font = self._bold(26)
        body_font = self._reg(22)
        dot_r = 36
        card_h = 280

        for i, step in enumerate(steps[:count]):
            cx = PAD + i * (card_w + gap)
            cy = y

            draw.rounded_rectangle([cx, cy, cx + card_w, cy + card_h], radius=16, fill=card_bg, outline=hex_to_rgb(t["card_border"]), width=1)

            # кружок с номером вверху карточки
            circle_cx = cx + card_w // 2
            circle_cy = cy + dot_r + 20
            draw.ellipse([circle_cx - dot_r, circle_cy - dot_r, circle_cx + dot_r, circle_cy + dot_r], fill=accent)
            num = str(step.get("number", i + 1))
            nw = self._tw(draw, num, num_font)
            nh = self._th(draw, num, num_font)
            draw.text((circle_cx - nw // 2, circle_cy - nh // 2 - 2), num, font=num_font, fill=(255, 255, 255))

            # заголовок под кружком
            title = step.get("title", "")
            title_lines = self._wrap(draw, title, title_font, card_w - 24)
            ty = circle_cy + dot_r + 20
            for tl in title_lines[:2]:
                tw = self._tw(draw, tl, title_font)
                draw.text((cx + (card_w - tw) // 2, ty), tl, font=title_font, fill=main)
                ty += 36

            # описание
            text = step.get("text", "")
            if text:
                ty += 8
                body_lines = self._wrap(draw, text, body_font, card_w - 24)
                for bl in body_lines[:3]:
                    bw = self._tw(draw, bl, body_font)
                    draw.text((cx + (card_w - bw) // 2, ty), bl, font=body_font, fill=dim)
                    ty += 30

            # стрелка между шагами
            if i < count - 1:
                ax = cx + card_w + gap // 2
                ay = cy + card_h // 2
                aw = 14
                draw.polygon([(ax - aw // 2, ay - aw), (ax + aw // 2, ay), (ax - aw // 2, ay + aw)], fill=accent)

        return y + card_h + 32

    # ── visual: magazine_split ────────────────────────────────────────────────

    def _draw_magazine_split(self, draw, theme, big_label, items, y):
        """Асимметричный разворот: 1/3 — большое слово/число, 2/3 — список."""
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["text_dim"])
        card_border = hex_to_rgb(t["card_border"])

        divider_x = PAD + (W - PAD * 2) // 3 + 16
        right_x = divider_x + 48
        right_max_w = W - PAD - right_x

        # вертикальная линия-разделитель — журнальный приём
        total_h = max(len(items) * 130, 300)
        draw.rectangle([divider_x, y, divider_x + 2, y + total_h], fill=accent)

        # левая колонка: большое слово/число в Playfair Italic
        left_font = self._playfair_italic(72)
        left_col_w = divider_x - PAD - 20
        left_lines = self._wrap(draw, big_label, left_font, left_col_w)
        line_h_l = int(72 * 1.22)
        block_h = len(left_lines) * line_h_l
        ly = y + (total_h - block_h) // 2
        for line in left_lines:
            lw = self._tw(draw, line, left_font)
            draw.text((PAD + (left_col_w - lw) // 2, ly), line, font=left_font, fill=accent)
            ly += line_h_l

        # правая колонка: список с заголовком + описание
        title_font = self._bold(30)
        body_font = self._reg(26)
        dot_r = 6
        ry = y + 8
        for item in items:
            title = item.get("title", "")
            text = item.get("text", "")

            # маркер
            draw.ellipse([right_x, ry + 10, right_x + dot_r * 2, ry + 10 + dot_r * 2], fill=accent)
            tx = right_x + dot_r * 2 + 16
            tw_max = right_max_w - dot_r * 2 - 16

            if title:
                t_lines = self._wrap(draw, title, title_font, tw_max)
                for tl in t_lines:
                    draw.text((tx, ry), tl, font=title_font, fill=main)
                    ry += 38

            if text:
                b_lines = self._wrap(draw, text, body_font, tw_max)
                for bl in b_lines:
                    draw.text((tx, ry), bl, font=body_font, fill=dim)
                    ry += 32
            ry += 24

        return max(y + total_h, ry) + 32

    # ── visual: cta_pill ──────────────────────────────────────────────────────

    def _draw_cta_pill(self, draw, theme, text, y):
        t = THEMES[theme]
        pill_bg = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["bg"])

        font = self._bold(38)
        tw = self._tw(draw, text, font)
        ph = 28
        pv = 20
        pw = tw + ph * 2
        pill_h = 76
        r = pill_h // 2

        px = PAD
        draw.rounded_rectangle([px, y, px + pw, y + pill_h], radius=r, fill=pill_bg)
        draw.text((px + ph, y + pv), text, font=font, fill=main)
        return y + pill_h + 20

    # ── photo: full-slide background with overlay ─────────────────────────────

    def _apply_photo_background(self, img, photo_path, theme):
        """Fill entire slide with photo, darken/lighten to keep text readable."""
        t = THEMES[theme]
        bg = hex_to_rgb(t["bg"])
        is_dark = sum(bg) < 400

        try:
            photo = Image.open(photo_path).convert("RGB")
            src_ratio = photo.width / photo.height
            dst_ratio = W / H
            if src_ratio > dst_ratio:
                new_h = H
                new_w = int(H * src_ratio)
            else:
                new_w = W
                new_h = int(W / src_ratio)
            photo = photo.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - W) // 2
            top = (new_h - H) // 2
            photo = photo.crop((left, top, left + W, top + H))

            img.paste(photo, (0, 0))

            # overlay to maintain text legibility
            overlay_alpha = 160 if is_dark else 140
            overlay_color = (0, 0, 0) if is_dark else (255, 255, 255)
            overlay = Image.new("RGBA", (W, H), overlay_color + (overlay_alpha,))
            img_rgba = img.convert("RGBA")
            img_rgba = Image.alpha_composite(img_rgba, overlay)
            img.paste(img_rgba.convert("RGB"), (0, 0))
        except Exception:
            pass

    # ── photo: right half with gradient fade to transparent ───────────────────

    def _apply_photo_half_fade(self, img, photo_path):
        """Paste photo on the right half, fading to transparent on the left edge."""
        try:
            photo = Image.open(photo_path).convert("RGBA")
            half_w = W // 2
            src_ratio = photo.width / photo.height
            dst_ratio = half_w / H
            if src_ratio > dst_ratio:
                new_h = H
                new_w = int(H * src_ratio)
            else:
                new_w = half_w
                new_h = int(half_w / src_ratio)
            photo = photo.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - half_w) // 2
            top = (new_h - H) // 2
            photo = photo.crop((left, top, left + half_w, top + H))

            # horizontal gradient mask: 0 on left, 255 on right
            mask = Image.new("L", (half_w, H), 0)
            mask_draw = ImageDraw.Draw(mask)
            for x in range(half_w):
                alpha = int(255 * x / (half_w - 1))
                mask_draw.line([(x, 0), (x, H)], fill=alpha)

            photo.putalpha(mask)

            base = img.convert("RGBA")
            base.paste(photo, (half_w, 0), photo)
            img.paste(base.convert("RGB"), (0, 0))
        except Exception:
            # fallback: simple paste without fade
            try:
                photo = Image.open(photo_path).convert("RGB")
                half_w = W // 2
                photo = photo.resize((half_w, H), Image.LANCZOS)
                img.paste(photo, (half_w, 0))
            except Exception:
                pass

    # ── photo: аккуратный блок без растягивания ────────────────────────────────

    def _draw_photo_block(self, img, theme, photo_path, y_top, y_bottom):
        """Вписывает фото в доступную область, НИКОГДА не увеличивая его выше
        нативного размера — поэтому качество не теряется. Центрирует, скругляет углы."""
        t = THEMES[theme]
        avail_w = W - PAD * 2
        avail_h = max(0, y_bottom - y_top)
        if avail_h < 180:
            return

        try:
            photo = Image.open(photo_path).convert("RGB")
        except Exception:
            return

        nw, nh = photo.size
        # масштаб: вписать по ширине и высоте, но не растягивать вверх (cap 1.0)
        scale = min(avail_w / nw, avail_h / nh, 1.0)
        disp_w = max(1, int(nw * scale))
        disp_h = max(1, int(nh * scale))
        photo = photo.resize((disp_w, disp_h), Image.LANCZOS)

        x = PAD + (avail_w - disp_w) // 2
        yy = y_top + (avail_h - disp_h) // 2

        rr = 24
        mask = Image.new("L", (disp_w, disp_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, disp_w, disp_h], radius=rr, fill=255)
        img.paste(photo, (x, yy), mask)

        # тонкая рамка в тон темы
        border = hex_to_rgb(t["card_border"])
        ImageDraw.Draw(img).rounded_rectangle(
            [x, yy, x + disp_w, yy + disp_h], radius=rr, outline=border, width=2
        )

    # ── photo+text layout ─────────────────────────────────────────────────────

    def _draw_photo_text_layout(self, img, draw, theme, photo_path, label, title, accent_word, bullet_items, y_start):
        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        main = hex_to_rgb(t["text"])
        dim = hex_to_rgb(t["label_color"])

        # photo: left half, square-ish
        photo_w = (W - PAD * 2 - 32) // 2
        photo_h = photo_w + 60
        rx, ry = PAD, y_start

        try:
            photo = Image.open(photo_path).convert("RGB")
            # crop to fill photo_w x photo_h
            src_ratio = photo.width / photo.height
            dst_ratio = photo_w / photo_h
            if src_ratio > dst_ratio:
                new_h = photo_h
                new_w = int(photo_h * src_ratio)
            else:
                new_w = photo_w
                new_h = int(photo_w / src_ratio)
            photo = photo.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - photo_w) // 2
            top = (new_h - photo_h) // 2
            photo = photo.crop((left, top, left + photo_w, top + photo_h))

            rr = 20
            mask = Image.new("L", (photo_w, photo_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, photo_w, photo_h], radius=rr, fill=255)
            img.paste(photo, (rx, ry), mask)
        except Exception:
            pass

        # text side — starts after photo + gap
        tx = PAD + photo_w + 32
        tw_max = W - tx - PAD

        label_font = self._med(26)
        draw.text((tx, y_start), label.upper(), font=label_font, fill=dim)

        y = y_start + 46
        y = self._draw_headline(draw, theme, title, accent_word, y, tw_max, font_size=58, x=tx)
        y += 20

        for item in bullet_items:
            r = 7
            draw.ellipse([tx, y + 15, tx + r * 2, y + 15 + r * 2], fill=accent)
            draw.text((tx + r * 2 + 16, y), item, font=self._reg(32), fill=main)
            y += 56

        return y

    # ── main ──────────────────────────────────────────────────────────────────

    def _resolve_slide_theme(self, base_theme, slide_style):
        """Возвращает эффективный словарь цветов для слайда в зависимости от slide_style."""
        t = THEMES.get(base_theme, THEMES["fuchsia"])
        if slide_style == "accent":
            # горячий розовый — всегда независимо от выбранной темы
            return THEMES.get("hot", t)
        if slide_style == "minimal":
            # белый/жемчужный фон
            return THEMES.get("pearl", t)
        # editorial и всё остальное — базовая тема
        return t

    def generate_slide(self, slide_data, theme="fuchsia", username="@username", photo_path=None, photo_mode=None):
        slide_style = slide_data.get("slide_style", "editorial")
        # effective_theme — строковый ключ темы с учётом стиля слайда
        if slide_style == "accent":
            effective_theme = "hot"
        elif slide_style == "minimal":
            effective_theme = "pearl"
        else:
            effective_theme = theme
        t = THEMES.get(effective_theme, THEMES["fuchsia"])
        bg = hex_to_rgb(t["bg"])

        img = Image.new("RGB", (W, H), bg)

        slide_num = slide_data.get("slide_number", 1)
        total = slide_data.get("total_slides", 1)

        # декорации: accent — мягкое свечение; minimal — без декора; editorial — по ротации
        if not photo_path:
            if slide_style == "accent":
                deco = "glow"
                deco_theme = "hot"
            elif slide_style == "minimal":
                deco = None
                deco_theme = theme
            else:
                deco = slide_data.get("bg") or self._pick_decoration(slide_num)
                deco_theme = theme
            if deco:
                self._draw_decoration(img, deco_theme, deco, slide_num)

        draw = ImageDraw.Draw(img)
        label = sanitize_text(slide_data.get("label", ""))
        title = sanitize_text(slide_data.get("title", ""))
        body_lines = [sanitize_text(l) for l in slide_data.get("body_lines", [])]
        accent_word = slide_data.get("accent_word")
        visual_type = slide_data.get("visual_type", "none")
        visual_data = slide_data.get("visual_data", {})
        topic = slide_data.get("topic", "")
        right_label = slide_data.get("right_label", "")
        cta_pill = slide_data.get("cta_pill", "")

        # страховка от дублей: не показываем в body_lines то, что уже есть в визуале
        if body_lines and visual_type not in ("none", "", None):
            body_lines = dedupe_body(body_lines, visual_data)

        # обложка на фоне фото
        if photo_mode == "cover_bg" and photo_path:
            if self._draw_photo_cover(img, theme, slide_data, username, photo_path):
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                img.save(tmp.name, "PNG")
                return tmp.name
            photo_path = None
            self._draw_decoration(img, effective_theme, "glow", slide_num)
            draw = ImageDraw.Draw(img)

        self._draw_top_bar(draw, effective_theme, username, topic, right_label)

        # photo+text is a full-layout override
        if visual_type == "photo_text" and photo_path:
            self._draw_badge(draw, effective_theme, slide_num, label, 220)
            self._draw_photo_text_layout(
                img, draw, effective_theme, photo_path,
                visual_data.get("photo_label", label),
                title, accent_word,
                visual_data.get("bullet_items", body_lines),
                310
            )
            self._draw_bottom_bar(draw, effective_theme, slide_num, total, slide_num == total, username)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(tmp.name, "PNG")
            return tmp.name

        is_cover = slide_num == 1
        has_visual = visual_type not in ("none", "", None) and not photo_path

        y = 168
        if label or slide_num:
            self._draw_badge(draw, effective_theme, slide_num, label, y)
            y += 92

        # размер заголовка: minimal — огромный; обложка — крупная; остальные стандарт
        if slide_style == "minimal":
            headline_size = 120
        elif is_cover:
            headline_size = 104
        elif has_visual:
            headline_size = 78
        else:
            headline_size = 88

        y = self._draw_headline(draw, effective_theme, title, accent_word, y, W - PAD * 2, font_size=headline_size, use_display=True)
        y += 20

        if has_visual and title:
            y = self._draw_divider(draw, effective_theme, y) + 30
        else:
            y += 14

        if body_lines and slide_style != "minimal":
            if photo_path:
                body_max_y = int(H * 0.50)
            elif has_visual:
                body_max_y = None
            else:
                body_max_y = H - 130
            y = self._draw_body(draw, effective_theme, body_lines, y, max_y=body_max_y)
            y += 24

        if photo_path:
            self._draw_photo_block(img, effective_theme, photo_path, y + 8, H - 130)
            draw = ImageDraw.Draw(img)
            visual_type = "none"

        if visual_type == "table" and visual_data.get("rows"):
            y = self._draw_table(draw, effective_theme, visual_data["rows"], y)
        elif visual_type == "cards" and visual_data.get("cards"):
            y = self._draw_cards_2x2(draw, effective_theme, visual_data["cards"], y)
        elif visual_type == "comparison" and visual_data.get("left"):
            y = self._draw_comparison(draw, effective_theme, visual_data["left"], visual_data["right"], y)
        elif visual_type == "terminal" and visual_data.get("lines"):
            y = self._draw_terminal(draw, effective_theme, visual_data.get("title", ""), visual_data["lines"], y)
        elif visual_type == "checklist" and visual_data.get("items"):
            y = self._draw_checklist(draw, effective_theme, visual_data.get("title", ""), visual_data["items"], y)
        elif visual_type == "numbered_list" and visual_data.get("items"):
            y = self._draw_numbered_list(draw, effective_theme, visual_data["items"], y)
        elif visual_type == "bullet_list" and visual_data.get("items"):
            y = self._draw_bullet_list(draw, effective_theme, visual_data["items"], y, visual_data.get("bullet_color"))
        elif visual_type == "progress_bar":
            y = self._draw_progress_bar(
                draw, effective_theme,
                visual_data.get("label_before", "было"),
                visual_data.get("value_before", ""),
                visual_data.get("label_after", "стало"),
                visual_data.get("value_after", ""),
                y
            )
        elif visual_type == "file_tree" and visual_data.get("rows"):
            y = self._draw_file_tree(draw, effective_theme,
                visual_data.get("header", ""),
                visual_data.get("badge", ""),
                visual_data["rows"], y)
        elif visual_type == "big_stat" and visual_data.get("stats"):
            y = self._draw_big_stat(draw, effective_theme, visual_data["stats"], y)
        elif visual_type == "quote":
            y = self._draw_quote(draw, effective_theme,
                visual_data.get("text", ""),
                visual_data.get("author", ""), y)
        elif visual_type == "number" and visual_data.get("big_number"):
            y = self._draw_hero_number(draw, effective_theme,
                visual_data.get("big_number", ""),
                visual_data.get("caption", ""), y)
        elif visual_type == "timeline" and visual_data.get("steps"):
            y = self._draw_timeline(draw, effective_theme, visual_data["steps"], y)
        elif visual_type == "two_col":
            y = self._draw_two_col(draw, effective_theme,
                visual_data.get("left_title", ""),
                visual_data.get("left_items", []),
                visual_data.get("right_title", ""),
                visual_data.get("right_items", []), y)
        elif visual_type == "pull_quote":
            y = self._draw_pull_quote(draw, effective_theme,
                visual_data.get("text", ""),
                visual_data.get("author", ""), y)
        elif visual_type == "stat_grid" and visual_data.get("stats"):
            y = self._draw_stat_grid(draw, effective_theme, visual_data["stats"], y)
        elif visual_type == "highlight_card":
            y = self._draw_highlight_card(draw, effective_theme,
                visual_data.get("heading", ""),
                visual_data.get("subtext", ""), y)
        elif visual_type == "steps_flow" and visual_data.get("steps"):
            y = self._draw_steps_flow(draw, effective_theme, visual_data["steps"], y)
        elif visual_type == "magazine_split":
            y = self._draw_magazine_split(draw, effective_theme,
                visual_data.get("big_label", ""),
                visual_data.get("items", []), y)

        if cta_pill:
            self._draw_cta_pill(draw, effective_theme, cta_pill, y + 8)

        self._draw_bottom_bar(draw, effective_theme, slide_num, total, slide_num == total, username)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        return tmp.name

    # ── обложка карусели: фото на весь слайд + хук поверх ───────────────────────

    def _draw_photo_cover(self, img, theme, slide_data, username, photo_path):
        """1-й слайд карусели на фоне фото, светлый текст поверх.
        True — фото подошло; False — фото мелкое (рисуем обложку обычным способом)."""
        if not self._cover_photo_bg(img, photo_path, W, H):
            return False

        t = THEMES[theme]
        accent = hex_to_rgb(t["accent"])
        white = (255, 255, 255)
        light = (224, 221, 216)
        draw = ImageDraw.Draw(img)

        slide_num = slide_data.get("slide_number", 1)
        total = slide_data.get("total_slides", 1)
        title = slide_data.get("title", "")
        accent_word = slide_data.get("accent_word")
        body_lines = slide_data.get("body_lines", [])
        topic = slide_data.get("topic", "")
        right_label = slide_data.get("right_label", "") or topic

        # шапка
        name_font = self._med(27)
        draw.text((PAD, 58), username, font=name_font, fill=white)
        rt = (right_label or "").upper()
        if rt:
            lf = self._med(23)
            rw = self._tracked_w(draw, rt, lf, 3)
            self._draw_tracked(draw, (W - PAD - rw, 62), rt, lf, light, 3)

        # хук — Oswald Bold для editorial-удара; прижат к низу (там темнее всего)
        size = 96
        font = self._oswald(size, "Bold")
        max_w = W - PAD * 2
        words = title.split()
        lines, cur = [], []
        for w in words:
            test = " ".join(cur + [w])
            if self._tw(draw, test, font) > max_w and cur:
                lines.append(" ".join(cur)); cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))

        line_h = int(size * 1.18)
        body_block = (len(body_lines[:2]) * 52 + 24) if body_lines else 0
        start_y = (H - 150) - len(lines) * line_h - body_block
        start_y = max(start_y, 360)

        y = start_y
        for line in lines:
            if accent_word and accent_word.lower() in line.lower():
                idx = line.lower().find(accent_word.lower())
                before = line[:idx]
                acc = line[idx:idx + len(accent_word)]
                after = line[idx + len(accent_word):]
                cx = PAD
                if before:
                    draw.text((cx, y), before, font=font, fill=white); cx += self._tw(draw, before, font)
                draw.text((cx, y), acc, font=font, fill=accent); cx += self._tw(draw, acc, font)
                if after:
                    draw.text((cx, y), after, font=font, fill=white)
            else:
                draw.text((PAD, y), line, font=font, fill=white)
            y += line_h

        if body_lines:
            y += 24
            bf = self._reg(38)
            max_w = W - PAD * 2
            for bl in body_lines[:2]:
                for wrapped in self._wrap(draw, bl, bf, max_w):
                    draw.text((PAD, y), wrapped, font=bf, fill=light)
                    y += 52

        # футер
        cf = self._med(25)
        self._draw_tracked(draw, (PAD, H - 76), f"{slide_num:02d} / {total:02d}", cf, light, 2)
        cta = "ЛИСТАЙ →"
        cw = self._tracked_w(draw, cta, cf, 3)
        self._draw_tracked(draw, (W - PAD - cw, H - 76), cta, cf, light, 3)
        return True

    # ── кликбейтная обложка для Reels/Shorts (9:16) ────────────────────────────

    def _cover_photo_bg(self, img, photo_path, CW, CH):
        """Фото на весь экран обложки + тёмный оверлей. Возвращает True при успехе."""
        try:
            photo = Image.open(photo_path).convert("RGB")
        except Exception:
            return False
        nw, nh = photo.size
        # качество: если фото пришлось бы сильно растягивать — отказываемся от фона
        cover_scale = max(CW / nw, CH / nh)
        if cover_scale > 1.7:
            return False
        new_w, new_h = int(nw * cover_scale), int(nh * cover_scale)
        photo = photo.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - CW) // 2
        top = (new_h - CH) // 2
        photo = photo.crop((left, top, left + CW, top + CH))
        img.paste(photo, (0, 0))
        # тёмный оверлей снизу→вверх, чтобы крупный текст читался
        overlay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(CH):
            a = int(60 + 150 * (y / CH))   # сверху светлее, снизу темнее
            od.line([(0, y), (CW, y)], fill=(0, 0, 0, a))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))
        return True

    def _cover_glow(self, img, theme, CW, CH):
        accent = hex_to_rgb(THEMES[theme]["accent"])
        overlay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        d.ellipse([CW - 760, -260, CW + 360, 860], fill=accent + (55,))
        d.ellipse([-360, CH - 860, 760, CH + 260], fill=accent + (45,))
        overlay = overlay.filter(ImageFilter.GaussianBlur(90))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    def _cover_composition(self, draw, lead, punch, tail, main, sub, accent, CW, top, bottom):
        """Редакционная композиция обложки.
        ch06 Dominance: punch — единственный доминант, lead/tail подчинены.
        ch07 Proximity: lead прилипает к punch (gap=16px) — рубрика, не отдельный элемент.
        ai-tells: левое выравнивание создаёт editorial ось вместо дефолтной центровки.
        Якорь в нижней трети — там оверлей темнее, текст читается лучше."""
        pad = 70
        x = pad                  # левая editorial ось
        max_w = CW - pad * 2
        avail = bottom - top

        # Lead — Inter-Regular, маленький (шёпот/рубрика над punch)
        lead_font = self._reg(36)
        lead_lh = 48
        lead_lines = self._wrap(draw, lead, lead_font, max_w) if lead else []
        lead_h = len(lead_lines) * lead_lh

        # Tail — Inter-Medium (не Bold): подчинённый финал, не конкурент punch (ch06)
        tail_font = self._med(52)
        tail_lh = 66
        tail_lines = self._wrap(draw, tail, tail_font, max_w) if tail else []
        tail_h = len(tail_lines) * tail_lh

        gap_lead = 16    # lead прилипает к punch — они одна мысль (ch07 proximity)
        gap_tail = 76    # дыхание после punch — отделяет вывод (ch07 white space)

        # бюджет высоты под punch
        reserved = lead_h + (gap_lead if lead else 0) + tail_h + (gap_tail if tail else 0)
        punch_budget = max(avail - reserved, 280)

        punch_up = (punch or "").upper()
        p_size, p_lines, p_lh = 110, [punch_up], 110
        for fs in (230, 212, 196, 180, 164, 148, 134, 120, 108):
            pf = self._oswald(fs, "Bold")
            lines = self._wrap(draw, punch_up, pf, max_w)
            lh = int(fs * 1.02)
            widest = max((self._tw(draw, ln, pf) for ln in lines), default=0)
            if widest <= max_w and len(lines) * lh <= punch_budget:
                p_size, p_lines, p_lh = fs, lines, lh
                break
        pf = self._oswald(p_size, "Bold")
        punch_h = len(p_lines) * p_lh

        total = lead_h + (gap_lead if lead else 0) + punch_h + (gap_tail if tail else 0) + tail_h

        # Якорь в нижней трети (65%): не по центру — центровка без оси = AI-tell
        y = top + int((avail - total) * 0.65)
        y = min(y, bottom - total - 60)
        y = max(y, top + 40)

        # Всё левое выравнивание — editorial ось
        for ln in lead_lines:
            draw.text((x, y), ln, font=lead_font, fill=sub); y += lead_lh
        if lead:
            y += gap_lead
        for ln in p_lines:
            draw.text((x, y), ln, font=pf, fill=accent); y += p_lh
        if tail:
            y += gap_tail
        for ln in tail_lines:
            draw.text((x, y), ln, font=tail_font, fill=main); y += tail_lh

    def generate_cover(self, cover_data, theme="fuchsia", username="@username", photo_path=None):
        """Одна кликбейтная вертикальная обложка 1080×1920 для Reels/Shorts."""
        CW, CH = 1080, 1920
        t = THEMES.get(theme, THEMES["fuchsia"])
        bg = hex_to_rgb(t["bg"])
        img = Image.new("RGB", (CW, CH), bg)

        kicker = (cover_data.get("kicker") or "").strip()
        lead = (cover_data.get("lead") or "").strip()
        punch = (cover_data.get("punch") or cover_data.get("headline") or "").strip()
        tail = (cover_data.get("tail") or "").strip()

        on_photo = False
        if photo_path:
            on_photo = self._cover_photo_bg(img, photo_path, CW, CH)
        if not on_photo:
            self._cover_glow(img, theme, CW, CH)

        draw = ImageDraw.Draw(img)
        accent = hex_to_rgb(t["accent"])
        if on_photo:
            main = (255, 255, 255)
            sub = (215, 212, 208)
            kicker_text_col = hex_to_rgb(t["bg"])
            user_col = (255, 255, 255)
        else:
            main = hex_to_rgb(t["text"])
            sub = hex_to_rgb(t["text_dim"])
            kicker_text_col = hex_to_rgb(t["bg"])
            user_col = hex_to_rgb(t["text_dim"])

        # верхняя плашка-кикер (заливка акцентом)
        # Oswald — та же гарнитура что у punch: кикер и заголовок = одна семья «объявлений» (Appendix B)
        top_zone = 280
        if kicker:
            kf = self._oswald(32, "Bold")
            kt = kicker.upper()
            kw = self._tracked_w(draw, kt, kf, 3)
            ph, pv = 30, 18
            pill_w = kw + ph * 2
            pill_h = 72
            px = (CW - pill_w) // 2
            py = 150
            draw.rounded_rectangle([px, py, px + pill_w, py + pill_h], radius=pill_h // 2, fill=accent)
            self._draw_tracked(draw, (px + ph, py + pv), kt, kf, kicker_text_col, 3)
            top_zone = py + pill_h + 40

        # композиция заголовка — иерархия размеров + шрифтовая пара
        self._cover_composition(draw, lead, punch, tail, main, sub, accent, CW, top_zone, CH - 230)

        # ник снизу по левой оси — метаданные, Inter-Regular 34px
        uf = self._reg(34)
        draw.text((70, CH - 150), username, font=uf, fill=user_col)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        return tmp.name
