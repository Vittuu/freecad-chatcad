"""Ícones do Chat CAD: SVG de traço, grade 24x24, renderizados com QtSvg.
Vários caminhos seguem os ícones Lucide (https://lucide.dev, licença ISC,
Copyright (c) Lucide Contributors). Ícones CAD (wf-*), sparkle, line e dot-* são próprios."""
try:
    from PySide import QtCore, QtGui, QtSvg, QtWidgets      # FreeCAD: shim Ext/PySide/QtSvg.py
except ImportError:
    from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

ICONS = {
    # marca
    "logo": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    # barra de título / gerais
    "new-chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M12 7v6"/><path d="M9 10h6"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "undock": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "settings": '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M8 13h8"/><path d="M8 17h5"/>',
    # estados
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.22-8.56"/>',
    "stop-circle": '<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    # controles
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{c}" stroke="none"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
    "zap": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
    "table": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 9v12"/>',
    "restore": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    # composer / anexos
    "crosshair": '<circle cx="12" cy="12" r="9"/><path d="M22 12h-4"/><path d="M6 12H2"/><path d="M12 6V2"/><path d="M12 22v-4"/>',
    "eye-off": '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c4.4 0 8.2 2.7 9.94 6.65a1 1 0 0 1 0 .7 10.8 10.8 0 0 1-1.4 2.3"/><path d="M6.6 6.6a10.75 10.75 0 0 0-4.54 5.05 1 1 0 0 0 0 .7A10.75 10.75 0 0 0 17.4 17.4"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
    "pencil": '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
    "sheet": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 3v18"/><path d="M15 3v18"/>',
    "paperclip": '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    "clipboard": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    # provedores / modos
    "cloud": '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "sparkle": '<path d="M12 3.5 13.7 9a2 2 0 0 0 1.3 1.3l5.5 1.7-5.5 1.7a2 2 0 0 0-1.3 1.3L12 20.5 10.3 15A2 2 0 0 0 9 13.7L3.5 12 9 10.3A2 2 0 0 0 10.3 9Z"/>',
    "plan": '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
    # passos das ferramentas
    "code": '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    "eye": '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    "layers": '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    "box-plus": '<path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l1-.57"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/><path d="M19 15v6"/><path d="M16 18h6"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "bulb": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/>',
    # fluxos CAD (próprios)
    "wf-bracket": '<path d="M5 3h3v13h13v3a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2Z"/><path d="M8 7.5 16.5 16"/>',
    "wf-box": '<rect x="2.5" y="4" width="19" height="4.5" rx="1"/><path d="M4 8.5V19a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8.5"/><path d="M10 12.5h4"/>',
    "wf-honeycomb": '<path d="M7.65 4 11.29 6.1v4.2L7.65 12.4 4.01 10.3V6.1Z"/><path d="M16.35 4 19.99 6.1v4.2l-3.64 2.1-3.64-2.1V6.1Z"/><path d="M12 11.5 15.64 13.6v4.2L12 19.9l-3.64-2.1v-4.2Z"/>',
    "wf-fillet": '<path d="M4 20v-8a8 8 0 0 1 8-8h8"/><path d="M12 12 6.6 6.6"/><circle cx="12" cy="12" r="1"/>',
    "wf-print": '<path d="M3 4h18"/><path d="M10 4v3l2 2.5L14 7V4"/><path d="M6 20h12"/><path d="M8 16.5h8"/><path d="M10 13h4"/>',
    "wf-hole": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5"/>',
    "wf-explain": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "wf-check": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    # janela de desenho
    "line": '<path d="M5 19 19 5"/>',
    "arrow": '<path d="M5 19 19 5"/><path d="M9 5h10v10"/>',
    "type": '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "dot-s": '<circle cx="12" cy="12" r="2" fill="{c}" stroke="none"/>',
    "dot-m": '<circle cx="12" cy="12" r="3.5" fill="{c}" stroke="none"/>',
    "dot-l": '<circle cx="12" cy="12" r="5.5" fill="{c}" stroke="none"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
_cache = {}


def _dprs():
    ds = {1.0}
    app = QtGui.QGuiApplication.instance()
    if app is not None:
        for s in app.screens():
            ds.add(round(s.devicePixelRatio(), 2))
    return sorted(ds)


def _stroke(size):                     # traço mais grosso em tamanhos pequenos
    return 2.25 if size < 14 else (2.0 if size < 18 else 1.75)


def pixmap(name, color, size=16, stroke=None, dpr=None):
    dpr = dpr or max(_dprs())
    stroke = stroke or _stroke(size)
    key = (name, color, size, stroke, dpr)
    pm = _cache.get(key)
    if pm is None:
        svg = _SVG.format(c=color, w=stroke, body=ICONS[name].replace("{c}", color)).encode("utf-8")
        r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg))
        px = max(1, int(round(size * dpr)))
        img = QtGui.QImage(px, px, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r.render(p, QtCore.QRectF(0, 0, px, px)); p.end()
        pm = QtGui.QPixmap.fromImage(img); pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def icon(name, color, size=16, hover=None, disabled=None, on=None, on_color=None, stroke=None):
    """QIcon nítido em 100–200 %. hover = modo Active/Selected (QToolButton precisa de autoRaise);
    on = ícone do estado marcado (ex.: on="check" em itens de menu checkable)."""
    from chatcad_theme import T
    hover = hover or color
    disabled = disabled or (T.get("text4") or "#666666")
    ic = QtGui.QIcon()
    for dpr in _dprs():
        for mode, col in ((QtGui.QIcon.Normal, color), (QtGui.QIcon.Active, hover),
                          (QtGui.QIcon.Selected, hover), (QtGui.QIcon.Disabled, disabled)):
            ic.addPixmap(pixmap(name, col, size, stroke, dpr), mode, QtGui.QIcon.Off)
            ic.addPixmap(pixmap(on or name, on_color or col, size, stroke, dpr), mode, QtGui.QIcon.On)
    return ic


def icon_label(name, color, size=16, parent=None):
    lab = QtWidgets.QLabel(parent); lab.setPixmap(pixmap(name, color, size)); lab.setFixedSize(size, size)
    lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    return lab


def rotated(name, color, size, angle):
    """Quadro de spinner (loader girado)."""
    base = pixmap(name, color, size)
    out = QtGui.QPixmap(base.size()); out.setDevicePixelRatio(base.devicePixelRatio()); out.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(out)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
    c = size / 2.0; p.translate(c, c); p.rotate(angle); p.translate(-c, -c); p.drawPixmap(0, 0, base); p.end()
    return out
