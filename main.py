# -*- coding: utf-8 -*-
"""
PhotoToCode 1.0 — превращает любую картинку в код.

Режимы:
  * base64 -> HTML / PHP / CSS / чистая строка
  * ASCII-арт
  * SVG — цветная или чёрно-белая векторизация контуров

Трейсер написан на чистом Python, поэтому для SVG поле ограничено
320 px (SVG_MAX_SIDE) — иначе обработка занимает минуты, особенно на телефоне.

Запуск на ПК:   python main.py
Сборка EXE:     pyinstaller --onefile --windowed --name PhotoToCode main.py
Сборка APK:     buildozer android debug
"""

import base64
import io
import os
import threading
from datetime import datetime

from PIL import Image, ImageOps

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

APP_TITLE = "PhotoToCode"
PREVIEW_LIMIT = 20000
SVG_MAX_SIDE = 320          # предел поля для чистого Python-трейсера
SVG_DEFAULT_SIDE = 240      # что подставляется при входе в SVG-режим

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:          # старый Pillow
    RESAMPLE = Image.LANCZOS

IMAGE_FILTERS = [
    "*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif",
    "*.tif", "*.tiff", "*.ico", "*.ppm", "*.pgm", "*.heic",
]

MODES = [
    ("base64_html", "base64 -> HTML"),
    ("base64_php", "base64 -> PHP"),
    ("base64_css", "base64 -> CSS"),
    ("base64_raw", "base64 -> чистая строка"),
    ("ascii", "ASCII-арт"),
    ("svg_color", "SVG — цветной"),
    ("svg_bw", "SVG — чёрно-белый"),
]


# =====================================================================
#  Файловые пути (Android и Windows)
# =====================================================================
def _android_storage():
    if platform == "android":
        try:
            from android.storage import primary_external_storage_path
            return primary_external_storage_path()
        except Exception:
            return "/storage/emulated/0"
    return None


def default_dir():
    base = _android_storage()
    if base:
        for sub in ("Pictures", "Download", "DCIM"):
            p = os.path.join(base, sub)
            if os.path.isdir(p):
                return p
        return base
    return os.path.expanduser("~")


def out_dir():
    base = _android_storage()
    if base:
        d = os.path.join(base, "Download", "PhotoToCode")
    else:
        d = os.path.join(os.path.expanduser("~"), "PhotoToCode")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        try:
            return App.get_running_app().user_data_dir
        except Exception:
            return os.getcwd()


# =====================================================================
#  Ядро: картинка -> код
# =====================================================================
def load_image(path, max_side=0):
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)      # поворот по EXIF
    except Exception:
        pass
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), RESAMPLE)
    return img


def image_to_base64(path, fmt="jpeg", max_side=1200, quality=85):
    """→ (строка base64, mime, размер исходных байт)"""
    img = load_image(path, max_side)
    if fmt == "png":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        mime = "image/png"
    else:
        if img.mode != "RGB":
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
        mime = "image/jpeg"

    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    return base64.b64encode(data).decode("ascii"), mime, len(data)


def wrap_html(b64, mime):
    return ("<!-- Вставьте этот код в любое место HTML-страницы -->\n"
            '<img src="data:%s;base64,%s" alt="моя картинка">' % (mime, b64))


def wrap_php(b64, mime):
    return ("<?php\n"
            "// Готовый data-URI вместо файла картинки\n"
            "$photo = 'data:%s;base64,%s';\n"
            "?>\n"
            "<!DOCTYPE html>\n"
            "<html lang=\"ru\">\n"
            "<head><meta charset=\"utf-8\"><title>Фото</title></head>\n"
            "<body>\n"
            "<img src=\"<?php echo $photo; ?>\" alt=\"фото\">\n"
            "</body>\n"
            "</html>" % (mime, b64))


def wrap_css(b64, mime, selector=".photo"):
    return ("/* Фон из картинки — отдельный файл не нужен */\n"
            "%s {\n"
            "  background-image: url(\"data:%s;base64,%s\");\n"
            "  background-size: cover;\n"
            "  background-position: center;\n"
            "  width: 100%%;\n"
            "  height: 100%%;\n"
            "}" % (selector, mime, b64))


ASCII_RAMP = "@%#*+=-:. "        # от тёмного к светлому


def image_to_ascii(path, width=100):
    img = load_image(path, 0).convert("L")
    w, h = img.size
    width = max(1, min(int(width), w))
    # символ примерно в 2 раза выше, чем шире -> поправка 0.5
    new_h = max(1, int(h * width / float(w) * 0.5))
    img = img.resize((width, new_h), RESAMPLE)
    px = img.load()
    last = len(ASCII_RAMP) - 1
    lines = []
    for y in range(new_h):
        row = []
        for x in range(width):
            row.append(ASCII_RAMP[px[x, y] * last // 255])
        lines.append("".join(row).rstrip())
    return "\n".join(lines)


# ------------------------- SVG: трейсинг контуров --------------------
def _link(adj, a, b):
    lst = adj.get(a)
    if lst is None:
        adj[a] = [b]
    else:
        lst.append(b)


def _trace(mask, w, h):
    """Обходит границы бинарной маски, возвращает замкнутые контуры."""
    adj = {}
    for y in range(h):
        row = mask[y]
        up = mask[y - 1] if y > 0 else None
        down = mask[y + 1] if y + 1 < h else None
        for x in range(w):
            if not row[x]:
                continue
            left = row[x - 1] if x > 0 else 0
            right = row[x + 1] if x + 1 < w else 0
            upv = up[x] if up is not None else 0
            downv = down[x] if down is not None else 0
            if not upv:
                _link(adj, (x, y), (x + 1, y))
            if not right:
                _link(adj, (x + 1, y), (x + 1, y + 1))
            if not downv:
                _link(adj, (x + 1, y + 1), (x, y + 1))
            if not left:
                _link(adj, (x, y + 1), (x, y))

    contours = []
    for start in list(adj.keys()):
        while adj.get(start):
            path = [start]
            nxt = adj[start].pop()
            guard = 0
            while nxt is not None and nxt != start and guard < 2000000:
                path.append(nxt)
                nbrs = adj.get(nxt)
                nxt = nbrs.pop() if nbrs else None
                guard += 1
            if len(path) >= 4:
                contours.append(path)
    return contours


def _drop_collinear(pts):
    n = len(pts)
    if n < 3:
        return pts
    out = []
    for i in range(n):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (x1 - x0) * (y2 - y1) != (y1 - y0) * (x2 - x1):
            out.append(pts[i])
    return out if len(out) >= 3 else pts


def _dp(pts, eps):
    """Упрощение линии Дугласа—Пекера."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        best = -1.0
        idx = -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if norm == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best = d
                idx = k
        if best > eps and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [pts[k] for k in range(n) if keep[k]]


def image_to_svg(path, max_side=SVG_DEFAULT_SIDE, colors=8, eps=0.9):
    """Цветная векторизация: квантование цветов + обводка контуров."""
    max_side = max(80, min(int(max_side), SVG_MAX_SIDE))
    img = load_image(path, max_side).convert("RGB")
    w, h = img.size
    q = img.quantize(colors=max(2, min(256, int(colors))))
    pal = q.getpalette() or []
    px = q.load()

    counts = {}
    for y in range(h):
        for x in range(w):
            i = px[x, y]
            counts[i] = counts.get(i, 0) + 1
    if not counts:
        raise ValueError("не удалось прочитать картинку")

    order = sorted(counts.keys(), key=lambda k: -counts[k])

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
           % (w, h, w, h)]

    for idx in order:
        mask = [[1 if px[x, y] == idx else 0 for x in range(w)] for y in range(h)]
        base = idx * 3
        if base + 2 >= len(pal):
            r = g = b = 0
        else:
            r, g, b = pal[base], pal[base + 1], pal[base + 2]
        color = "#%02x%02x%02x" % (r, g, b)

        cmds = []
        for contour in _trace(mask, w, h):
            pts = _drop_collinear(contour)
            simp = _dp(pts + [pts[0]], eps)
            if len(simp) >= 4:
                simp = simp[:-1]
            else:
                simp = pts
            if len(simp) < 3:
                continue
            seg = ["M%d %d" % simp[0]]
            for p in simp[1:]:
                seg.append("L%d %d" % p)
            seg.append("Z")
            cmds.append("".join(seg))

        if cmds:
            out.append('<path d="%s" fill="%s" stroke="%s" stroke-width="0.7" '
                       'stroke-linejoin="round"/>' % ("".join(cmds), color, color))

    out.append("</svg>")
    return "\n".join(out)


def convert(path, mode, params):
    """→ (текст, расширение файла, примечание)"""
    size = int(params.get("size", 1200))
    colors = int(params.get("colors", 8))
    ascii_w = int(params.get("ascii_width", 100))

    if mode == "base64_html":
        b64, mime, n = image_to_base64(path, "jpeg", size, 85)
        return wrap_html(b64, mime), "html", "Сжатая JPEG, %d КБ" % (n // 1024)
    if mode == "base64_php":
        b64, mime, n = image_to_base64(path, "jpeg", size, 85)
        return wrap_php(b64, mime), "php", "Сжатая JPEG, %d КБ" % (n // 1024)
    if mode == "base64_css":
        b64, mime, n = image_to_base64(path, "jpeg", size, 85)
        return wrap_css(b64, mime), "css", "Сжатая JPEG, %d КБ" % (n // 1024)
    if mode == "base64_raw":
        b64, mime, n = image_to_base64(path, "png", size, 85)
        return b64, "txt", "PNG без потерь, %d КБ" % (n // 1024)
    if mode == "ascii":
        text = image_to_ascii(path, ascii_w)
        return text, "txt", "Ширина %d символов" % ascii_w
    if mode == "svg_bw":
        side = max(80, min(size, SVG_MAX_SIDE))
        text = image_to_svg(path, side, 2)
        return text, "svg", "2 цвета, поле %d px" % side
    if mode == "svg_color":
        side = max(80, min(size, SVG_MAX_SIDE))
        text = image_to_svg(path, side, colors)
        return text, "svg", "%d цветов, поле %d px" % (colors, side)
    raise ValueError("неизвестный режим: %s" % mode)


# =====================================================================
#  Интерфейс
# =====================================================================
class PhotoToCode(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=dp(8),
                         spacing=dp(6), **kw)
        self.source_path = ""
        self.result_text = ""
        self.result_ext = "txt"
        self._b64_size = 0          # детализация, которую вернуть из SVG-режима
        self._build()

    # ---------------------------------------------------------- сборка UI
    def _build(self):
        self.add_widget(Label(
            text="[b]PhotoToCode[/b]\nкартинка -> код",
            markup=True, halign="center", size_hint_y=None, height=dp(54)))

        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        btn_pick = Button(text="Выбрать картинку")
        btn_pick.bind(on_release=lambda *_: self.open_chooser())
        row.add_widget(btn_pick)
        self.lbl_file = Label(text="файл не выбран", halign="left", valign="middle")
        self.lbl_file.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
        row.add_widget(self.lbl_file)
        self.add_widget(row)

        self.spinner = Spinner(text=MODES[0][1], values=[m[1] for m in MODES],
                               size_hint_y=None, height=dp(46))
        self.spinner.bind(text=self.on_mode_change)
        self.add_widget(self.spinner)

        self.row_size, self.sl_size, self.lbl_size = self._slider(
            "Детализация, px", 60, 1600, 1200, 20)
        self.add_widget(self.row_size)
        self.row_colors, self.sl_colors, _ = self._slider("Цветов SVG", 2, 16, 8, 1)
        self.add_widget(self.row_colors)
        self.row_ascii, self.sl_ascii, _ = self._slider("Ширина ASCII", 40, 220, 100, 5)
        self.add_widget(self.row_ascii)

        row_btn = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        btn_go = Button(text="Преобразовать", background_color=(0.2, 0.6, 1, 1))
        btn_go.bind(on_release=lambda *_: self.run_convert())
        row_btn.add_widget(btn_go)
        btn_copy = Button(text="Копировать")
        btn_copy.bind(on_release=lambda *_: self.copy_result())
        row_btn.add_widget(btn_copy)
        btn_save = Button(text="Сохранить")
        btn_save.bind(on_release=lambda *_: self.save_result())
        row_btn.add_widget(btn_save)
        self.add_widget(row_btn)

        self.txt = TextInput(text="", readonly=True, font_size=dp(11),
                             size_hint_y=1)
        self.add_widget(self.txt)

        self.lbl_status = Label(text="Готово. Выберите картинку.",
                                size_hint_y=None, height=dp(40),
                                halign="left", valign="middle")
        self.lbl_status.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
        self.add_widget(self.lbl_status)

        self.on_mode_change(self.spinner, self.spinner.text)

    def _slider(self, title, mn, mx, val, step):
        box = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        lbl = Label(text=title, size_hint_x=None, width=dp(150),
                    halign="left", valign="middle")
        lbl.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
        sl = Slider(min=mn, max=mx, value=val, step=step)
        box.add_widget(lbl)
        box.add_widget(sl)
        return box, sl, lbl

    # ------------------------------------------------------------ логика
    def current_mode(self):
        for key, title in MODES:
            if title == self.spinner.text:
                return key
        return MODES[0][0]

    def on_mode_change(self, *args):
        mode = self.current_mode()
        is_svg = mode in ("svg_color", "svg_bw")
        need_size = mode in ("base64_html", "base64_php", "base64_css",
                             "base64_raw", "svg_color", "svg_bw")
        self.row_size.disabled = not need_size
        self.row_colors.disabled = mode != "svg_color"
        self.row_ascii.disabled = mode != "ascii"

        # У SVG-трейсера своё поле: запоминаем «большое» значение и вернём его
        if is_svg:
            if self.sl_size.value > SVG_MAX_SIDE:
                self._b64_size = self.sl_size.value
                self.sl_size.value = SVG_DEFAULT_SIDE
            self.lbl_size.text = "Детализация, px (SVG ≤ %d)" % SVG_MAX_SIDE
        else:
            if self._b64_size:
                self.sl_size.value = self._b64_size
                self._b64_size = 0
            self.lbl_size.text = "Детализация, px"

    def set_status(self, text):
        self.lbl_status.text = text

    def set_source(self, path):
        self.source_path = path
        self.lbl_file.text = os.path.basename(path)
        self.set_status("Выбрано: %s" % path)

    def open_chooser(self):
        box = BoxLayout(orientation="vertical", spacing=dp(6))
        fc = FileChooserListView(path=default_dir(), filters=IMAGE_FILTERS)
        box.add_widget(fc)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        ok = Button(text="Открыть")
        cancel = Button(text="Отмена")
        row.add_widget(ok)
        row.add_widget(cancel)
        box.add_widget(row)
        popup = Popup(title="Выберите картинку", content=box, size_hint=(0.95, 0.95))

        def choose(*_):
            if fc.selection:
                self.set_source(fc.selection[0])
                popup.dismiss()
            else:
                self.set_status("Файл не выбран")

        ok.bind(on_release=choose)
        cancel.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def run_convert(self):
        if not self.source_path:
            self.set_status("Сначала выберите картинку")
            return
        params = {
            "size": int(self.sl_size.value),
            "colors": int(self.sl_colors.value),
            "ascii_width": int(self.sl_ascii.value),
        }
        self.set_status("Обрабатываю…")
        threading.Thread(target=self._worker,
                         args=(self.source_path, self.current_mode(), params),
                         daemon=True).start()

    def _worker(self, path, mode, params):
        try:
            text, ext, note = convert(path, mode, params)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.set_status("Ошибка: %s" % e), 0)
            return
        Clock.schedule_once(lambda dt: self._done(text, ext, note), 0)

    def _done(self, text, ext, note):
        self.result_text = text
        self.result_ext = ext
        if len(text) > PREVIEW_LIMIT:
            preview = text[:PREVIEW_LIMIT] + \
                "\n\n… показаны первые %d символов из %d. Полный результат — в файле." \
                % (PREVIEW_LIMIT, len(text))
        else:
            preview = text
        self.txt.text = preview
        self.set_status("Готово. %s. Символов: %d" % (note, len(text)))

    def copy_result(self):
        if not self.result_text:
            self.set_status("Сначала нажмите «Преобразовать»")
            return
        Clipboard.copy(self.result_text)
        self.set_status("Скопировано в буфер (%d символов)" % len(self.result_text))

    def save_result(self):
        if not self.result_text:
            self.set_status("Сначала нажмите «Преобразовать»")
            return
        name = "phototocode_%s.%s" % (
            datetime.now().strftime("%Y%m%d_%H%M%S"), self.result_ext)
        path = os.path.join(out_dir(), name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.result_text)
            self.set_status("Сохранено: %s" % path)
        except Exception as e:
            self.set_status("Не удалось сохранить: %s" % e)


class PhotoToCodeApp(App):
    title = APP_TITLE

    def build(self):
        return PhotoToCode()


if __name__ == "__main__":
    PhotoToCodeApp().run()
