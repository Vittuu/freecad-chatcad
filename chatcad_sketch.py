"""Tela para rabiscar por cima de uma imagem (vista do FreeCAD, print, foto ou folha em branco)."""

from PySide import QtCore, QtGui, QtWidgets

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
        scaled = img.scaled(self.size(), QtCore.Qt.KeepAspectRatio)
        x = (self.width() - scaled.width()) / 2
        y = (self.height() - scaled.height()) / 2
        return QtCore.QRectF(x, y, scaled.width(), scaled.height())

    def _to_img(self, pos):
        r = self._target()
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
    def _push_undo(self):
        self.undo_stack.append(self.ink.copy())
        del self.undo_stack[:-30]

    def undo(self):
        if self.undo_stack:
            self.ink = self.undo_stack.pop()
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
        if e.button() != QtCore.Qt.LeftButton:
            return
        p = self._to_img(e.position())
        if self.tool == "text":
            text, ok = QtWidgets.QInputDialog.getText(self, "Texto", "O que escrever (ex.: 60 mm):")
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
        self._push_undo()
        self.drawing = True
        self.start = self.last = p

    def mouseMoveEvent(self, e):
        if not self.drawing:
            return
        p = self._to_img(e.position())
        if self.tool == "pen":
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
        if self.tool in ("line", "arrow") and self.preview:
            painter = QtGui.QPainter(self.ink)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(self._pen())
            a, b = self.preview
            if self.tool == "arrow":
                self._draw_arrow(painter, a, b)
            else:
                painter.drawLine(a, b)
            painter.end()
        self.preview = None
        self.update()
        self.changed.emit()

    def paintEvent(self, _e):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#141518"))
        r = self._target()
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.drawImage(r, self.base)
        painter.drawImage(r, self.ink)
        if self.preview:
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


class SketchDialog(QtWidgets.QDialog):
    def __init__(self, image, title="Desenhar", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Chat CAD — {title}")
        self.setObjectName("chatcadSketch")
        self.resize(1000, 760)
        self.setStyleSheet("""
            #chatcadSketch { background: #1b1c1f; }
            #chatcadSketch QLabel { color: #a9adb6; }
            #chatcadSketch QToolButton {
                background: #22242a; color: #e6e6e6; border: 1px solid #2e3036;
                border-radius: 7px; padding: 5px 10px; }
            #chatcadSketch QToolButton:checked { background: #3a3d45; border-color: #6b7080; }
            #chatcadSketch QToolButton:hover { border-color: #4a4e57; }
            #chatcadSketch QPushButton { background: #33373e; color: #f0f0f0; border: 1px solid #41454d;
                border-radius: 7px; padding: 6px 14px; }
            #chatcadSketch QPushButton#primary { background: #e6e6e6; color: #111; border: none; font-weight: 600; }
        """)
        self.canvas = Canvas(image)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        tools = QtWidgets.QButtonGroup(self)
        for label, key, tip in (("✎ Caneta", "pen", "Traço livre"), ("╱ Linha", "line", "Linha reta"),
                                ("➜ Seta", "arrow", "Seta apontando"), ("T Texto", "text", "Escrever uma medida")):
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(key == "pen")
            b.clicked.connect(lambda _=False, k=key: setattr(self.canvas, "tool", k))
            tools.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(10)
        colors = QtWidgets.QButtonGroup(self)
        for name, hexa in COLORS:
            b = QtWidgets.QToolButton()
            b.setToolTip(name)
            b.setCheckable(True)
            b.setChecked(hexa == COLORS[0][1])
            b.setFixedSize(26, 26)
            b.setStyleSheet(f"QToolButton {{ background: {hexa}; border-radius: 13px; border: 2px solid #1b1c1f; }}"
                            f"QToolButton:checked {{ border: 2px solid #ffffff; }}")
            b.clicked.connect(lambda _=False, h=hexa: setattr(self.canvas, "color", QtGui.QColor(h)))
            colors.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(10)
        size = QtWidgets.QComboBox()
        for name, w in SIZES:
            size.addItem(name, w)
        size.setCurrentIndex(1)
        size.currentIndexChanged.connect(lambda i: setattr(self.canvas, "pen_width", size.itemData(i)))
        bar.addWidget(size)
        bar.addStretch()
        undo = QtWidgets.QToolButton()
        undo.setText("↶ Desfazer")
        undo.setShortcut(QtGui.QKeySequence.Undo)
        undo.clicked.connect(self.canvas.undo)
        clear = QtWidgets.QToolButton()
        clear.setText("Limpar")
        clear.clicked.connect(self.canvas.clear)
        bar.addWidget(undo)
        bar.addWidget(clear)

        hint = QtWidgets.QLabel("Rabisque onde quer a mudança e escreva medidas. Depois descreva o pedido no chat.")
        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(hint, 1)
        cancel = QtWidgets.QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Anexar ao chat")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(bottom)

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
