"""Tokens de tema do Chat CAD (derivados do tema ativo do FreeCAD)."""
import os
import re

try:
    from PySide import QtCore, QtGui, QtWidgets
except ImportError:                                   # testes offscreen fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtWidgets
try:
    import FreeCAD as App
except ImportError:
    App = None

HERE = os.path.dirname(os.path.abspath(__file__))
UI_FAMILIES = ["Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Noto Sans", "Arial"]
MONO_FAMILIES = ["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Menlo", "monospace"]

DARK = {
    "bg": "#191919", "title": "#1e1e1e", "surface": "#1f1f1f", "raised": "#242424",
    "hover": "#2b2b2b", "active": "#353535",
    "border": "#272727", "border2": "#343434", "border3": "#4b4b4b",
    "text": "#ececec", "text2": "#b8b8b8", "text3": "#949494", "text4": "#666666",
    "brand": "#f0884f", "brand_soft": "#2e2119", "success": "#3fb950",
    "danger": "#f85149", "danger_text": "#ffb4ab", "danger_soft": "#2a1a1a", "danger_border": "#5a2b2b",
    "warn": "#e3b341", "warn_text": "#ecd9b0", "warn_soft": "#26211a", "warn_border": "#4a3c22",
    "info": "#79b8ff", "info_text": "#b6d6ff", "info_soft": "#182230", "info_border": "#2b4566",
    "inverse": "#ececec", "inverse_text": "#141414", "inverse_hover": "#ffffff",
    "code_bg": "#141414", "code_inline": "#262626", "code_text": "#d4d4d4", "code_accent": "#f2b58f",
    "scroll": "rgba(255, 255, 255, 0.10)", "scroll_hover": "rgba(255, 255, 255, 0.22)",
    "scrim": "rgba(0, 0, 0, 0.72)",
}
LIGHT = {
    "bg": "#f5f5f5", "title": "#ebebeb", "surface": "#ffffff", "raised": "#f0f0f0",
    "hover": "#e8e8e8", "active": "#dedede",
    "border": "#e2e2e2", "border2": "#d0d0d0", "border3": "#b0b0b0",
    "text": "#1d1d1d", "text2": "#444444", "text3": "#666666", "text4": "#9a9a9a",
    "brand": "#c4561d", "brand_soft": "#fbe7da", "success": "#1a7f37",
    "danger": "#cf222e", "danger_text": "#82071e", "danger_soft": "#ffebe9", "danger_border": "#f5b8b1",
    "warn": "#9a6700", "warn_text": "#5c4300", "warn_soft": "#fff6e0", "warn_border": "#ecd49a",
    "info": "#0969da", "info_text": "#0a3069", "info_soft": "#ddf4ff", "info_border": "#a8d4f5",
    "inverse": "#1d1d1d", "inverse_text": "#ffffff", "inverse_hover": "#000000",
    "code_bg": "#eeeeee", "code_inline": "#e6e6e6", "code_text": "#2b2b2b", "code_accent": "#a4410f",
    "scroll": "rgba(0, 0, 0, 0.14)", "scroll_hover": "rgba(0, 0, 0, 0.28)",
    "scrim": "rgba(0, 0, 0, 0.60)",
}
T = {}          # tokens ativos (preenchido por tokens())


def _mix(a, b, t):
    a, b = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(round(a.red() + (b.red() - a.red()) * t),
                        round(a.green() + (b.green() - a.green()) * t),
                        round(a.blue() + (b.blue() - a.blue()) * t)).name()


def _host_accent():
    rgba = 0x4AA5FFFF
    if App is not None:
        try:
            rgba = App.ParamGet("User parameter:BaseApp/Preferences/Themes").GetUnsigned(
                "ThemeAccentColor1", 0) or rgba
        except Exception:
            pass
    return "#%06x" % ((rgba >> 8) & 0xFFFFFF)


def is_dark():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return True
    return app.palette().color(QtGui.QPalette.Window).lightness() < 128


def tokens(dark=None):
    if dark is None:
        dark = is_dark()
    t = dict(DARK if dark else LIGHT)
    t["focus"] = _host_accent()
    t["selection"] = _mix(t["bg"], t["focus"], 0.35)
    t["link"] = _mix(t["focus"], "#ffffff", 0.35) if dark else _mix(t["focus"], "#000000", 0.25)
    t["dark"] = dark
    T.clear()
    T.update(t)
    return T


def ensure():
    """Tokens prontos (para widgets criados fora do install(), ex.: testes e diálogos)."""
    return T or tokens()


def load_qss(path=None, t=None):
    t = t or ensure()
    with open(path or os.path.join(HERE, "chatcad.qss"), encoding="utf-8") as f:
        css = f.read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)            # comentários citam @tokens

    def sub(m):
        k = m.group(1)
        if k in t and isinstance(t[k], str):
            return t[k]
        if App is not None:
            try:
                App.Console.PrintWarning(f"Chat CAD: token QSS desconhecido @{k}\n")
            except Exception:
                pass
        return m.group(0)
    return re.sub(r"@([a-z][a-z0-9_]*)", sub, css)


def ui_font(px=13, weight=None):
    f = QtGui.QFont()
    f.setFamilies(UI_FAMILIES)
    f.setPixelSize(px)
    if weight:
        f.setWeight(weight)
    return f


def mono_font(px=12):
    f = QtGui.QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPixelSize(px)
    f.setStyleHint(QtGui.QFont.Monospace)
    return f


def popup_menu(parent):
    """QMenu com cantos arredondados de verdade."""
    m = QtWidgets.QMenu(parent)
    m.setWindowFlags(m.windowFlags() | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint)
    m.setAttribute(QtCore.Qt.WA_TranslucentBackground)
    return m


def set_prop(w, name, value):
    """Propriedade dinâmica + repolish (só quando muda)."""
    if w.property(name) != value:
        w.setProperty(name, value)
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()


def fmt_secs(s):
    s = int(round(s))
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s"
