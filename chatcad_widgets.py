"""Peças da interface do Chat CAD (barra de título, aviso, tela inicial, área de digitação,
passos das ferramentas, resposta em markdown, miniaturas). O tema vem de chatcad_theme /
chatcad.qss; os ícones de chatcad_icons."""
import html
import json
import math
import os
import time

try:
    from PySide import QtCore, QtGui, QtWidgets
except ImportError:                                   # testes offscreen fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtWidgets

import chatcad_icons as I
import chatcad_theme as TH
from chatcad_theme import T, fmt_secs, set_prop

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


# ---------------------------------------------------------------------------
# peças básicas
# ---------------------------------------------------------------------------

class Clickable(QtWidgets.QFrame):
    """QFrame clicável e focável por Tab (Enter/Espaço)."""
    clicked = QtCore.Signal()

    def __init__(self, obj, parent=None):
        super().__init__(parent)
        self.setObjectName(obj)
        self.setAttribute(QtCore.Qt.WA_Hover)
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)

    def mousePressEvent(self, e):
        e.accept()                                       # sem isso o release não volta para cá

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class ClickLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def plain_tip(text):
    """Dica sempre como texto puro: o Qt lê como HTML o que parece marcação (ex.: "<b>" num comentário
    do modelo ou no Label de um objeto do documento)."""
    text = text or ""
    if "<" not in text:
        return text
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


class ElidedLabel(QtWidgets.QLabel):
    """Uma linha, cortada com "…" na largura disponível; o texto inteiro vai na dica.
    Texto puro sempre: títulos e dicas vêm do modelo e de Labels do documento."""

    def __init__(self, text="", parent=None, tip=None):
        super().__init__(parent)
        self.setTextFormat(QtCore.Qt.PlainText)
        self._full = ""
        self._tip = tip
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._elide()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()

    def minimumSizeHint(self):
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def _chrome(self):
        """Largura de padding/borda do QSS (o sizeHint do QLabel já conta com ela)."""
        return max(0, super().sizeHint().width() - self.fontMetrics().horizontalAdvance(QtWidgets.QLabel.text(self)))

    def sizeHint(self):
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self._full) + self._chrome() + 1,
                            super().sizeHint().height())

    def _elide(self):
        w = (self.width() - self._chrome()) if self.width() > 0 else 10 ** 6
        QtWidgets.QLabel.setText(self, self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, max(0, w)))
        self.setToolTip(plain_tip(self._tip if self._tip is not None else self._full))


def tool_btn(obj, icon_name, tip, size=16, color=None):
    b = QtWidgets.QToolButton()
    b.setObjectName(obj)
    b.setAutoRaise(True)                                  # autoRaise => cor de hover
    b.setIcon(I.icon(icon_name, color or T["text3"], size, hover=T["text"]))
    b.setIconSize(QtCore.QSize(size, size))
    b.setToolTip(tip)
    b.setAccessibleName(tip.split(" (")[0])
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setFocusPolicy(QtCore.Qt.TabFocus)
    return b


def menu_section(menu, text):
    lab = QtWidgets.QLabel(text)
    lab.setObjectName("ccMenuSection")
    act = QtWidgets.QWidgetAction(menu)
    act.setDefaultWidget(lab)
    act.setEnabled(False)
    menu.addAction(act)
    return act


def _dpr():
    app = QtWidgets.QApplication.instance()
    return app.devicePixelRatio() if app is not None else 1.0


def rounded(pm, w, h=None, radius=8):
    """Pixmap com cantos redondos, DPR correto; h => recorte 'cover' centralizado."""
    dpr = _dpr()
    if pm is None or pm.isNull():
        pm = QtGui.QPixmap(max(1, w), max(1, h or w))
        pm.fill(QtGui.QColor(T.get("surface", "#1f1f1f")))
    W, H = int(w * dpr), int((h or 0) * dpr)
    if h:
        src = pm.scaled(W, H, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
        src = src.copy((src.width() - W) // 2, (src.height() - H) // 2, W, H)
    else:
        src = pm.scaledToWidth(W, QtCore.Qt.SmoothTransformation)
    out = QtGui.QPixmap(src.size())
    out.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(out.rect()), radius * dpr, radius * dpr)
    p.setClipPath(path)
    p.drawPixmap(0, 0, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class ChevronMixin:
    """Desenha um chevron à direita (o ::menu-indicator fica escondido no QSS)."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtGui.QPainter(self)
        p.drawPixmap(self.width() - self._chev_x, (self.height() - 12) // 2, I.pixmap("chevron-down", T["text3"], 12))
        p.end()


class MenuButton(ChevronMixin, QtWidgets.QPushButton):      # ccQuick (text-align só funciona em QPushButton)
    _chev_x = 22


class ElidingToolButton(QtWidgets.QToolButton):
    """QToolButton cujo texto encolhe com "…" quando falta espaço, sem forçar a largura do dock.
    O texto inteiro fica em full_text (e na dica, a cargo de quem chama)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        # QToolButton nasce Fixed: com Preferred o layout pode encolher até minimumSizeHint (texto com "…")
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def set_full_text(self, text):
        self.full_text = text or ""
        self.setText(self.full_text)
        self.updateGeometry()
        self._elide()

    def _text_w(self, text):
        return self.fontMetrics().horizontalAdvance(text)

    def sizeHint(self):
        s = super().sizeHint()
        if self.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly:
            return s
        return QtCore.QSize(s.width() + self._text_w(self.full_text) - self._text_w(self._shown()), s.height())

    def minimumSizeHint(self):
        s = self.sizeHint()
        if self.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly:
            return s
        return QtCore.QSize(s.width() - self._text_w(self.full_text) + self._text_w(self.full_text[:3] + "…"),
                            s.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self.updateGeometry()
            self._elide()

    def _elide(self):
        if self.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly or not self.full_text:
            return
        extra = self.sizeHint().width() - self._text_w(self.full_text)      # ícone + margens (+ chevron)
        avail = max(0, self.width() - extra)
        fits = self.width() >= self.sizeHint().width()
        text = self.full_text if fits else self.fontMetrics().elidedText(self.full_text, QtCore.Qt.ElideRight, avail)
        if text != self._shown():
            QtWidgets.QToolButton.setText(self, text)

    def _shown(self):                                      # texto na tela (pode ter "…")
        return QtWidgets.QToolButton.text(self)


class PillButton(ChevronMixin, ElidingToolButton):          # ccPill
    _chev_x = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccPill")
        self.setAutoRaise(True)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QtCore.QSize(14, 14))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self.setMenu(TH.popup_menu(self))

    def set_value(self, icon_name, text, icon_color=None, max_chars=26, tip=None):
        self.setIcon(I.icon(icon_name, icon_color or T["text2"], 14, hover=icon_color or T["text"]))
        self.setToolTip(tip or text)
        self.setAccessibleName(text)
        self.set_full_text(text if len(text) <= max_chars else text[:max_chars - 1] + "…")


# ---------------------------------------------------------------------------
# barra de título do dock (sem título duplicado)
# ---------------------------------------------------------------------------

class DockTitleBar(QtWidgets.QFrame):
    """titleBarWidget do QDockWidget. QFrame/QLabel ignoram mousePress => arrastar e
    duplo clique (flutuar/encaixar) continuam com o QDockWidget."""
    newChat = QtCore.Signal()

    def __init__(self, dock=None):
        super().__init__(dock)
        self.dock = None
        self.setObjectName("ccTitle")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 6, 7)
        lay.setSpacing(2)
        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        r1 = QtWidgets.QHBoxLayout()
        r1.setSpacing(6)
        badge = QtWidgets.QLabel("CHAT")
        badge.setObjectName("ccBadge")
        name = QtWidgets.QLabel("CAD")
        name.setObjectName("ccName")
        r1.addWidget(badge)
        r1.addWidget(name)
        r1.addStretch()
        r2 = QtWidgets.QHBoxLayout()
        r2.setSpacing(6)
        self.dot = QtWidgets.QLabel()
        self.dot.setObjectName("ccDot")
        self.state = ElidedLabel("verificando…")
        self.state.setObjectName("ccState")
        r2.addWidget(self.dot, 0, QtCore.Qt.AlignVCenter)
        r2.addWidget(self.state, 1)
        col.addLayout(r1)
        col.addLayout(r2)
        lay.addLayout(col, 1)
        self.gear_btn = tool_btn("ccIconBtn", "settings", "Configurar a IA local")
        self.gear_btn.hide()
        self.new_btn = tool_btn("ccIconBtn", "new-chat", "Nova conversa")       # sem Ctrl+N (é o "Novo" do FreeCAD)
        self.new_btn.clicked.connect(self.newChat)
        self.more_btn = tool_btn("ccIconBtn", "more", "Mais opções")
        self.more_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.sep = QtWidgets.QFrame()
        self.sep.setObjectName("ccTitleSep")
        self.float_btn = tool_btn("ccWinBtn", "undock", "Desencaixar / encaixar", 12)
        self.close_btn = tool_btn("ccWinBtn", "close", "Fechar painel (Ctrl+Shift+Espaço reabre)", 12)
        self.float_btn.clicked.connect(self._toggle_float)
        self.close_btn.clicked.connect(self._close)
        for w in (self.gear_btn, self.new_btn, self.more_btn):
            lay.addWidget(w, 0, QtCore.Qt.AlignVCenter)
        lay.addSpacing(4)
        lay.addWidget(self.sep, 0, QtCore.Qt.AlignVCenter)
        lay.addSpacing(4)
        for w in (self.float_btn, self.close_btn):
            lay.addWidget(w, 0, QtCore.Qt.AlignVCenter)
        if dock is not None:
            self.bind(dock)
        else:
            self.set_native(True)

    def bind(self, dock):
        self.dock = dock
        self.set_native(False)

    def _toggle_float(self):
        if self.dock is not None:
            self.dock.setFloating(not self.dock.isFloating())

    def _close(self):
        if self.dock is not None:
            self.dock.close()

    def set_native(self, native):            # fallback NativeTitleBar: barra do Qt fica, escondemos as nossas
        for w in (self.float_btn, self.close_btn, self.sep):
            w.setVisible(not native)

    def set_status(self, state, text, tip=None):   # ok | busy | off | idle — sempre com texto, nunca só cor
        set_prop(self.dot, "state", state)
        self.state.setText(text)
        self.dot.setToolTip(tip or text)


# ---------------------------------------------------------------------------
# aviso (banner)
# ---------------------------------------------------------------------------

class Banner(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccBanner")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.kind = ""
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 8, 10)
        lay.setSpacing(10)
        self.icon = QtWidgets.QLabel()
        self.icon.setFixedSize(16, 18)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName("ccBannerTitle")
        self.title.setWordWrap(True)
        self.text = QtWidgets.QLabel()
        self.text.setObjectName("ccBannerText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(QtCore.Qt.RichText)
        self.text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(6)
        btns.setContentsMargins(0, 6, 0, 0)
        self.action = QtWidgets.QPushButton()
        self.action.setObjectName("ccBtn")
        self.action.setCursor(QtCore.Qt.PointingHandCursor)
        self.recheck = QtWidgets.QPushButton("Verificar de novo")
        self.recheck.setObjectName("ccBtn")
        self.recheck.setCursor(QtCore.Qt.PointingHandCursor)
        self.recheck.setIcon(I.icon("refresh", T["text2"], 14))
        self.recheck.setIconSize(QtCore.QSize(14, 14))
        btns.addWidget(self.action)
        btns.addWidget(self.recheck)
        btns.addStretch()
        col.addWidget(self.title)
        col.addWidget(self.text)
        col.addLayout(btns)
        self.close_btn = tool_btn("ccMiniBtn", "close", "Dispensar", 12)
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.icon, 0, QtCore.Qt.AlignTop)
        lay.addLayout(col, 1)
        lay.addWidget(self.close_btn, 0, QtCore.Qt.AlignTop)
        self.hide()

    def show_message(self, kind, title, html, action=None, recheck=True):   # kind: warn | error | info
        name, col = {"warn": ("alert", T["warn"]), "error": ("x-circle", T["danger"]),
                     "info": ("info", T["info"])}[kind]
        self.kind = kind
        set_prop(self, "kind", kind)
        for w in (self.title, self.text):      # as regras dos filhos dependem do [kind] do pai: repolir
            w.style().unpolish(w)
            w.style().polish(w)
        self.icon.setPixmap(I.pixmap(name, col, 16))
        self.title.setText(title)
        self.text.setText(html)
        self.text.setVisible(bool(html))
        self.action.setVisible(bool(action))
        self.action.setText(action or "")
        self.recheck.setVisible(recheck)
        self.show()


# ---------------------------------------------------------------------------
# tela inicial
# ---------------------------------------------------------------------------

WORKFLOWS = [
    ("wf-bracket", "Suporte de parede", "Com furos de parafuso e escareado",
     "Quero um suporte de parede para "),
    ("wf-box", "Caixa com tampa", "Caixas para eletrônica, com encaixe",
     "Quero uma caixa com tampa para "),
    ("wf-honeycomb", "Ventilação e colmeia", "Padrões vazados com borda sólida",
     "Faça um padrão de colmeia vazado na face selecionada, mantendo uma borda sólida de 4 mm."),
    ("wf-fillet", "Ajustar a peça atual", "Arredondar, furar, engrossar, mudar medidas",
     "Na peça atual, quero "),
    ("wf-print", "Preparar para impressão", "Checar paredes finas e exportar STL / 3MF",
     "Verifique se a peça está boa para impressão 3D (paredes finas, sólido único, balanços) "
     "e exporte um STL ao lado do arquivo."),
]


class WorkflowCard(Clickable):
    def __init__(self, icon_name, title, sub, last=False, parent=None):
        super().__init__("ccCard", parent)
        self.setProperty("last", last)
        self.setAccessibleName(title)
        self.setAccessibleDescription(sub)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(8, 10, 8, 10)
        h.setSpacing(12)
        self.tile = QtWidgets.QLabel()
        self.tile.setObjectName("ccTile")
        self.tile.setAlignment(QtCore.Qt.AlignCenter)
        self.tile.setPixmap(I.pixmap(icon_name, T["text2"], 16))
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        t = QtWidgets.QLabel(title)
        t.setObjectName("ccCardTitle")
        s = ElidedLabel(sub)
        s.setObjectName("ccCardSub")
        col.addWidget(t)
        col.addWidget(s)
        self.chev = I.icon_label("chevron-right", T["text3"], 14)
        sp = self.chev.sizePolicy()
        sp.setRetainSizeWhenHidden(True)                  # o texto não pula quando o chevron aparece
        self.chev.setSizePolicy(sp)
        self.chev.setVisible(False)
        h.addWidget(self.tile, 0, QtCore.Qt.AlignTop)
        h.addLayout(col, 1)
        h.addWidget(self.chev)

    def enterEvent(self, e):
        self.chev.show()
        set_prop(self.tile, "hot", True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.chev.hide()
        set_prop(self.tile, "hot", False)
        super().leaveEvent(e)


# ---------------------------------------------------------------------------
# área de digitação
# ---------------------------------------------------------------------------

QUICK_ACTIONS = [   # (seção, ícone, rótulo, prompt, precisa_seleção)
    ("Na seleção", "wf-fillet", "Arredondar a seleção",
     "Arredonde as arestas selecionadas com raio de 3 mm, como parâmetro.", True),
    ("Na seleção", "wf-hole", "Furo na face selecionada",
     "Faça um furo passante de 5 mm no ponto que eu cliquei na face selecionada.", True),
    ("Na seleção", "wf-honeycomb", "Colmeia na face selecionada",
     "Faça um padrão de colmeia vazado na face selecionada, mantendo uma borda sólida de 4 mm.", True),
    ("Na peça", "wf-explain", "Explicar a peça",
     "Explique em poucas linhas como esta peça foi construída e quais parâmetros existem.", False),
    ("Na peça", "wf-check", "Checar para impressão 3D",
     "Verifique se a peça está boa para impressão 3D e me diga o que melhorar.", False),
    ("Na peça", "download", "Exportar STL", "Exporte a peça final como STL na mesma pasta do arquivo.", False),
]

PLACEHOLDER = {"agent": "Descreva o que você quer criar…",
               "plan": "Pergunte ou peça um plano. A peça não será alterada."}


class ContextChip(ElidingToolButton):
    """Seleção do FreeCAD que vai junto com a mensagem; encolhe com "…" no painel estreito."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccChip")
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QtCore.QSize(14, 14))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self._key = None

    def set_selection(self, text, on):
        if (text, on) == self._key:
            return                                         # timer de 400 ms: não repolir à toa
        self._key = (text, on)
        self.setIcon(I.icon("crosshair" if on else "eye-off", T["text2"] if on else T["text3"], 14, hover=T["text"]))
        full = text if on else f"Ignorando: {text}"
        tip = ("A seleção vai junto com a mensagem. Clique para ignorar." if on
               else "Clique para mandar a seleção de novo.")
        self.setToolTip(plain_tip(f"{full}\n{tip}"))      # o Label do objeto vem do documento
        self.setAccessibleName(full)
        set_prop(self, "off", not on)
        self.set_full_text(full)

    def text(self):                                        # texto inteiro (o da tela pode ter "…")
        return self.full_text


class AttachThumb(QtWidgets.QFrame):
    """Miniatura de um anexo, com × para remover e clique para desenhar por cima."""
    removed = QtCore.Signal(str)
    opened = QtCore.Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setObjectName("ccAttach")
        self.setAttribute(QtCore.Qt.WA_Hover)
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setFixedSize(52, 52)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Clique para desenhar por cima")
        pic = QtWidgets.QLabel(self)
        pic.setGeometry(2, 2, 48, 48)
        pic.setPixmap(rounded(QtGui.QPixmap(path), 48, 48, 6))
        pic.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.x_btn = x = QtWidgets.QToolButton(self)
        x.setObjectName("ccAttachX")
        x.setAutoRaise(True)
        x.setGeometry(33, 3, 16, 16)
        x.setIcon(I.icon("close", "#ffffff", 10))
        x.setIconSize(QtCore.QSize(10, 10))
        x.setToolTip("Remover")
        x.setAccessibleName("Remover anexo")
        x.setCursor(QtCore.Qt.PointingHandCursor)
        x.clicked.connect(lambda: self.removed.emit(self.path))

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.opened.emit(self.path)


class SendButton(QtWidgets.QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccSend")
        self.setIconSize(QtCore.QSize(16, 16))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self.busy = None
        self.set_busy(False)

    def set_busy(self, busy):
        if busy == self.busy:
            return
        self.busy = busy
        self.setIcon(I.icon("stop", T["text"], 14) if busy else
                     I.icon("arrow-up", T["inverse_text"], 16, disabled=T["text4"], stroke=2.25))
        self.setIconSize(QtCore.QSize(14, 14) if busy else QtCore.QSize(16, 16))
        self.setToolTip("Parar (Esc)" if busy else "Enviar (Enter)")
        self.setAccessibleName("Parar" if busy else "Enviar")
        set_prop(self, "busy", busy)


class Prompt(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()
    stopRequested = QtCore.Signal()
    imagePasted = QtCore.Signal(object)      # QImage (Ctrl+V de um print)
    filesDropped = QtCore.Signal(list)       # caminhos de imagens arrastadas/coladas
    focusChanged = QtCore.Signal(bool)
    dragActive = QtCore.Signal(bool)
    MIN_LINES, MAX_LINES = 2, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccPrompt")
        self.setTabChangesFocus(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.history, self._hist_i = [], None
        # cor do texto de exemplo: placeholder-text-color no QSS (#ccPrompt)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_):
        lines = max(self.MIN_LINES, min(self.MAX_LINES, int(self.document().size().height())))
        m = self.contentsMargins()
        # +2: o QPlainTextEdit só esconde a rolagem se a última linha couber com folga (sem isso,
        # 2+ linhas já mostravam a barra com máximo 1)
        self.setFixedHeight(math.ceil(lines * QtGui.QFontMetricsF(self.font()).lineSpacing())
                            + int(2 * self.document().documentMargin()) + m.top() + m.bottom() + 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():         # a largura muda a quebra das linhas
            QtCore.QTimer.singleShot(0, self, self._fit)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._fit()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focusChanged.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focusChanged.emit(False)

    def event(self, e):        # Esc é nosso só dentro do campo (não vira atalho global)
        if e.type() == QtCore.QEvent.ShortcutOverride and e.key() == QtCore.Qt.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    # colar / arrastar imagens -----------------------------------------------------
    def canInsertFromMimeData(self, src):
        return src.hasImage() or src.hasUrls() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src):
        if src.hasUrls():
            paths = [u.toLocalFile() for u in src.urls()
                     if u.toLocalFile().lower().endswith(IMG_EXT)]
            if paths:
                self.filesDropped.emit(paths)
                return
        # Excel/PowerPoint/OneNote põem texto E um bitmap da seleção: com texto, cola o texto.
        # Print (Win+Shift+S) e "Copiar imagem" não trazem texto e continuam virando anexo.
        if src.hasImage() and not (src.hasText() and src.text().strip()):
            img = QtGui.QImage(src.imageData())
            if not img.isNull():
                self.imagePasted.emit(img)
                return
        super().insertFromMimeData(src)

    def dragEnterEvent(self, e):
        super().dragEnterEvent(e)
        md = e.mimeData()
        self.dragActive.emit(bool(md.hasImage() or md.hasUrls()))

    def dragLeaveEvent(self, e):
        super().dragLeaveEvent(e)
        self.dragActive.emit(False)

    def dropEvent(self, e):
        self.dragActive.emit(False)
        super().dropEvent(e)

    # teclado ------------------------------------------------------------------------
    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (mods & QtCore.Qt.ShiftModifier):
            if self.toPlainText().strip():
                self.history.append(self.toPlainText().strip())
            self._hist_i = None
            self.submitted.emit()
            return
        if k == QtCore.Qt.Key_Escape:
            self.stopRequested.emit()
            return
        if k == QtCore.Qt.Key_Up and self.history and self.textCursor().blockNumber() == 0 \
                and (not self.toPlainText() or self._hist_i is not None):
            self._recall(-1)
            return
        if k == QtCore.Qt.Key_Down and self._hist_i is not None:
            self._recall(+1)
            return
        super().keyPressEvent(e)

    def _recall(self, step):
        i = len(self.history) if self._hist_i is None else self._hist_i
        i = max(0, min(len(self.history), i + step))
        self._hist_i = i if i < len(self.history) else None
        self.setPlainText(self.history[i] if i < len(self.history) else "")
        self.moveCursor(QtGui.QTextCursor.End)


# ---------------------------------------------------------------------------
# conversa
# ---------------------------------------------------------------------------

_ZWSP = chr(0x200B)
_SEPS = "/_.-" + chr(92)                           # chr(92) = barra invertida (caminhos do Windows)


def breakable(text):
    """Só para exibir: pontos de quebra invisíveis (U+200B) depois de barras, _ . - e a cada 20
    letras de uma palavra enorme, para o QLabel com quebra de linha não cortar caminhos de
    arquivo na borda do balão."""
    out, run = [], 0
    for i, ch in enumerate(text):
        out.append(ch)
        if ch.isspace():
            run = 0
            continue
        run += 1
        if i + 1 < len(text) and not text[i + 1].isspace() and (ch in _SEPS or run >= 20):
            out.append(_ZWSP)
            run = 0
    return "".join(out)


class UserBubble(QtWidgets.QFrame):
    def __init__(self, text, tags=(), images=(), max_w=300, parent=None):
        super().__init__(parent)
        self.setObjectName("ccUserBubble")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)
        self._text, self._tags, self._max_w = text, [], max_w
        if tags:
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(4)
            for tg in tags:
                lab = ElidedLabel(tg)
                lab.setObjectName("ccCtxTag")
                lab.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
                r.addWidget(lab, 0)
                self._tags.append(lab)
            r.addStretch(1)
            v.addLayout(r)
        if images:
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(4)
            for pm in images:
                lab = QtWidgets.QLabel()
                lab.setObjectName("ccBubbleImg")
                lab.setPixmap(rounded(pm, 40, 40, 6))
                r.addWidget(lab)
            r.addStretch()
            v.addLayout(r)
        self.label = lab = QtWidgets.QLabel(breakable(text))
        lab.setObjectName("ccUserText")
        lab.setTextFormat(QtCore.Qt.PlainText)            # "<b>" digitado aparece como texto
        lab.setWordWrap(True)
        lab.setFont(TH.ui_font(13))
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(lab)
        self._floor = 160 if (tags or images) else 48
        self.fit(max_w)

    def _natural(self):
        """Largura que o texto e a etiqueta da seleção pedem (medidas com a fonte do QSS, se já polido)."""
        fm = self.label.fontMetrics()
        text_w = max([fm.horizontalAdvance(ln) for ln in self._text.splitlines()] or [0])
        tags_w = sum(t.sizeHint().width() for t in self._tags) + 4 * max(0, len(self._tags) - 1)
        return max(text_w, tags_w) + 28                  # margens 12+12, borda e folga

    def fit(self, max_w=None):
        """Largura justa ao conteúdo, até max_w (QLabel com wordWrap erra a largura sozinho).
        A altura também fica fixa: a coluna da conversa perguntaria a altura para a largura dela,
        não para a do balão, e o texto quebrado em mais linhas sairia cortado embaixo."""
        if max_w is not None:
            self._max_w = max_w
        w = int(min(self._max_w, max(self._natural(), self._floor)))
        self.setFixedWidth(w)
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            self.setFixedHeight(lay.totalHeightForWidth(w))

    def showEvent(self, e):
        self.fit()                                        # já com a folha de estilo aplicada
        super().showEvent(e)


class TurnHeader(QtWidgets.QWidget):
    def __init__(self, meta, plan=False, parent=None):
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        av = QtWidgets.QLabel()
        av.setObjectName("ccAvatar")
        av.setAlignment(QtCore.Qt.AlignCenter)
        av.setPixmap(I.pixmap("logo", T["brand"], 12))
        who = QtWidgets.QLabel("Chat CAD")
        who.setObjectName("ccWho")
        self.meta = m = ElidedLabel(meta)
        m.setObjectName("ccWhoMeta")
        h.addWidget(av)
        h.addWidget(who)
        h.addWidget(m, 1)
        if plan:
            tag = QtWidgets.QLabel("Planejar")
            tag.setObjectName("ccModeTag")
            h.addWidget(tag)


TOOL_META = {
    # MCP do FreeCAD (Claude Code, Codex, Antigravity)
    "execute_code": ("code", "Executando no FreeCAD"),
    "execute_code_async": ("code", "Executando em segundo plano"),
    "get_view": ("eye", "Olhando a peça"),
    "get_objects": ("layers", "Lendo os objetos"),
    "get_object": ("layers", "Lendo um objeto"),
    "list_documents": ("file", "Listando documentos"),
    "create_document": ("file", "Criando documento"),
    "create_object": ("box-plus", "Criando objeto"),
    "edit_object": ("pencil", "Editando objeto"),
    "delete_object": ("trash", "Removendo objeto"),
    "run_fem_analysis": ("activity", "Rodando análise de esforço"),
    "insert_part_from_library": ("box-plus", "Inserindo peça da biblioteca"),
    # IA local
    "run_python": ("code", "Executando no FreeCAD"),
    "get_document": ("layers", "Lendo o documento"),
    "set_parameter": ("table", "Ajustando medida"),
    "view_screenshot": ("eye", "Olhando a peça"),
    "thinking": ("bulb", "Pensando"),
    "reading": ("cpu", "Preparando o próximo passo"),
    "describe_image": ("image", "Lendo a imagem"),
}

TOOL_TIPS = {
    "reading": "O modelo local está lendo o pedido. No processador, a primeira resposta pode levar alguns segundos.",
    "thinking": "O que o modelo pensou antes de agir",
    "describe_image": "Um modelo de visão descreve a imagem anexada em texto",
}


def tool_label(name):
    short = name.split("__")[-1]
    return TOOL_META.get(short, ("wrench", short.replace("_", " ")))[1]


def describe_tool(name, inp):
    """-> (ícone, título, tooltip, dica, texto de entrada)"""
    short, inp = name.split("__")[-1], inp if isinstance(inp, dict) else {}
    ic, label = TOOL_META.get(short, ("wrench", short.replace("_", " ")))
    title, hint, detail = label, "", ""
    tip = TOOL_TIPS.get(short, label)
    if short.startswith("execute_code") or short == "run_python":
        code = str(inp.get("code", ""))
        first = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
        resumo = str(inp.get("resumo") or "").strip()
        if first.startswith("#"):
            title = first.lstrip("#").strip().rstrip(".")[:70] or label
        elif resumo and resumo != "código escrito na resposta":
            title = (resumo[:1].upper() + resumo[1:]).rstrip(".")[:70]
        detail = code
    elif short == "set_parameter":
        nome, valor = str(inp.get("nome") or ""), str(inp.get("valor") or "")
        if nome:
            hint = f"{nome} = {valor}"[:40]
        detail = json.dumps(inp, ensure_ascii=False, indent=1)[:4000] if inp else ""
    elif short in ("thinking", "reading"):
        detail = ""
    else:
        hint = str(inp.get("obj_name") or inp.get("view_name") or inp.get("doc_name")
                   or inp.get("objeto") or inp.get("vista") or inp.get("modelo") or "")[:40]
        detail = json.dumps(inp, ensure_ascii=False, indent=1)[:4000] if inp else ""
    return ic, title, tip, hint, detail


class TextView(QtWidgets.QPlainTextEdit):
    """Caixa de código/texto só de leitura, da altura do conteúdo até max_lines (depois rola).
    A altura é refeita quando a largura muda: o texto quebra em mais ou menos linhas."""

    def __init__(self, text, max_lines, obj="ccCode", error=False, parent=None):
        super().__init__(parent)
        self.max_lines = max_lines
        self.setObjectName(obj)
        self.setReadOnly(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame if obj == "ccThinking" else QtWidgets.QFrame.StyledPanel)
        self.setFont(TH.mono_font(12) if obj == "ccCode" else TH.ui_font(12))
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setProperty("error", error)
        self.document().documentLayout().documentSizeChanged.connect(self.fit)
        self.setPlainText(text)
        self.fit()

    def line_count(self):
        """Linhas na tela, contadas com a largura atual (o layout do QPlainTextEdit é preguiçoso)."""
        doc = self.document()
        width = self.viewport().width() - 2 * doc.documentMargin()
        if width <= 0:
            return max(1, doc.blockCount())
        opt = doc.defaultTextOption()
        n, b = 0, doc.begin()
        while b.isValid():
            lay = QtGui.QTextLayout(b.text(), self.font())
            lay.setTextOption(opt)
            lay.beginLayout()
            while True:
                line = lay.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(width)
                n += 1
            lay.endLayout()
            b = b.next()
        return max(1, n)

    def fit(self, *_):
        n = self.line_count()
        shown = min(self.max_lines, n)
        m = self.contentsMargins()
        # +2: o QPlainTextEdit só esconde a rolagem se a última linha couber com folga
        self.setFixedHeight(math.ceil(shown * QtGui.QFontMetricsF(self.font()).lineSpacing())
                            + int(2 * self.document().documentMargin()) + m.top() + m.bottom() + 2)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded if n > self.max_lines
                                        else QtCore.Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            QtCore.QTimer.singleShot(0, self, self.fit)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            QtCore.QTimer.singleShot(0, self, self.fit)


def text_view(text, max_lines, obj="ccCode", error=False):
    return TextView(text, max_lines, obj, error)


def caption(text):
    c = QtWidgets.QLabel(text)
    c.setObjectName("ccCaption")
    return c


class StepRow(QtWidgets.QWidget):
    def __init__(self, icon_name, title, tooltip, hint="", detail="", kind="tool", parent=None):
        super().__init__(parent)
        self.state, self.kind, self.t0 = "run", kind, time.time()
        self._in, self._out, self._body = detail or "", "", None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = Clickable("ccStep")
        self.head.clicked.connect(self.toggle)
        self.head.setAccessibleName(title)
        h = QtWidgets.QHBoxLayout(self.head)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        self.status = QtWidgets.QLabel()
        self.status.setFixedSize(14, 14)
        self.status.setPixmap(I.rotated("loader", T["brand"], 14, 0))
        self.title = ElidedLabel(title, tip=tooltip)
        self.title.setObjectName("ccStepText")
        self.hint = ElidedLabel(hint)
        self.hint.setObjectName("ccStepHint")
        self.hint.setVisible(bool(hint))
        self.hint.setMaximumWidth(140)
        self.time = QtWidgets.QLabel()
        self.time.setObjectName("ccStepTime")
        self.chev = I.icon_label("chevron-right", T["text4"], 12)
        h.addWidget(self.status)
        h.addWidget(I.icon_label(icon_name, T["text3"], 14))
        h.addWidget(self.title, 3)
        h.addWidget(self.hint, 1)
        h.addWidget(self.time)
        h.addWidget(self.chev)
        v.addWidget(self.head)

    def spin(self, angle):
        if self.state == "run":
            self.status.setPixmap(I.rotated("loader", T["brand"], 14, angle))

    def append_detail(self, chunk):          # raciocínio em streaming
        self._in += chunk
        if self.expanded():
            self._rebuild()

    def set_output(self, text):
        self._out = text or ""

    def expanded(self):
        return self._body is not None and not self._body.isHidden()

    def finish(self, error=False, output="", title=None, interrupted=False):
        self.state = "stop" if interrupted else ("err" if error else "ok")
        if output:
            self._out = output
        name, col = (("stop-circle", T["text3"]) if interrupted else
                     ("x-circle", T["danger"]) if error else ("check", T["success"]))
        self.status.setPixmap(I.pixmap(name, col, 14))
        self.time.setText(fmt_secs(time.time() - self.t0))
        if title:
            self.title.setText(title)
            self.head.setAccessibleName(title)
        if error:
            set_prop(self.title, "state", "error")
        if self._body is not None:
            self._rebuild()

    def toggle(self):
        if self._body is None:
            self._rebuild()
            self._body.show()
        else:
            self._body.setVisible(self._body.isHidden())
        self.chev.setPixmap(I.pixmap("chevron-down" if self.expanded() else "chevron-right", T["text4"], 12))

    def _rebuild(self):
        vis = self._body is None or not self._body.isHidden()
        if self._body is not None:
            self._body.hide()
            self._body.deleteLater()
        b = QtWidgets.QFrame()
        b.setObjectName("ccStepBody")
        lay = QtWidgets.QVBoxLayout(b)
        lay.setContentsMargins(26, 2, 6, 8)
        lay.setSpacing(4)
        if self.kind == "thinking":
            lay.addWidget(text_view(self._in or "…", 12, "ccThinking"))
        else:
            if self._in:
                lay.addWidget(caption("ENTRADA"))
                lay.addWidget(text_view(self._in, 10))
            if self._out or self.state == "err":
                lay.addWidget(caption("ERRO" if self.state == "err" else "SAÍDA"))
                lay.addWidget(text_view(self._out or "(sem detalhes)", 6, error=self.state == "err"))
            if not (self._in or self._out or self.state == "err"):
                lay.addWidget(caption("SEM DETALHES"))
        self._body = b
        self.layout().addWidget(b)
        b.setVisible(vis)


class StepsGroup(QtWidgets.QFrame):
    """Passos consecutivos de um turno. Aberto enquanto roda; recolhe sozinho ao fechar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccSteps")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.rows, self.errors, self.t0 = {}, 0, time.time()
        self.done, self._angle, self._current, self._total = False, 0, "Trabalhando", ""
        self.interrupted = False
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        self.head = Clickable("ccStepsHead")
        self.head.clicked.connect(self.toggle)
        h = QtWidgets.QHBoxLayout(self.head)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)
        self.st = QtWidgets.QLabel()
        self.st.setFixedSize(14, 14)
        self.title = ElidedLabel()
        self.title.setObjectName("ccStepsTitle")
        self.meta = QtWidgets.QLabel()
        self.meta.setObjectName("ccStepsMeta")
        self.chev = I.icon_label("chevron-down", T["text3"], 14)
        h.addWidget(self.st)
        h.addWidget(self.title, 1)
        h.addWidget(self.meta)
        h.addWidget(self.chev)
        self.list = QtWidgets.QFrame()
        self.list.setObjectName("ccStepList")
        self.list.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.list_lay = QtWidgets.QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(2, 2, 2, 2)
        self.list_lay.setSpacing(0)
        v.addWidget(self.head)
        v.addWidget(self.list)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)
        self._tick()

    def add(self, key, icon_name, title, tooltip, hint="", detail="", kind="tool"):
        row = StepRow(icon_name, title, tooltip, hint, detail, kind)
        self.rows[key] = row
        self.list_lay.addWidget(row)
        self._current = title
        self._refresh()
        return row

    def finish_step(self, key, error=False, output="", title=None):
        row = self.rows.get(key)
        if row is not None and row.state == "run":
            row.finish(error, output, title)
            self.errors += bool(error)
            running = [r for r in self.rows.values() if r.state == "run"]
            if running:
                self._current = running[-1].title.text()
            self._refresh()

    def expanded(self):
        return self.list.isVisible() if self.isVisible() else not self.list.isHidden()

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.st.setPixmap(I.rotated("loader", T["brand"], 14, self._angle))
        for r in self.rows.values():
            r.spin(self._angle)
        if self._angle == 0:                          # texto do tempo ~1x por segundo
            self._refresh()

    def _refresh(self):
        n, el = len(self.rows), fmt_secs(time.time() - self.t0)
        if not self.done:
            self.title.setText(self._current.rstrip("…") + "…")
            self.meta.setText(f"passo {max(n, 1)} · {el}")
            self.head.setAccessibleName(self.title.text())
            return
        ok = not self.errors
        steps = f"{n} {'passo' if n == 1 else 'passos'}"
        if not ok:
            self.st.setPixmap(I.pixmap("alert", T["warn"], 14))
            self.title.setText(f"{steps} · {self.errors} com erro")
        elif self.interrupted:                        # parado pelo usuário: não parece sucesso
            self.st.setPixmap(I.pixmap("stop-circle", T["text3"], 14))
            self.title.setText(f"{steps} · interrompido")
        else:
            self.st.setPixmap(I.pixmap("check-circle", T["success"], 14))
            self.title.setText(steps)
        set_prop(self.title, "state", "" if ok else "error")
        self.meta.setText(self._total)
        self.head.setAccessibleName(self.title.text())

    def set_expanded(self, on):
        self.list.setVisible(on)
        self.chev.setPixmap(I.pixmap("chevron-down" if on else "chevron-right", T["text3"], 14))

    def toggle(self):
        self.set_expanded(self.list.isHidden())

    def close_group(self, interrupted=False):
        if self.done:
            return
        self.done = True
        self.interrupted = interrupted
        self._timer.stop()
        self._total = fmt_secs(time.time() - self.t0)
        for r in self.rows.values():
            if r.state == "run":
                r.finish(interrupted=True)
        if not self.errors:                           # com erro fica aberto para o usuário ver
            self.set_expanded(False)
        self._refresh()


# ---------------------------------------------------------------------------
# resposta em markdown
# ---------------------------------------------------------------------------

def open_link(url):
    """Abre um link da resposta no app padrão. Relativo => pasta do documento ativo (ou a pessoal)."""
    url = QtCore.QUrl(url)
    if url.isRelative() and not url.scheme():
        base = os.path.expanduser("~")
        try:
            import FreeCAD
            doc = FreeCAD.ActiveDocument
            if doc is not None and doc.FileName:
                base = os.path.dirname(doc.FileName)
        except Exception:
            pass
        path = url.path()
        if not path:                                   # só "#âncora": nada para abrir
            return
        url = QtCore.QUrl.fromLocalFile(os.path.join(base, path))
    QtGui.QDesktopServices.openUrl(url)


class AnswerView(QtWidgets.QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ccAnswer")
        # links abrem fora; sem isso um link relativo ("peca.stl") ou file: troca a resposta inteira
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(open_link)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setFixedHeight(20)
        self.source_md = ""
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.render)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def add_text(self, chunk):
        self.source_md += chunk
        if not self._timer.isActive():
            self._timer.start(60)                     # no máx. 1 re-render a cada 60 ms

    def render(self):                                 # chamado também no fim do turno (flush)
        self._timer.stop()
        doc = self.document()
        doc.setDefaultFont(TH.ui_font(13))
        doc.setMarkdown(self.source_md, QtGui.QTextDocument.MarkdownDialectGitHub)
        style_markdown(doc)
        self._fit()

    def _fit(self, *_):
        w = self.viewport().width()
        if w <= 0:
            return
        self.document().setTextWidth(w)
        h = int(self.document().size().height()) + 2
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def wheelEvent(self, e):
        e.ignore()                                    # a rolagem é da conversa


def style_markdown(doc):
    f = doc.defaultFont()
    # tamanhos em px, como o resto do painel (pt só bate com px a 96 dpi; no macOS o código encolheria)
    base = f.pixelSize() if f.pixelSize() > 0 else f.pointSizeF() * 4 / 3
    doc.setIndentWidth(16)
    doc.setDocumentMargin(0)
    mono = QtGui.QTextCharFormat()
    mono.setFontFamilies(TH.MONO_FAMILIES)
    mono.setProperty(QtGui.QTextFormat.FontPixelSize, max(1, round(base - 1)))
    scale = {1: 1.15, 2: 1.08}
    ranges, tables, first, prev_code = [], {}, True, False
    b = doc.begin()
    while b.isValid():
        bf, cur = b.blockFormat(), QtGui.QTextCursor(b)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock, QtGui.QTextCursor.KeepAnchor)
        is_code = bf.hasProperty(QtGui.QTextFormat.BlockCodeFence) or bf.nonBreakableLines()
        nxt = b.next()
        next_code = nxt.isValid() and (nxt.blockFormat().hasProperty(QtGui.QTextFormat.BlockCodeFence)
                                       or nxt.blockFormat().nonBreakableLines())
        nbf = QtGui.QTextBlockFormat(bf)
        if bf.headingLevel():
            # o markdown marca FontSizeAdjustment (xx-large); com ele presente o tamanho em px é
            # ignorado, então a propriedade sai de cada trecho do título
            px = round(base * scale.get(bf.headingLevel(), 1.0))
            frs, it = [], b.begin()
            while not it.atEnd():
                fr = it.fragment()
                if fr.isValid():
                    frs.append((fr.position(), fr.length(), QtGui.QTextCharFormat(fr.charFormat())))
                it += 1
            for pos, n, cf in frs:
                cf.clearProperty(QtGui.QTextFormat.FontSizeAdjustment)
                cf.setProperty(QtGui.QTextFormat.FontPixelSize, px)
                cf.setFontWeight(QtGui.QFont.DemiBold)
                cf.setForeground(QtGui.QColor(T["text"]))
                c = QtGui.QTextCursor(doc)
                c.setPosition(pos)
                c.setPosition(pos + n, QtGui.QTextCursor.KeepAnchor)
                c.setCharFormat(cf)
            nbf.setTopMargin(0 if first else 12)
            nbf.setBottomMargin(4)
        elif is_code:
            nbf.setBackground(QtGui.QColor(T["code_bg"]))
            nbf.setNonBreakableLines(False)                                  # quebra, não alarga o dock
            nbf.setLeftMargin(0)
            nbf.setTopMargin(6 if not prev_code else 0)
            nbf.setBottomMargin(6 if not next_code else 0)                  # margem só nas pontas
            cf = QtGui.QTextCharFormat(mono)
            cf.setForeground(QtGui.QColor(T["code_text"]))
            cur.mergeCharFormat(cf)
        else:
            nbf.setLineHeight(115, 1)                                       # 1 = ProportionalHeight
            if b.textList() is not None:
                nbf.setTopMargin(1)
                nbf.setBottomMargin(1)
            else:
                nbf.setTopMargin(0)
                nbf.setBottomMargin(8)
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                if fr.isValid():
                    if fr.charFormat().isAnchor():
                        ranges.append((fr.position(), fr.length(), "link"))
                    elif fr.charFormat().fontFixedPitch():
                        ranges.append((fr.position(), fr.length(), "code"))
                it += 1
        cur.setBlockFormat(nbf)
        t = QtGui.QTextCursor(b).currentTable()
        if t is not None:
            tables[t.firstPosition()] = t
        prev_code, first, b = is_code, False, nxt
    for pos, n, kind in ranges:                        # depois do laço (fragmentos se re-dividem)
        c = QtGui.QTextCursor(doc)
        c.setPosition(pos)
        c.setPosition(pos + n, QtGui.QTextCursor.KeepAnchor)
        cf = QtGui.QTextCharFormat()
        if kind == "link":
            cf.setForeground(QtGui.QColor(T["link"]))
            cf.setFontUnderline(True)
        else:
            cf.merge(mono)
            cf.setBackground(QtGui.QColor(T["code_inline"]))
            cf.setForeground(QtGui.QColor(T["code_accent"]))
        c.mergeCharFormat(cf)
    for t in tables.values():                          # só linhas horizontais
        tf = t.format()
        tf.setBorder(0)
        tf.setCellSpacing(0)
        tf.setCellPadding(4)
        tf.setBorderCollapse(True)
        t.setFormat(tf)
        for r in range(t.rows()):
            for col in range(t.columns()):
                cell = t.cellAt(r, col)
                cf = cell.format().toTableCellFormat()
                cf.setBottomBorder(1)
                cf.setBottomBorderBrush(QtGui.QBrush(QtGui.QColor(T["border2"])))
                cf.setBottomBorderStyle(QtGui.QTextFrameFormat.BorderStyle_Solid)
                if r == 0:
                    cf.setBackground(QtGui.QColor(T["surface"]))
                cell.setFormat(cf)


# ---------------------------------------------------------------------------
# miniaturas, visualizador, rodapé, avisos na conversa
# ---------------------------------------------------------------------------

class ImageThumb(QtWidgets.QWidget):
    def __init__(self, data, width=240, parent=None):
        super().__init__(parent)
        self.w = width
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.pic = ClickLabel()
        self.pic.setObjectName("ccThumb")
        self.pic.setCursor(QtCore.Qt.PointingHandCursor)
        self.pic.setToolTip("Clique para ampliar")
        self.pic.clicked.connect(self.open_viewer)
        cap = QtWidgets.QLabel("Vista capturada pelo agente · clique para ampliar")
        cap.setObjectName("ccThumbCaption")
        cap.setWordWrap(True)
        v.addWidget(self.pic, 0, QtCore.Qt.AlignLeft)
        v.addWidget(cap)
        self.set_image(data)

    def set_image(self, data):                         # bytes (get_view) ou caminho
        self.pix = QtGui.QPixmap()
        if isinstance(data, str):
            self.pix.load(data)
        else:
            self.pix.loadFromData(bytes(data))
        if self.pix.isNull():
            self.pic.setText("(imagem indisponível)")
            return
        self.pic.setPixmap(rounded(self.pix, self.w, radius=8))

    def open_viewer(self):
        if not self.pix.isNull():
            dlg = ImageViewer(self.pix, self.window())
            try:                                       # filha da janela: sem isto vivia até fechar o FreeCAD
                dlg.exec()
            finally:
                dlg.deleteLater()


class ImageViewer(QtWidgets.QDialog):
    def __init__(self, pix, parent=None):
        super().__init__(parent)
        self.setObjectName("ccViewer")
        self.setWindowTitle("Chat CAD — vista")
        self.setStyleSheet(TH.load_qss())
        self.setFont(TH.ui_font(13))
        screen = (parent.screen() if parent is not None else None) or QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()
        dpr = self.devicePixelRatioF()
        pm = pix.scaled(int(geo.width() * 0.85 * dpr), int(geo.height() * 0.85 * dpr),
                        QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        if pm.width() > pix.width():                   # não amplia além do tamanho real
            pm = QtGui.QPixmap(pix)
        pm.setDevicePixelRatio(dpr)
        lab = QtWidgets.QLabel()
        lab.setPixmap(pm)
        lab.setAlignment(QtCore.Qt.AlignCenter)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(lab)

    def mouseReleaseEvent(self, e):
        self.accept()                                  # Esc fecha (padrão do QDialog)


class TurnFooter(QtWidgets.QWidget):
    def __init__(self, text, ok=True, on_copy=None, on_params=None, on_restore=None, parent=None):
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(I.icon_label("check" if ok else "stop-circle", T["success"] if ok else T["text3"], 12))
        self.label = lab = ElidedLabel(text)
        lab.setObjectName("ccFooter")
        h.addWidget(lab, 1)
        self.buttons = []
        for name, tip, slot in (("copy", "Copiar resposta", on_copy),
                                ("table", "Abrir a planilha Parametros", on_params),
                                ("restore", "Desfazer: voltar ao backup de antes deste pedido", on_restore)):
            if slot:
                b = tool_btn("ccMiniBtn", name, tip, 14)
                b.clicked.connect(slot)
                h.addWidget(b)
                self.buttons.append(b)


class Callout(QtWidgets.QFrame):
    def __init__(self, kind, text, actions=(), parent=None):     # actions: [(rótulo, slot)]
        super().__init__(parent)
        self.setObjectName("ccCallout")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setProperty("kind", kind)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)
        name, col = {"error": ("x-circle", T["danger"]), "warn": ("alert", T["warn"]),
                     "info": ("info", T["info"])}[kind]
        h.addWidget(I.icon_label(name, col, 16), 0, QtCore.Qt.AlignTop)
        col_l = QtWidgets.QVBoxLayout()
        col_l.setSpacing(6)
        self.text = t = QtWidgets.QLabel(text)
        t.setObjectName("ccCalloutText")
        t.setWordWrap(True)
        t.setTextFormat(QtCore.Qt.PlainText)
        t.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        col_l.addWidget(t)
        self.buttons = []
        if actions:
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(6)
            for label, slot in actions:
                b = QtWidgets.QPushButton(label)
                b.setObjectName("ccBtn")
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(slot)
                r.addWidget(b)
                self.buttons.append(b)
            r.addStretch()
            col_l.addLayout(r)
        h.addLayout(col_l, 1)
