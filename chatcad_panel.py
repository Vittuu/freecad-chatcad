"""Painel lateral do Chat CAD (dock à esquerda do FreeCAD)."""

import os
import shutil
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from chatcad_backend import ADDON_DIR, PROVIDERS, AuthCheck, ClaudeRunner, attachments_dir, provider
from chatcad_sketch import SketchDialog, blank_sheet

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

PREFS = "User parameter:Plugins/ChatCAD"

DOCK_NAME = "ChatCAD"

WORKFLOWS = [
    ("⌂", "Suporte de parede",
     "Com furos de parafuso e escareado",
     "Quero um suporte de parede para "),
    ("▣", "Caixa com tampa",
     "Caixas para eletrônica, com encaixe",
     "Quero uma caixa com tampa para "),
    ("⬡", "Ventilação e colmeia",
     "Padrões vazados com borda sólida",
     "Faça um padrão de colmeia vazado na face selecionada, mantendo uma borda sólida de 4 mm."),
    ("◠", "Ajustar a peça atual",
     "Arredondar, furar, engrossar, mudar medidas",
     "Na peça atual, quero "),
    ("⇩", "Preparar para impressão",
     "Checar paredes finas e exportar STL / 3MF",
     "Verifique se a peça está boa para impressão 3D (paredes finas, sólido único, balanços) "
     "e exporte um STL ao lado do arquivo."),
]

QUICK_ACTIONS = [
    ("Ações rápidas…", ""),
    ("Arredondar a seleção", "Arredonde as arestas selecionadas com raio de 3 mm, como parâmetro."),
    ("Furo na face selecionada", "Faça um furo passante de 5 mm no ponto que eu cliquei na face selecionada."),
    ("Colmeia na face selecionada",
     "Faça um padrão de colmeia vazado na face selecionada, mantendo uma borda sólida de 4 mm."),
    ("Explicar a peça", "Explique em poucas linhas como esta peça foi construída e quais parâmetros existem."),
    ("Checar para impressão 3D", "Verifique se a peça está boa para impressão 3D e me diga o que melhorar."),
    ("Exportar STL", "Exporte a peça final como STL na mesma pasta do arquivo."),
]


TOOL_LABELS = {
    "execute_code": "Executando no FreeCAD",
    "execute_code_async": "Executando em segundo plano",
    "get_view": "Olhando a peça",
    "get_objects": "Lendo os objetos",
    "get_object": "Lendo um objeto",
    "create_object": "Criando objeto",
    "edit_object": "Editando objeto",
    "delete_object": "Removendo objeto",
    "create_document": "Criando documento",
    "list_documents": "Listando documentos",
    "run_fem_analysis": "Rodando análise de esforço",
    "insert_part_from_library": "Inserindo peça da biblioteca",
}


# passos internos dos agentes (procurar/ler a definição das ferramentas): não poluem o chat
HIDDEN_TOOLS = {"ToolSearch", "view_file", "list_mcp_resources", "list_mcp_resource_templates"}


def _tool_label(name):
    short = name.split("__")[-1]
    return TOOL_LABELS.get(short, short.replace("_", " "))


# ---------------------------------------------------------------------------
# pedaços da interface
# ---------------------------------------------------------------------------

class WorkflowCard(QtWidgets.QFrame):
    clicked = QtCore.Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def __init__(self, icon, title, subtitle, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(QtCore.Qt.WA_Hover)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        ic = QtWidgets.QLabel(icon)
        ic.setObjectName("cardIcon")
        ic.setFixedWidth(18)
        ic.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(1)
        t = QtWidgets.QLabel(title)
        t.setObjectName("cardTitle")
        s = QtWidgets.QLabel(subtitle)
        s.setObjectName("cardSub")
        s.setWordWrap(False)
        txt.addWidget(t)
        txt.addWidget(s)
        lay.addWidget(ic)
        lay.addLayout(txt, 1)
        for w in (ic, t, s):
            w.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setMinimumHeight(48)


class Prompt(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()
    imagePasted = QtCore.Signal(object)   # QImage (Ctrl+V de um print)
    filesDropped = QtCore.Signal(list)    # caminhos de imagens arrastadas/coladas

    def canInsertFromMimeData(self, src):
        return src.hasImage() or src.hasUrls() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src):
        if src.hasUrls():
            paths = [u.toLocalFile() for u in src.urls()
                     if u.toLocalFile().lower().endswith(IMG_EXT)]
            if paths:
                self.filesDropped.emit(paths)
                return
        if src.hasImage():
            img = QtGui.QImage(src.imageData())
            if not img.isNull():
                self.imagePasted.emit(img)
                return
        super().insertFromMimeData(src)

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and \
                not (e.modifiers() & QtCore.Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(e)


class AttachThumb(QtWidgets.QFrame):
    """Miniatura de um anexo, com × para remover e clique para desenhar por cima."""

    removed = QtCore.Signal(str)
    edit = QtCore.Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setObjectName("attach")
        self.setFixedSize(56, 56)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Clique para desenhar por cima")
        pic = QtWidgets.QLabel(self)
        pic.setGeometry(2, 2, 52, 52)
        pm = QtGui.QPixmap(path)
        pic.setPixmap(pm.scaled(52, 52, QtCore.Qt.KeepAspectRatioByExpanding,
                                QtCore.Qt.SmoothTransformation))
        pic.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        x = QtWidgets.QToolButton(self)
        x.setObjectName("attachX")
        x.setText("×")
        x.setGeometry(36, 2, 18, 18)
        x.clicked.connect(lambda: self.removed.emit(self.path))

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.edit.emit(self.path)


class ToolRow(QtWidgets.QLabel):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName("toolRow")
        self.label = _tool_label(name)
        self.started = time.time()
        self.setText(f"◌  {self.label}…")

    def done(self, error):
        secs = time.time() - self.started
        mark = "✕" if error else "✓"
        self.setText(f"{mark}  {self.label}  ·  {secs:.0f}s")
        self.setProperty("error", error)
        self.style().unpolish(self)
        self.style().polish(self)


class ImageThumb(QtWidgets.QLabel):
    def __init__(self, data, parent=None, width=220):
        super().__init__(parent)
        self.setObjectName("thumb")
        self.pix = QtGui.QPixmap()
        if isinstance(data, str):
            self.pix.load(data)
        else:
            self.pix.loadFromData(data)
        self.setPixmap(self.pix.scaledToWidth(width, QtCore.Qt.SmoothTransformation))
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Clique para ampliar")

    def mousePressEvent(self, e):
        dlg = QtWidgets.QDialog(self.window())
        dlg.setWindowTitle("Chat CAD — vista")
        lay = QtWidgets.QVBoxLayout(dlg)
        lab = QtWidgets.QLabel()
        lab.setPixmap(self.pix)
        lay.addWidget(lab)
        dlg.exec()


# ---------------------------------------------------------------------------
# painel principal
# ---------------------------------------------------------------------------

class ChatPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatcad")
        self.runner = ClaudeRunner(self)
        self.runner.textDelta.connect(self._on_text)
        self.runner.turnBreak.connect(self._on_break)
        self.runner.toolStarted.connect(self._on_tool)
        self.runner.toolFinished.connect(self._on_tool_done)
        self.runner.finished.connect(self._on_finished)
        self.runner.failed.connect(self._on_failed)
        self.auth = AuthCheck(self)
        self.auth.finished.connect(self._on_auth)

        self._answer = None
        self._answer_text = ""
        self._tools = {}
        self._turn_started = 0.0
        self._use_selection = True
        self._attachments = []

        with open(os.path.join(ADDON_DIR, "chatcad.qss"), encoding="utf-8") as f:
            self.setStyleSheet(f.read())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_banner())

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_chat())
        root.addWidget(self.stack, 1)
        root.addWidget(self._build_composer())

        self.sel_timer = QtCore.QTimer(self)
        self.sel_timer.timeout.connect(self._refresh_selection)
        self.sel_timer.start(400)
        self._apply_provider(App.ParamGet(PREFS).GetString("Provider", "claude"), initial=True)

    # --- construção --------------------------------------------------------

    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("header")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 8, 10)
        badge = QtWidgets.QLabel("CHAT")
        badge.setObjectName("badge")
        name = QtWidgets.QLabel("CAD")
        name.setObjectName("brand")
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(0)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(badge)
        row.addWidget(name)
        row.addStretch()
        col.addLayout(row)
        sub = QtWidgets.QLabel("Agente de modelagem")
        sub.setObjectName("muted")
        col.addWidget(sub)
        lay.addLayout(col, 1)
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setObjectName("dotIdle")
        self.status_dot.setToolTip("Verificando conexão…")
        lay.addWidget(self.status_dot)
        new = QtWidgets.QToolButton()
        new.setObjectName("iconBtn")
        new.setText("＋")
        new.setToolTip("Nova conversa")
        new.clicked.connect(self.new_chat)
        lay.addWidget(new)
        return bar

    def _build_banner(self):
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("banner")
        lay = QtWidgets.QHBoxLayout(self.banner)
        lay.setContentsMargins(14, 8, 10, 8)
        self.banner_text = QtWidgets.QLabel("Verificando o Claude…")
        self.banner_text.setObjectName("bannerText")
        self.banner_text.setWordWrap(True)
        lay.addWidget(self.banner_text, 1)
        self.banner_btn = QtWidgets.QPushButton("Conectar")
        self.banner_btn.setObjectName("ghostBtn")
        self.banner_btn.clicked.connect(self._open_login)
        lay.addWidget(self.banner_btn)
        recheck = QtWidgets.QToolButton()
        recheck.setObjectName("iconBtn")
        recheck.setText("↻")
        recheck.setToolTip("Verificar de novo")
        recheck.clicked.connect(lambda: self.auth.run(self.runner.prov))
        lay.addWidget(recheck)
        self.banner.hide()
        return self.banner

    def _build_empty(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.addStretch(1)
        kicker = QtWidgets.QLabel("Comece aqui")
        kicker.setObjectName("kicker")
        title = QtWidgets.QLabel("Comece com um objetivo")
        title.setObjectName("title")
        sub = QtWidgets.QLabel("Escolha um ponto de partida, ou descreva a peça que você quer.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(kicker)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(10)
        for icon, t, s, prompt in WORKFLOWS:
            card = WorkflowCard(icon, t, s)
            card.clicked.connect(lambda p=prompt: self._prefill(p))
            lay.addWidget(card)
        lay.addStretch(2)
        return page

    def _build_chat(self):
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        holder = QtWidgets.QWidget()
        holder.setObjectName("chatHolder")
        self.chat = QtWidgets.QVBoxLayout(holder)
        self.chat.setContentsMargins(14, 12, 14, 12)
        self.chat.setSpacing(8)
        self.chat.addStretch(1)
        self.scroll.setWidget(holder)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _a, b: self.scroll.verticalScrollBar().setValue(b))
        return self.scroll

    def _build_composer(self):
        box = QtWidgets.QFrame()
        box.setObjectName("composerArea")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)

        self.quick = QtWidgets.QComboBox()
        self.quick.setObjectName("quick")
        for label, _ in QUICK_ACTIONS:
            self.quick.addItem(label)
        self.quick.activated.connect(self._quick_action)
        lay.addWidget(self.quick)

        self.sel_chip = QtWidgets.QPushButton()
        self.sel_chip.setObjectName("chip")
        self.sel_chip.setToolTip("Seleção do FreeCAD que vai junto com a mensagem. Clique para ignorar.")
        self.sel_chip.clicked.connect(self._toggle_selection)
        self.sel_chip.hide()
        lay.addWidget(self.sel_chip)

        frame = QtWidgets.QFrame()
        frame.setObjectName("composer")
        fl = QtWidgets.QVBoxLayout(frame)
        fl.setContentsMargins(10, 8, 8, 8)
        fl.setSpacing(4)
        self.input = Prompt()
        self.input.setObjectName("prompt")
        self.input.setPlaceholderText("Descreva o que você quer criar…")
        self.input.setFixedHeight(64)
        self.input.submitted.connect(self.send)
        self.input.imagePasted.connect(lambda img: self._attach_qimage(img, "colado"))
        self.input.filesDropped.connect(self._attach_files)
        self.attach_row = QtWidgets.QHBoxLayout()
        self.attach_row.setSpacing(6)
        self.attach_row.setContentsMargins(0, 0, 0, 2)
        self.attach_row.addStretch()
        self.attach_box = QtWidgets.QWidget()
        self.attach_box.setLayout(self.attach_row)
        self.attach_box.hide()
        fl.addWidget(self.attach_box)
        fl.addWidget(self.input)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.mode = QtWidgets.QComboBox()
        self.mode.setObjectName("pill")
        self.mode.addItem("✦ Agente")
        self.mode.addItem("◇ Planejar")
        self.mode.setToolTip("Agente: modela direto.  Planejar: só conversa e propõe, sem mexer na peça.")
        self.agent = QtWidgets.QComboBox()
        self.agent.setObjectName("pill")
        self.agent.setToolTip("Qual IA vai trabalhar na peça")
        for p in PROVIDERS:
            self.agent.addItem(p.name, p.key)
        self.agent.activated.connect(lambda i: self._apply_provider(self.agent.itemData(i)))
        self.model = QtWidgets.QComboBox()
        self.model.setObjectName("pill")
        self.model.setToolTip("Modelo")
        self.model.activated.connect(
            lambda i: App.ParamGet(PREFS).SetString("Model_" + self.runner.prov.key, self.model.itemData(i) or ""))
        plus = QtWidgets.QToolButton()
        plus.setObjectName("plusBtn")
        plus.setText("＋")
        plus.setToolTip("Anexar imagem ou desenhar")
        plus.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(plus)
        menu.addAction("✎  Desenhar na vista 3D", self._draw_on_view)
        menu.addAction("▭  Folha em branco (croqui)", self._draw_blank)
        menu.addAction("🖼  Anexar imagem…", self._pick_files)
        plus.setMenu(menu)
        row.addWidget(plus)
        row.addWidget(self.agent)
        row.addWidget(self.mode)
        row.addWidget(self.model)
        row.addStretch()
        self.send_btn = QtWidgets.QPushButton("↑")
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.setToolTip("Enviar (Enter)")
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.send_btn)
        fl.addLayout(row)
        lay.addWidget(frame)
        return box

    # --- ações ---------------------------------------------------------------

    def _prefill(self, text):
        self.input.setPlainText(text)
        self.input.setFocus()
        self.input.moveCursor(QtGui.QTextCursor.End)

    def _quick_action(self, idx):
        prompt = QUICK_ACTIONS[idx][1]
        self.quick.setCurrentIndex(0)
        if prompt:
            self._prefill(prompt)

    # --- anexos -------------------------------------------------------------

    def _attach_qimage(self, img, tag):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(attachments_dir(), f"{stamp}-{tag}.png")
        n = 1
        while os.path.exists(path):
            path = os.path.join(attachments_dir(), f"{stamp}-{tag}-{n}.png")
            n += 1
        img.save(path, "PNG")
        self._add_attachment(path)

    def _attach_files(self, paths):
        for p in paths:
            if os.path.isfile(p):
                self._add_attachment(os.path.normpath(p))

    def _add_attachment(self, path):
        if path not in self._attachments:
            self._attachments.append(path)
            self._refresh_attachments()

    def _refresh_attachments(self):
        while self.attach_row.count() > 1:
            w = self.attach_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for path in self._attachments:
            t = AttachThumb(path)
            t.removed.connect(self._remove_attachment)
            t.edit.connect(self._edit_attachment)
            self.attach_row.insertWidget(self.attach_row.count() - 1, t)
        self.attach_box.setVisible(bool(self._attachments))

    def _remove_attachment(self, path):
        if path in self._attachments:
            self._attachments.remove(path)
        self._refresh_attachments()

    def _sketch(self, image, title):
        dlg = SketchDialog(image, title, Gui.getMainWindow())
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            return dlg.image()
        return None

    def _edit_attachment(self, path):
        img = self._sketch(QtGui.QImage(path), "Desenhar por cima")
        if img is not None:
            i = self._attachments.index(path)
            new = os.path.join(attachments_dir(), time.strftime("%Y%m%d-%H%M%S-rabisco.png"))
            img.save(new, "PNG")
            self._attachments[i] = new
            self._refresh_attachments()

    def _draw_on_view(self):
        doc = Gui.ActiveDocument
        view = doc.ActiveView if doc else None
        if view is None or not hasattr(view, "saveImage"):
            self._draw_blank()
            return
        tmp = os.path.join(attachments_dir(), "_vista.png")
        try:
            w, h = view.getSize()
        except Exception:
            w, h = 1200, 900
        scale = min(1.5, 1600.0 / max(w, h, 1))
        view.saveImage(tmp, int(w * scale), int(h * scale), "Current")
        img = self._sketch(QtGui.QImage(tmp), "Desenhar na vista")
        if img is not None:
            self._attach_qimage(img, "vista")

    def _draw_blank(self):
        img = self._sketch(blank_sheet(), "Folha em branco")
        if img is not None:
            self._attach_qimage(img, "croqui")

    def _pick_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Anexar imagens", os.path.expanduser("~"),
            "Imagens (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        self._attach_files(paths)

    def new_chat(self):
        self.runner.reset()
        while self.chat.count() > 1:
            item = self.chat.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._answer = None
        self.stack.setCurrentIndex(0)
        self._set_busy(False)

    def _send_or_stop(self):
        if self.runner.busy():
            self.runner.stop()
            self._add_note("Interrompido.")
            self._set_busy(False)
        else:
            self.send()

    def send(self):
        text = self.input.toPlainText().strip()
        images = list(self._attachments)
        if (not text and not images) or self.runner.busy():
            return
        if not text:
            text = "Veja a imagem anexada e faça o que está marcado nela."
        self.input.clear()
        self._attachments = []
        self._refresh_attachments()
        self.stack.setCurrentIndex(1)
        for path in images:
            self._insert(ImageThumb(path, width=150), QtCore.Qt.AlignRight)
        self._add_bubble(text)

        context = self._context_text()
        prompt = f"{context}\n\n{text}" if context else text
        doc = App.ActiveDocument
        cwd = os.path.dirname(doc.FileName) if doc and doc.FileName else os.path.expanduser("~")
        self._answer = None
        self._tools = {}
        self._turn_started = time.time()
        self._set_busy(True)
        self.runner.send(prompt,
                         model=self.model.currentData() or "",
                         plan=self.mode.currentIndex() == 1,
                         cwd=cwd,
                         images=images)

    def _set_busy(self, busy):
        self.send_btn.setText("■" if busy else "↑")
        self.send_btn.setToolTip("Parar" if busy else "Enviar (Enter)")
        self.status_dot.setObjectName("dotBusy" if busy else
                                      ("dotOk" if self.banner.isHidden() else "dotIdle"))
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    # --- seleção do FreeCAD --------------------------------------------------

    def _selection(self):
        try:
            return Gui.Selection.getSelectionEx("", 0)
        except Exception:
            return []

    def _refresh_selection(self):
        sel = self._selection()
        if not sel:
            self.sel_chip.hide()
            self._use_selection = True
            return
        parts = []
        for s in sel:
            subs = [n.split(".")[-1] for n in s.SubElementNames if n]
            label = s.Object.Label
            parts.append(f"{label} › {', '.join(subs[:3])}" if subs else label)
        txt = "  ·  ".join(parts)
        if len(txt) > 60:
            txt = txt[:57] + "…"
        prefix = "◎ Seleção: " if self._use_selection else "○ Ignorando: "
        self.sel_chip.setText(prefix + txt)
        self.sel_chip.setProperty("off", not self._use_selection)
        self.sel_chip.style().unpolish(self.sel_chip)
        self.sel_chip.style().polish(self.sel_chip)
        self.sel_chip.show()

    def _toggle_selection(self):
        self._use_selection = not self._use_selection
        self._refresh_selection()

    def _context_text(self):
        doc = App.ActiveDocument
        lines = []
        if doc:
            lines.append(f"Documento ativo: {doc.Name}" + (f" ({doc.FileName})" if doc.FileName else " (não salvo)"))
        sel = self._selection() if self._use_selection else []
        if sel:
            lines.append("Contexto da seleção (o que o usuário clicou no FreeCAD):")
            for s in sel:
                subs = [n for n in s.SubElementNames if n] or ["(objeto inteiro)"]
                pts = [f"({p.x:.2f}, {p.y:.2f}, {p.z:.2f})" for p in s.PickedPoints]
                line = f"- objeto {s.ObjectName} (\"{s.Object.Label}\"), elementos: {', '.join(subs)}"
                if pts:
                    line += f"; pontos clicados: {', '.join(pts)}"
                lines.append(line)
        return "\n".join(lines)

    # --- mensagens -----------------------------------------------------------

    def _insert(self, widget, align=None):
        if align is not None:
            wrap = QtWidgets.QHBoxLayout()
            if align == QtCore.Qt.AlignRight:
                wrap.addStretch()
            wrap.addWidget(widget)
            if align == QtCore.Qt.AlignLeft:
                wrap.addStretch()
            holder = QtWidgets.QWidget()
            holder.setLayout(wrap)
            wrap.setContentsMargins(0, 0, 0, 0)
            widget = holder
        self.chat.insertWidget(self.chat.count() - 1, widget)

    def _add_bubble(self, text):
        b = QtWidgets.QLabel(text)
        b.setObjectName("userBubble")
        b.setWordWrap(True)
        b.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        b.setMaximumWidth(max(200, int(self.width() * 0.85)))
        self._insert(b, QtCore.Qt.AlignRight)

    def _add_note(self, text, error=False):
        n = QtWidgets.QLabel(text)
        n.setObjectName("errorNote" if error else "note")
        n.setWordWrap(True)
        n.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._insert(n)

    def _on_break(self):
        self._answer = None

    def _on_text(self, chunk):
        if self._answer is None:
            self._answer = QtWidgets.QLabel()
            self._answer.setObjectName("answer")
            self._answer.setWordWrap(True)
            self._answer.setTextFormat(QtCore.Qt.MarkdownText)
            self._answer.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse)
            self._answer.setOpenExternalLinks(True)
            self._answer_text = ""
            self._insert(self._answer)
        self._answer_text += chunk
        self._answer.setText(self._answer_text)

    def _on_tool(self, tool_id, name, _input):
        if name.split("__")[-1] in HIDDEN_TOOLS:
            return
        self._answer = None
        row = ToolRow(name)
        self._tools[tool_id] = row
        self._insert(row)

    def _on_tool_done(self, tool_id, error, images):
        row = self._tools.get(tool_id)
        if row:
            row.done(error)
        for data in images[:1]:
            self._insert(ImageThumb(data))

    def _on_finished(self, result):
        self._set_busy(False)
        self._answer = None
        secs = time.time() - self._turn_started
        if result.get("is_error"):
            msg = result.get("result") or "Algo deu errado."
            self._add_note(msg, error=True)
            if "login" in str(msg).lower():
                self.auth.run(self.runner.prov)
            return
        self._add_note(f"concluído em {secs:.0f}s")
        try:
            doc = App.ActiveDocument
            if doc:
                doc.recompute()
                Gui.updateGui()
        except Exception:
            pass

    def _on_failed(self, msg):
        self._set_busy(False)
        self._add_note(msg, error=True)

    # --- conexão com o Claude ------------------------------------------------

    def _on_auth(self, ok, msg):
        if ok:
            self.banner.hide()
            self.status_dot.setToolTip("Claude conectado")
        else:
            self.banner_text.setText(
                msg + (f" Clique em Conectar. {self.runner.prov.login_hint} Depois clique em ↻."
                       if self.runner.prov.exe() else ""))
            self.banner_btn.setVisible(self.runner.prov.exe() is not None)
            self.banner.show()
            self.status_dot.setToolTip("Claude desconectado")
        self._set_busy(self.runner.busy())

    def _open_login(self):
        cmd = self.runner.prov.login_cmd()
        if not (cmd and cmd[0]):
            return
        import shlex
        import sys
        title = "Login - " + self.runner.prov.name
        if sys.platform == "win32":
            QtCore.QProcess.startDetached("cmd.exe", ["/c", "start", title, "cmd", "/k"] + cmd)
            return
        line = " ".join(shlex.quote(c) for c in cmd)
        if sys.platform == "darwin":
            script = f'tell application "Terminal" to do script "{line}"'
            QtCore.QProcess.startDetached("osascript", ["-e", script, "-e", 'tell application "Terminal" to activate'])
            return
        for term, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]),
                           ("konsole", ["-e"]), ("xterm", ["-e"])):
            if shutil.which(term):
                QtCore.QProcess.startDetached(term, args + ["sh", "-c", f"{line}; exec $SHELL"])
                return
        QtWidgets.QMessageBox.information(
            self, title, f"Abra um terminal e rode:\n\n{line}\n\nDepois clique em ↻ no painel.")

    def _apply_provider(self, key, initial=False):
        prov = provider(key)
        if not initial and prov is self.runner.prov:
            return
        if not initial:
            self.new_chat()
        self.runner.set_provider(prov)
        App.ParamGet(PREFS).SetString("Provider", prov.key)
        self.agent.setCurrentIndex(max(0, self.agent.findData(prov.key)))
        self.model.clear()
        for label, value in prov.models():
            self.model.addItem(label, value)
        saved = App.ParamGet(PREFS).GetString("Model_" + prov.key, "")
        idx = self.model.findData(saved)
        self.model.setCurrentIndex(idx if idx >= 0 else 0)
        self.banner_text.setText(f"Verificando o {prov.name}…")
        self.auth.run(prov)


# ---------------------------------------------------------------------------

def install():
    mw = Gui.getMainWindow()
    if mw.findChild(QtWidgets.QDockWidget, DOCK_NAME):
        return
    dock = QtWidgets.QDockWidget("Chat CAD", mw)
    dock.setObjectName(DOCK_NAME)
    dock.setWidget(ChatPanel(dock))
    dock.setMinimumWidth(300)
    mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
    others = [d for d in mw.findChildren(QtWidgets.QDockWidget)
              if d is not dock and mw.dockWidgetArea(d) == QtCore.Qt.LeftDockWidgetArea and d.isVisible()]
    if others:
        mw.splitDockWidget(dock, others[0], QtCore.Qt.Horizontal)
    mw.resizeDocks([dock], [360], QtCore.Qt.Horizontal)
    toggle = dock.toggleViewAction()
    toggle.setText("Chat CAD")
    toggle.setShortcut(QtGui.QKeySequence("Ctrl+Shift+Space"))
    mw.addAction(toggle)
    dock.show()
    return dock
