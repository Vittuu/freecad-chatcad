"""Tela para rabiscar por cima de uma imagem (vista do FreeCAD, print, foto ou folha em branco)."""

try:
    from PySide import QtCore, QtGui, QtWidgets          # shim do FreeCAD
except ImportError:                                       # testes offscreen fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtWidgets

import chatcad_icons as I
import chatcad_theme as TH
from chatcad_theme import T

COLORS = [("Vermelho", "#ff3b30"), ("Azul", "#2f7bff"), ("Verde", "#34c759"),
          ("Amarelo", "#ffd60a"), ("Preto", "#111111")]
SIZES = [("Fino", 3), ("Médio", 6), ("Grosso", 12)]


class Canvas(QtWidgets.QWidget):
    """Mostra a imagem ajustada ao espaço e guarda os traços numa camada do tamanho original."""

    changed = QtCore.Signal()

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.base = image.convertToFormat(QtGui.QImage.Format_ARGB32)
        self.ink = QtGui.QImage(self.base.size(), QtGui.QImage.Format_ARGB32)
        self.ink.fill(QtCore.Qt.transparent)
        self.undo_stack = []
        self.redo_stack = []
        self._pending = None          # cópia da tinta no clique; só vira passo de desfazer se algo for desenhado
        self.color = QtGui.QColor(COLORS[0][1])
        self.pen_width = SIZES[1][1]
        self.tool = "pen"  # pen | line | arrow | text
        self.drawing = False
        self.start = None
        self.last = None
        self.preview = None
        self.setMinimumSize(320, 240)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setMouseTracking(True)

    # geometria tela <-> imagem ------------------------------------------------
    def _target(self):
        img = self.base.size()
        room = (self.size() - QtCore.QSize(32, 32)).expandedTo(QtCore.QSize(1, 1))   # respiro em volta
        scaled = img.scaled(room, QtCore.Qt.KeepAspectRatio)
        x = (self.width() - scaled.width()) / 2
        y = (self.height() - scaled.height()) / 2
        return QtCore.QRectF(x, y, scaled.width(), scaled.height())

    def _to_img(self, pos):
        r = self._target()
        if self.base.isNull() or r.width() <= 0 or r.height() <= 0:     # imagem vazia: sem escala
            return QtCore.QPointF(pos)
        sx = self.base.width() / r.width()
        sy = self.base.height() / r.height()
        return QtCore.QPointF((pos.x() - r.x()) * sx, (pos.y() - r.y()) * sy)

    def _pen(self):
        # espessura proporcional à imagem, para o traço não sumir em prints grandes
        scale = max(1.0, self.base.width() / 900.0)
        pen = QtGui.QPen(self.color, self.pen_width * scale, QtCore.Qt.SolidLine,
                         QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
        return pen

    # desenho -----------------------------------------------------------------
    def _push_undo(self, snapshot=None):
        self.undo_stack.append(self.ink.copy() if snapshot is None else snapshot)
        del self.undo_stack[:-30]
        self.redo_stack = []

    def _commit_pending(self):
        """Primeiro traço de verdade desde o clique: aí sim entra no desfazer (e zera o refazer)."""
        if self._pending is not None:
            self._push_undo(self._pending)
            self._pending = None

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.ink.copy())
            self.ink = self.undo_stack.pop()
            self.update()
            self.changed.emit()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.ink.copy())
            self.ink = self.redo_stack.pop()
            self.update()
            self.changed.emit()

    def clear(self):
        self._push_undo()
        self.ink.fill(QtCore.Qt.transparent)
        self.update()
        self.changed.emit()

    def _draw_arrow(self, painter, a, b):
        painter.drawLine(a, b)
        line = QtCore.QLineF(b, a)
        if line.length() < 1:
            return
        head = max(14.0, self._pen().widthF() * 4)
        for angle in (28, -28):
            l2 = QtCore.QLineF(line)
            l2.setLength(head)
            l2.setAngle(line.angle() + angle)
            painter.drawLine(l2)

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton or self.base.isNull():
            return
        p = self._to_img(e.position())
        if self.tool == "text":
            dlg = QtWidgets.QInputDialog(self)                 # herda a folha do diálogo (ccSketch)
            dlg.setWindowTitle("Texto")
            dlg.setLabelText("O que escrever (ex.: 60 mm):")
            dlg.setOkButtonText("Inserir")
            dlg.setCancelButtonText("Cancelar")
            ok = dlg.exec() == QtWidgets.QDialog.Accepted
            text = dlg.textValue()
            if ok and text.strip():
                self._push_undo()
                painter = QtGui.QPainter(self.ink)
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                font = QtGui.QFont("Segoe UI")
                font.setBold(True)
                font.setPixelSize(int(max(16, self.base.width() / 40) * (self.pen_width / 6) ** 0.5))
                painter.setFont(font)
                painter.setPen(self.color)
                painter.drawText(p, text.strip())
                painter.end()
                self.update()
                self.changed.emit()
            return
        self._pending = self.ink.copy()       # clique sem traço não gasta desfazer nem apaga o refazer
        self.drawing = True
        self.start = self.last = p

    def mouseMoveEvent(self, e):
        if not self.drawing:
            return
        p = self._to_img(e.position())
        if self.tool == "pen":
            if p == self.last:
                return
            self._commit_pending()
            painter = QtGui.QPainter(self.ink)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(self._pen())
            painter.drawLine(self.last, p)
            painter.end()
            self.last = p
        else:
            self.preview = (self.start, p)
        self.update()

    def mouseReleaseEvent(self, e):
        if not self.drawing:
            return
        self.drawing = False
        a, b = self.preview or (None, None)
        if self.tool in ("line", "arrow") and a is not None and QtCore.QLineF(a, b).length() > 0:
            self._commit_pending()
            painter = QtGui.QPainter(self.ink)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(self._pen())
            if self.tool == "arrow":
                self._draw_arrow(painter, a, b)
            else:
                painter.drawLine(a, b)
            painter.end()
        drew = self._pending is None
        self._pending = None
        self.preview = None
        self.update()
        if drew:
            self.changed.emit()

    def paintEvent(self, _e):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(T.get("code_bg") or "#141414"))
        r = self._target()
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.drawImage(r, self.base)
        painter.drawImage(r, self.ink)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 24) if T.get("dark", True)
                                  else QtGui.QColor(0, 0, 0, 30), 1))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self.preview and self.base.width() > 0 and self.base.height() > 0:
            painter.save()
            painter.translate(r.x(), r.y())
            painter.scale(r.width() / self.base.width(), r.height() / self.base.height())
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(self._pen())
            a, b = self.preview
            if self.tool == "arrow":
                self._draw_arrow(painter, a, b)
            else:
                painter.drawLine(a, b)
            painter.restore()
        painter.end()

    def result(self):
        out = QtGui.QImage(self.base)
        painter = QtGui.QPainter(out)
        painter.drawImage(0, 0, self.ink)
        painter.end()
        return out


class Swatch(QtWidgets.QAbstractButton):
    """Bolinha de cor (QSS não desenha anel com afastamento, então é pintada à mão)."""

    def __init__(self, name, hexa, parent=None):
        super().__init__(parent)
        self.color = QtGui.QColor(hexa)
        self.setCheckable(True)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setFixedSize(24, 24)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self.setAttribute(QtCore.Qt.WA_Hover)

    def sizeHint(self):
        return QtCore.QSize(24, 24)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked() or self.underMouse() or self.hasFocus():
            ring = T["focus"] if self.hasFocus() else (T["text"] if self.isChecked() else T["border3"])
            p.setPen(QtGui.QPen(QtGui.QColor(ring), 1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(r.adjusted(0.75, 0.75, -0.75, -0.75))
        p.setPen(QtGui.QPen(QtGui.QColor(T["border3"]), 1))          # contorno: preto continua visível
        p.setBrush(self.color)
        p.drawEllipse(r.adjusted(4, 4, -4, -4))
        p.end()


def segmented(parent, items, show_text):
    """Botões exclusivos num trilho. items: (chave, ícone, rótulo, atalho)."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("ccSeg")
    frame.setAttribute(QtCore.Qt.WA_StyledBackground)
    h = QtWidgets.QHBoxLayout(frame)
    h.setContentsMargins(2, 2, 2, 2)
    h.setSpacing(2)
    group = QtWidgets.QButtonGroup(parent)
    group.setExclusive(True)
    buttons = {}
    for key, ic, label, sc in items:
        b = QtWidgets.QToolButton()
        b.setObjectName("ccSegBtn")
        b.setAutoRaise(True)
        b.setCheckable(True)
        b.setIcon(I.icon(ic, T["text2"], 16, hover=T["text"], on=ic, on_color=T["text"]))
        b.setIconSize(QtCore.QSize(16, 16))
        b.setToolTip(f"{label} ({sc})")
        b.setAccessibleName(label)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setFocusPolicy(QtCore.Qt.TabFocus)
        if show_text:
            b.setText(label)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        QtGui.QShortcut(QtGui.QKeySequence(sc), parent, activated=b.click)
        group.addButton(b)
        h.addWidget(b)
        buttons[key] = b
    frame.group = group
    return frame, buttons


def vsep():
    s = QtWidgets.QFrame()
    s.setObjectName("ccVSep")
    return s


def flat_btn(icon_name, text, tip):
    b = QtWidgets.QToolButton()
    b.setObjectName("ccFlat")
    b.setAutoRaise(True)
    b.setText(text)
    b.setIcon(I.icon(icon_name, T["text2"], 16, hover=T["text"]))
    b.setIconSize(QtCore.QSize(16, 16))
    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    b.setToolTip(tip)
    b.setAccessibleName(text)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setFocusPolicy(QtCore.Qt.TabFocus)
    return b


class SketchDialog(QtWidgets.QDialog):
    def __init__(self, image, title="Desenhar", parent=None):
        super().__init__(parent)
        TH.ensure()
        self.setWindowTitle(f"Chat CAD — {title}")
        self.setObjectName("ccSketch")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setStyleSheet(TH.load_qss())          # filho da janela principal: não herda a folha do dock
        self.setFont(TH.ui_font(13))
        self.canvas = Canvas(image)

        bar = QtWidgets.QFrame()
        bar.setObjectName("ccSketchBar")
        bar.setAttribute(QtCore.Qt.WA_StyledBackground)
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(12)
        tools, self.tool_buttons = segmented(self, [("pen", "pencil", "Caneta", "P"),
                                                    ("line", "line", "Linha", "L"),
                                                    ("arrow", "arrow", "Seta", "S"),
                                                    ("text", "type", "Texto", "T")], show_text=True)
        for k, b in self.tool_buttons.items():
            b.clicked.connect(lambda _=False, k=k: setattr(self.canvas, "tool", k))
        self.tool_buttons["pen"].setChecked(True)

        colors = QtWidgets.QHBoxLayout()
        colors.setSpacing(4)
        self.color_group = QtWidgets.QButtonGroup(self)
        self.swatches = []
        for i, (name, hexa) in enumerate(COLORS):
            s = Swatch(name, hexa)
            s.setToolTip(f"{name} ({i + 1})")
            s.setChecked(i == 0)
            self.color_group.addButton(s)
            s.clicked.connect(lambda _=False, h_=hexa: setattr(self.canvas, "color", QtGui.QColor(h_)))
            QtGui.QShortcut(QtGui.QKeySequence(str(i + 1)), self, activated=s.click)
            colors.addWidget(s)
            self.swatches.append(s)

        # espessura com Ctrl+1/2/3 (1-5 são as cores)
        size_items = [(w, ic, label, f"Ctrl+{n}")
                      for n, (ic, (label, w)) in enumerate(zip(("dot-s", "dot-m", "dot-l"), SIZES), 1)]
        sizes, self.size_buttons = segmented(self, size_items, show_text=False)
        for w_, b in self.size_buttons.items():
            b.clicked.connect(lambda _=False, w_=w_: setattr(self.canvas, "pen_width", w_))
        self.size_buttons[SIZES[1][1]].setChecked(True)

        self.undo_btn = flat_btn("undo", "Desfazer", "Desfazer (Ctrl+Z)")
        self.undo_btn.setShortcut(QtGui.QKeySequence.Undo)
        self.undo_btn.clicked.connect(self.canvas.undo)
        self.redo_btn = flat_btn("redo", "Refazer", "Refazer (Ctrl+Y)")
        self.redo_btn.setShortcut(QtGui.QKeySequence("Ctrl+Y"))
        self.redo_btn.clicked.connect(self.canvas.redo)
        self.clear_btn = clear = flat_btn("trash", "Limpar", "Apagar todos os traços")
        clear.clicked.connect(self.canvas.clear)
        self.canvas.changed.connect(self._sync_history)
        h.addWidget(tools)
        h.addWidget(vsep())
        h.addLayout(colors)
        h.addWidget(vsep())
        h.addWidget(sizes)
        h.addStretch()
        h.addWidget(self.undo_btn)
        h.addWidget(self.redo_btn)
        h.addWidget(clear)

        foot = QtWidgets.QFrame()
        foot.setObjectName("ccSketchFoot")
        foot.setAttribute(QtCore.Qt.WA_StyledBackground)
        f = QtWidgets.QHBoxLayout(foot)
        f.setContentsMargins(16, 10, 12, 10)
        f.setSpacing(8)
        f.addWidget(I.icon_label("info", T["text3"], 14))
        hint = QtWidgets.QLabel("Marque onde quer a mudança e escreva as medidas. Depois descreva o pedido no chat.")
        hint.setObjectName("ccHint")
        hint.setWordWrap(True)
        f.addWidget(hint, 1)
        cancel = QtWidgets.QPushButton("Cancelar")
        cancel.setObjectName("ccSecondary")
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Anexar ao chat")
        ok.setObjectName("ccPrimary")
        ok.setCursor(QtCore.Qt.PointingHandCursor)
        ok.setDefault(True)
        ok.setIcon(I.icon("paperclip", T["inverse_text"], 14))
        ok.setIconSize(QtCore.QSize(14, 14))
        ok.setToolTip("Anexar ao chat (Ctrl+Enter)")
        ok.clicked.connect(self.accept)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, activated=self.accept)
        f.addWidget(cancel)
        f.addWidget(ok)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(foot)
        self._sync_history()
        # foco inicial no desenho (anéis de foco só aparecem navegando com Tab)
        self.canvas.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.canvas.setFocus(QtCore.Qt.OtherFocusReason)

        # janela estreita (notebook com escala 125-150 %): ferramentas só com ícone (a dica tem o nome).
        # A barra aceita encolher até a largura "só ícones"; o resizeEvent troca o estilo.
        self._bar, self._compact = bar, None
        self._set_compact(True)
        compact_w = bar.minimumSizeHint().width()
        self._set_compact(False)
        self._full_w = bar.sizeHint().width()
        bar.setMinimumWidth(compact_w)
        screen = (parent.screen() if parent is not None else None) or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1280, 800)
        self.resize(min(1000, int(avail.width() * 0.92)), min(760, int(avail.height() * 0.9)))
        self._set_compact(self.width() < self._full_w)

    def _set_compact(self, on):
        if on == self._compact:
            return
        self._compact = on
        style = QtCore.Qt.ToolButtonIconOnly if on else QtCore.Qt.ToolButtonTextBesideIcon
        for b in list(self.tool_buttons.values()) + [self.undo_btn, self.redo_btn, self.clear_btn]:
            b.setToolButtonStyle(style)
            TH.set_prop(b, "iconOnly", on)
        for b in self.size_buttons.values():           # espessuras: sempre só ícone
            TH.set_prop(b, "iconOnly", True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_compact", None) is not None:
            self._set_compact(self.width() < self._full_w)

    def _sync_history(self):
        self.undo_btn.setEnabled(bool(self.canvas.undo_stack))
        self.redo_btn.setEnabled(bool(self.canvas.redo_stack))

    def image(self):
        return self.canvas.result()


def blank_sheet(w=1400, h=1000):
    """Folha branca quadriculada (1 quadrado = referência visual, sem escala real)."""
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#ffffff"))
    painter = QtGui.QPainter(img)
    painter.setPen(QtGui.QPen(QtGui.QColor("#e3e7ee"), 1))
    for x in range(0, w, 40):
        painter.drawLine(x, 0, x, h)
    for y in range(0, h, 40):
        painter.drawLine(0, y, w, y)
    painter.end()
    return img
