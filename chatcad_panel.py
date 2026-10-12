"""Painel lateral do Chat CAD (dock à esquerda do FreeCAD).

Peças visuais em chatcad_widgets, cores em chatcad_theme (tokens) + chatcad.qss, ícones em
chatcad_icons. A folha de estilo é aplicada uma vez só, no QDockWidget (ver install())."""

import html
import os
import shutil
import sys
import time

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide import QtCore, QtGui, QtWidgets          # shim do FreeCAD
except ImportError:                                       # testes offscreen fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtWidgets

import chatcad_icons as I
import chatcad_theme as TH
from chatcad_backend import PROVIDERS, AuthCheck, ClaudeRunner, attachments_dir, provider
from chatcad_sketch import SketchDialog, blank_sheet
from chatcad_theme import T, fmt_secs, set_prop
from chatcad_widgets import (IMG_EXT, PLACEHOLDER, QUICK_ACTIONS, TOOL_META, WORKFLOWS, AnswerView,
                             AttachThumb, Banner, Callout, ContextChip, DockTitleBar, ImageThumb,
                             MenuButton, PillButton, Prompt, SendButton, StepsGroup, TurnFooter,
                             TurnHeader, UserBubble, WorkflowCard, describe_tool, menu_section)

PREFS = "User parameter:Plugins/ChatCAD"
DOCK_NAME = "ChatCAD"
NARROW = 360            # abaixo disso (px) os seletores encolhem

# rótulos dos passos (MCP do FreeCAD + IA local); ícones e títulos ficam em chatcad_widgets.TOOL_META
TOOL_LABELS = {k: v[1] for k, v in TOOL_META.items()}

# passos internos dos agentes (procurar/ler a definição das ferramentas): não poluem o chat
HIDDEN_TOOLS = {"ToolSearch", "view_file", "list_mcp_resources", "list_mcp_resource_templates"}

__all__ = ["ChatPanel", "install", "IMG_EXT", "TOOL_LABELS", "HIDDEN_TOOLS"]


def _tool_label(name):
    short = name.split("__")[-1]
    return TOOL_LABELS.get(short, short.replace("_", " "))


def _is_local(prov):
    return bool(getattr(prov, "in_process", False))


def _local_host(prov):
    """None se o servidor da IA local é este computador (loopback); senão o host que recebe os pedidos.
    Na dúvida (configuração ilegível) não promete nada: devolve "?"."""
    from chatcad_local import _base, _is_loopback
    try:
        url = _base(prov.settings())
    except Exception:
        return "?"
    return None if _is_loopback(url) else (QtCore.QUrl(url).host() or url)


# ---------------------------------------------------------------------------
# painel principal
# ---------------------------------------------------------------------------

class ChatPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        TH.ensure()
        self.setObjectName("ccPanel")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)

        self.runner = ClaudeRunner(self)
        self.runner.textDelta.connect(self._on_text)
        self.runner.turnBreak.connect(self._on_break)
        self.runner.toolStarted.connect(self._on_tool)
        self.runner.toolFinished.connect(self._on_tool_done)
        self.runner.thinkingDelta.connect(self._on_thinking)
        self.runner.finished.connect(self._on_finished)
        self.runner.failed.connect(self._on_failed)
        self.runner.idle.connect(self._on_idle)
        self.auth = AuthCheck(self)
        self.auth.finished.connect(self._on_auth)

        # estado da conversa
        self._answer = self._group = self._thumb = self._think = None
        self._answers, self._bubbles = [], []
        self._turns = 0
        self._turn_started = 0.0
        self._stopping = False
        self._use_selection = True
        self._attachments = []
        self._last_request = None
        self._stats_mark = 0
        self._model = ""
        self._labels = {}
        self._online = None              # None = verificando
        self._auth_msg = ""
        self._local_catalog = None
        self._exe_cache = {}             # provedor -> caminho do CLI (o menu de modelos não procura de novo)
        self._busy_state = False
        self._send_when_idle = False     # Enter (True) ou "Tentar de novo" (o pedido) enquanto o CLI fecha
        self.mode = "agent"

        # barra de título: install() a coloca como titleBarWidget do dock
        self.title = DockTitleBar()
        self.title.newChat.connect(self.new_chat)
        self.title.gear_btn.clicked.connect(self._open_settings)
        self.title.more_btn.setMenu(self.build_more_menu())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.banner = Banner()
        self.banner.action.clicked.connect(self._open_login)
        self.banner.recheck.clicked.connect(self._recheck)
        root.addWidget(self.banner)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("ccPages")
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_chat())
        root.addWidget(self.stack, 1)
        root.addWidget(self._build_composer())

        self.sel_timer = QtCore.QTimer(self)
        self.sel_timer.timeout.connect(self._refresh_selection)
        self.sel_timer.start(400)
        self._apply_provider(App.ParamGet(PREFS).GetString("Provider", "claude"), initial=True)

    # --- construção --------------------------------------------------------

    def build_more_menu(self):
        m = TH.popup_menu(self.title.more_btn)
        m.aboutToShow.connect(lambda: self._fill_more_menu(m))
        return m

    def _fill_more_menu(self, m):
        m.clear()

        def add(ic, text, slot, enabled=True):
            a = m.addAction(I.icon(ic, T["text2"], 14, hover=T["text"]), text)
            a.setEnabled(enabled)
            a.triggered.connect(slot)
            return a

        add("new-chat", "Nova conversa", self.new_chat)
        add("refresh", "Verificar conexão", self._recheck)
        if any(_is_local(p) for p in PROVIDERS):
            add("cpu", "Atualizar modelos locais", self._refresh_local_models)
        if _is_local(self.runner.prov):
            add("settings", "Configurar a IA local…", self._open_settings)
        m.addSeparator()
        add("folder", "Abrir pasta de anexos", self._open_attachments_dir)
        add("file-text", "Abrir log do último pedido", self._open_log, os.path.exists(self._log_path()))
        dock = self.title.dock
        if dock is not None:
            m.addSeparator()
            add("undock", "Encaixar de novo" if dock.isFloating() else "Desencaixar o painel",
                lambda: dock.setFloating(not dock.isFloating()))

    def _build_empty(self):
        page = QtWidgets.QWidget()
        page.setObjectName("ccEmpty")
        page.setAttribute(QtCore.Qt.WA_StyledBackground)
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(0)
        lay.addStretch(1)
        kicker = QtWidgets.QLabel("COMECE AQUI")
        kicker.setObjectName("ccKicker")
        f = kicker.font()
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.6)
        kicker.setFont(f)
        lay.addWidget(kicker)
        lay.addSpacing(4)
        for text, obj, gap in (("O que vamos modelar?", "ccHeadline", 6),
                               ("Escolha um ponto de partida ou descreva a peça. Tudo sai com parâmetros "
                                "que você ajusta depois.", "ccLead", 16)):
            lab = QtWidgets.QLabel(text)
            lab.setObjectName(obj)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            lay.addSpacing(gap)
        self.cards = []
        for i, (ic, t, s, prompt) in enumerate(WORKFLOWS):
            card = WorkflowCard(ic, t, s, last=i == len(WORKFLOWS) - 1)
            card.clicked.connect(lambda p=prompt: self._prefill(p))
            lay.addWidget(card)
            self.cards.append(card)
        lay.addSpacing(14)
        tip = QtWidgets.QHBoxLayout()
        tip.setSpacing(8)
        tip.addWidget(I.icon_label("crosshair", T["text3"], 14), 0, QtCore.Qt.AlignTop)
        t = QtWidgets.QLabel("Dica: clique numa face da peça antes de pedir — a seleção vai junto com a mensagem.")
        t.setObjectName("ccTip")
        t.setWordWrap(True)
        tip.addWidget(t, 1)
        lay.addLayout(tip)
        lay.addSpacing(8)
        self.footnote = QtWidgets.QLabel()
        self.footnote.setObjectName("ccFootnote")
        self.footnote.setWordWrap(True)
        lay.addWidget(self.footnote)
        lay.addStretch(2)
        scroll = QtWidgets.QScrollArea()            # em painéis baixos a tela inicial rola
        scroll.setObjectName("ccScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _build_chat(self):
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("ccScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        holder = QtWidgets.QWidget()
        holder.setObjectName("ccChatHolder")
        holder.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.chat = QtWidgets.QVBoxLayout(holder)
        self.chat.setContentsMargins(16, 12, 16, 16)
        self.chat.setSpacing(0)
        self.chat.addStretch(1)
        self.scroll.setWidget(holder)
        # grudar no fim só se o usuário já estava lá
        bar = self.scroll.verticalScrollBar()
        self._stick = True
        bar.valueChanged.connect(lambda v: setattr(self, "_stick", v >= bar.maximum() - 24))
        bar.rangeChanged.connect(lambda _a, mx: bar.setValue(mx) if self._stick else None)
        return self.scroll

    def _build_composer(self):
        area = QtWidgets.QFrame()
        area.setObjectName("ccComposerArea")
        area.setAttribute(QtCore.Qt.WA_StyledBackground)
        lay = QtWidgets.QVBoxLayout(area)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        self.quick = MenuButton("Ações rápidas")
        self.quick.setObjectName("ccQuick")
        self.quick.setIcon(I.icon("zap", T["text2"], 14))
        self.quick.setIconSize(QtCore.QSize(14, 14))
        self.quick.setCursor(QtCore.Qt.PointingHandCursor)
        self.quick.setFocusPolicy(QtCore.Qt.TabFocus)
        qm = TH.popup_menu(self.quick)
        qm.aboutToShow.connect(lambda: self._build_quick_menu(qm))
        self.quick.setMenu(qm)
        lay.addWidget(self.quick)

        self.composer = QtWidgets.QFrame()
        self.composer.setObjectName("ccComposer")
        self.composer.setAttribute(QtCore.Qt.WA_StyledBackground)
        fl = QtWidgets.QVBoxLayout(self.composer)
        fl.setContentsMargins(10, 8, 8, 8)
        fl.setSpacing(6)
        self.sel_chip = ContextChip()
        self.sel_chip.clicked.connect(self._toggle_selection)
        self.sel_chip.hide()
        fl.addWidget(self.sel_chip, 0, QtCore.Qt.AlignLeft)
        self.attach_box = QtWidgets.QWidget()
        self.attach_row = QtWidgets.QHBoxLayout(self.attach_box)
        self.attach_row.setContentsMargins(0, 0, 0, 0)
        self.attach_row.setSpacing(6)
        self.attach_row.addStretch()
        self.attach_box.hide()
        fl.addWidget(self.attach_box)
        self.vision_warn = QtWidgets.QLabel()
        self.vision_warn.setObjectName("ccWarnLine")
        self.vision_warn.setWordWrap(True)
        self.vision_warn.hide()
        fl.addWidget(self.vision_warn)
        self.input = Prompt()
        self.input.setPlaceholderText(PLACEHOLDER["agent"])
        self.input.submitted.connect(self.send)
        # Esc só para o que a tela mostra rodando (depois do "result" o CLI ainda fecha: não é parar)
        self.input.stopRequested.connect(lambda: self._busy_state and self._send_or_stop())
        self.input.textChanged.connect(self._sync_send)
        self.input.focusChanged.connect(lambda on: set_prop(self.composer, "focus", on))
        self.input.dragActive.connect(lambda on: set_prop(self.composer, "drop", on))
        self.input.imagePasted.connect(lambda img: self._attach_qimage(img, "colado"))
        self.input.filesDropped.connect(self._attach_files)
        fl.addWidget(self.input)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.plus = plus = QtWidgets.QToolButton()
        plus.setObjectName("ccRound")
        plus.setAutoRaise(True)
        plus.setIcon(I.icon("plus", T["text2"], 14, hover=T["text"]))
        plus.setIconSize(QtCore.QSize(14, 14))
        plus.setToolTip("Anexar imagem ou desenhar")
        plus.setAccessibleName("Anexar")
        plus.setCursor(QtCore.Qt.PointingHandCursor)
        plus.setFocusPolicy(QtCore.Qt.TabFocus)
        plus.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        pm = TH.popup_menu(plus)
        for ic, label, slot in (("pencil", "Desenhar na vista 3D", self._draw_on_view),
                                ("sheet", "Folha em branco (croqui)", self._draw_blank), (None, None, None),
                                ("image", "Anexar imagem…", self._pick_files),
                                ("clipboard", "Colar print\tCtrl+V", self._paste_clipboard)):
            if ic is None:
                pm.addSeparator()
                continue
            pm.addAction(I.icon(ic, T["text2"], 14, hover=T["text"]), label).triggered.connect(slot)
        plus.setMenu(pm)
        self.mode_pill = PillButton()
        self.model_pill = PillButton()
        self.model_pill.menu().aboutToShow.connect(self._build_model_menu)
        self.send_btn = SendButton()
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(plus)
        row.addWidget(self.mode_pill)
        row.addWidget(self.model_pill, 0)
        row.addStretch(1)
        row.addWidget(self.send_btn)
        fl.addLayout(row)
        lay.addWidget(self.composer)
        self._set_mode("agent")
        return area

    # --- menus da área de digitação -------------------------------------------

    def _build_quick_menu(self, m):
        m.clear()
        has_sel = bool(self._selection())
        section = None
        for sec, ic, label, prompt, needs in QUICK_ACTIONS:
            if sec != section:
                section = sec
                menu_section(m, sec if (sec != "Na seleção" or has_sel) else "Na seleção · clique numa face antes")
            a = m.addAction(I.icon(ic, T["text2"], 14, hover=T["text"]), label)
            a.setEnabled(has_sel or not needs)
            a.triggered.connect(lambda _=False, p=prompt: self._prefill(p))

    def _build_mode_menu(self):
        m = self.mode_pill.menu()
        m.clear()
        g = QtGui.QActionGroup(m)
        for key, ic, label, hint in (("agent", "sparkle", "Agente", "modela direto na peça"),
                                     ("plan", "plan", "Planejar", "só conversa, não mexe na peça")):
            a = m.addAction(I.icon(ic, T["text2"], 14, hover=T["text"], on="check", on_color=T["text"]),
                            f"{label}\t{hint}")
            a.setCheckable(True)
            a.setChecked(key == self.mode)
            g.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self._set_mode(k))

    def _set_mode(self, key):
        self.mode = key
        plan = key == "plan"
        self.mode_pill.set_value("plan" if plan else "sparkle", "Planejar" if plan else "Agente",
                                 tip="Agente: modela direto. Planejar: só conversa e propõe, sem mexer na peça.")
        set_prop(self.composer, "plan", plan)
        self.input.setPlaceholderText(PLACEHOLDER[key])
        self._build_mode_menu()

    def _model_options(self, prov, cached=False):
        """[(rótulo, valor)] do provedor. cached=True nunca roda nada lento (menus)."""
        if cached and hasattr(prov, "cached_models"):
            return prov.cached_models()
        return prov.models()

    def _local_infos(self, prov):
        """Modelos locais com capacidades, do cache (nunca rede aqui)."""
        try:
            infos = prov.catalog() if hasattr(prov, "catalog") else []
        except Exception:
            infos = []
        ranked = []
        try:
            import chatcad_llm as L
            ranked = L.rank_models(infos)
        except Exception:
            pass
        names = {i["name"] for i in ranked}
        return ranked + sorted((i for i in infos if i.get("name") not in names), key=lambda i: i.get("name", ""))

    def _build_model_menu(self):
        m = self.model_pill.menu()
        m.clear()
        g = QtGui.QActionGroup(m)
        cur = (self.runner.prov.key, self._model_value())

        def add(prov, value, text, ic, hint="", enabled=True, tip=""):
            a = m.addAction(I.icon(ic, T["text2"], 14, hover=T["text"], on="check", on_color=T["text"]),
                            text + (f"\t{hint}" if hint else ""))
            a.setCheckable(True)
            a.setEnabled(enabled)
            if tip:
                a.setToolTip(tip)
            g.addAction(a)
            a.setChecked((prov.key, value) == cur)
            a.triggered.connect(lambda _=False, p=prov, v=value: self._choose_model(p, v))
            return a

        local = [p for p in PROVIDERS if _is_local(p)]
        if local:
            menu_section(m, "No seu computador · offline")
            for p in local:
                infos = self._local_infos(p)
                if not any(md.get("tools") for md in infos):
                    # sem modelo usável a IA local continua escolhível: o aviso dela diz o que fazer
                    try:
                        ollama = p.settings()["backend"] == "ollama"
                    except Exception:
                        ollama = True
                    add(p, "", f"{p.name} · conectar ao {'Ollama' if ollama else 'servidor'}", "cpu",
                        "nenhum modelo com ferramentas" if infos else "nenhum modelo encontrado",
                        tip="Escolha para ver como iniciar o servidor ou instalar um modelo.")
                for md in infos:
                    tools = bool(md.get("tools"))
                    hint = " · ".join(filter(None, [
                        "ferramentas" if tools else "sem ferramentas",
                        "visão" if md.get("vision") else "",
                        f'{md["ctx"] // 1000}k' if md.get("ctx") else ""]))
                    # o agente local sempre manda as ferramentas: modelo sem elas não funciona nem no Planejar
                    add(p, md["name"], md["name"], "cpu", hint, tools,
                        "" if tools else "Este modelo não usa ferramentas; o Chat CAD precisa delas.")
        menu_section(m, "Na nuvem · precisa de conta")
        for p in (p for p in PROVIDERS if not _is_local(p)):
            ok = self._exe(p) is not None
            for label, value in self._model_options(p, cached=True):
                add(p, value, f"{p.name} · {label}", "cloud", "" if ok else "não instalado", ok)
        m.addSeparator()
        m.addAction(I.icon("refresh", T["text2"], 14, hover=T["text"]),
                    "Atualizar lista de modelos").triggered.connect(self._refresh_local_models)

    def _exe(self, prov):
        """prov.exe() guardado (which + glob do WinGet levam ~50 ms cada a cada abertura do menu)."""
        if prov.key not in self._exe_cache:
            self._exe_cache[prov.key] = prov.exe()
        return self._exe_cache[prov.key]

    def _choose_model(self, prov, value):
        if prov is not self.runner.prov:
            App.ParamGet(PREFS).SetString("Model_" + prov.key, value or "")
            self._apply_provider(prov.key)
        self._model = value or ""
        App.ParamGet(PREFS).SetString("Model_" + prov.key, self._model)
        self.runner.prewarm(self._model)
        self._update_model_pill()
        self._set_busy(self.runner.busy())
        self._refresh_attachments()

    def _model_value(self):
        return self._model

    def _model_title(self):
        if _is_local(self.runner.prov):
            return self._model or "sem modelo"
        return self._labels.get(self._model) or self._model or "Padrão"

    def _update_model_pill(self):
        prov, local = self.runner.prov, _is_local(self.runner.prov)
        label = self._model_title()
        narrow = self.width() < NARROW
        text = label if (narrow or local) else f"{prov.name} · {label}"
        self.model_pill.set_value("cpu" if local else "cloud", text,
                                  T["brand"] if local else None, max_chars=18 if narrow else 26,
                                  tip=f"{prov.name}{' (no seu computador)' if local else ''} · {label}\n"
                                      "Clique para trocar de IA ou de modelo")

    def _local_info(self):
        prov = self.runner.prov
        if not _is_local(prov) or not self._model:
            return None
        try:
            return prov.info(self._model)
        except Exception:
            return None

    def _sync_send(self):
        busy = self._busy_state
        self.send_btn.set_busy(busy)
        self.send_btn.setEnabled(busy or bool(self.input.toPlainText().strip() or self._attachments))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < NARROW
        self.mode_pill.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly if narrow
                                          else QtCore.Qt.ToolButtonTextBesideIcon)
        self._update_model_pill()
        self._sync_gear()
        self._set_busy(getattr(self, "_busy_state", False))
        max_w = self._bubble_max()
        for b in self._bubbles:
            b.fit(max_w)

    def _sync_gear(self):
        # engrenagem só na IA local; no painel estreito ela vai para o menu "Mais opções"
        self.title.gear_btn.setVisible(_is_local(self.runner.prov) and self.width() >= NARROW)

    # --- ações ---------------------------------------------------------------

    def _prefill(self, text):
        self.input.setPlainText(text)
        self.input.setFocus()
        self.input.moveCursor(QtGui.QTextCursor.End)

    def _recheck(self):
        self._exe_cache.clear()                 # o usuário pode ter acabado de instalar o CLI
        self._online = None
        self._set_busy(self.runner.busy())
        self.auth.run(self.runner.prov)

    def _refresh_local_models(self):
        self._exe_cache.clear()
        local = next((p for p in PROVIDERS if _is_local(p)), None)
        if local is None:
            return
        if self.runner.prov is local:
            self._recheck()
            return
        from chatcad_local import Catalog                 # assíncrono; não mexe no status do atual
        if self._local_catalog is None or self._local_catalog.prov is not local:
            self._local_catalog = Catalog(local, self)
            self._local_catalog.ready.connect(self._on_local_catalog)
        self._local_catalog.refresh()

    def _on_local_catalog(self, ok, msg):
        if _is_local(self.runner.prov):         # trocou para a IA local no meio: o aviso dela cuida
            return
        if ok:
            self.banner.show_message("info", "Modelos locais atualizados", html.escape(msg), recheck=False)
            QtCore.QTimer.singleShot(4000, self, lambda: self.banner.kind == "info" and self.banner.hide())
        else:
            self.banner.show_message("warn", "Não consegui atualizar os modelos locais",
                                     self._local_problem(msg)[1], recheck=False)

    def _log_path(self):
        try:
            return os.path.join(App.getUserAppDataDir(), "chatcad_last.log")
        except Exception:
            return ""

    def _open_log(self):
        path = self._log_path()
        if path and os.path.exists(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _open_attachments_dir(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(attachments_dir()))

    # --- anexos -------------------------------------------------------------

    def _attach_qimage(self, img, tag):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(attachments_dir(), f"{stamp}-{tag}.png")
        n = 1
        while os.path.exists(path):
            path = os.path.join(attachments_dir(), f"{stamp}-{tag}-{n}.png")
            n += 1
        if img.isNull() or not img.save(path, "PNG"):    # sem arquivo, nada de anexo fantasma
            self._notice("Não consegui salvar a imagem na pasta de anexos.")
            return
        self._add_attachment(path)

    def _notice(self, msg):
        self.vision_warn.setText(msg)
        self.vision_warn.show()

    def _attach_files(self, paths):
        for p in paths:
            if p and os.path.isfile(p) and p.lower().endswith(IMG_EXT):
                self._add_attachment(os.path.normpath(p))

    def _add_attachment(self, path):
        if path not in self._attachments:
            self._attachments.append(path)
            self._refresh_attachments()

    def _refresh_attachments(self):
        while self.attach_row.count() > 1:
            w = self.attach_row.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for path in self._attachments:
            t = AttachThumb(path)
            t.removed.connect(self._remove_attachment)
            t.opened.connect(self._edit_attachment)
            self.attach_row.insertWidget(self.attach_row.count() - 1, t)
        self.attach_box.setVisible(bool(self._attachments))
        warn = ""
        info = self._local_info()
        if self._attachments and info is not None and not info.get("vision"):
            try:
                bridge = self.runner.prov.vision_model(self._model)
            except Exception:
                bridge = None
            if not bridge:
                warn = (f"{self._model} não enxerga imagens e não há modelo de visão instalado — "
                        "use um modelo da nuvem ou descreva em texto.")
        self.vision_warn.setText(warn)
        self.vision_warn.setVisible(bool(warn))
        self._sync_send()

    def _remove_attachment(self, path):
        if path in self._attachments:
            self._attachments.remove(path)
        self._refresh_attachments()

    def _sketch(self, image, title):
        if image is None or image.isNull():     # arquivo apagado, formato não suportado ou captura que falhou
            self._notice("Não consegui abrir a imagem para desenhar (o arquivo sumiu ou o formato "
                         "não é suportado).")
            return None
        dlg = SketchDialog(image, title, Gui.getMainWindow())
        try:                                    # filha da janela principal: sem isto vivia até fechar o FreeCAD
            return dlg.image() if dlg.exec() == QtWidgets.QDialog.Accepted else None
        finally:
            dlg.deleteLater()

    def _edit_attachment(self, path):
        img = self._sketch(QtGui.QImage(path), "Desenhar por cima")
        if img is not None and path in self._attachments:
            i = self._attachments.index(path)
            new = os.path.join(attachments_dir(), time.strftime("%Y%m%d-%H%M%S-rabisco.png"))
            if not img.save(new, "PNG"):
                self._notice("Não consegui salvar a imagem na pasta de anexos.")
                return
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

    def _paste_clipboard(self):
        md = QtWidgets.QApplication.clipboard().mimeData()
        img = QtGui.QImage(md.imageData()) if md is not None and md.hasImage() else QtGui.QImage()
        if not img.isNull():                    # botão "colar print": a imagem, mesmo com texto junto (Office)
            self._attach_qimage(img, "colado")
        elif md is not None and md.hasUrls():
            self.input.insertFromMimeData(md)
        else:
            self._notice("Não há imagem na área de transferência. Tire um print (Win+Shift+S) "
                         "e tente de novo.")

    # --- conversa ------------------------------------------------------------

    def new_chat(self):
        # o turno que estiver rodando morre aqui; o que ele ainda mandar até o próximo envio é eco
        # (send() zera _stopping)
        self._stopping = True
        self._send_when_idle = False
        self.runner.reset()
        while self.chat.count() > 1:
            item = self.chat.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._answer = self._group = self._thumb = self._think = None
        self._answers, self._bubbles = [], []
        self._turns = 0
        self.stack.setCurrentIndex(0)
        self._set_busy(False)

    def _send_or_stop(self):
        # decide pelo que a tela mostra: com a resposta pronta e o CLI ainda fechando, o botão já é
        # "Enviar" e o clique entra na fila (send()), nunca para o turno que terminou
        if self._busy_state:
            if self._stopping:                  # 2º clique / Esc repetido: já parou
                return
            self._stopping = True
            self.runner.stop()
            self._flush()
            if self._group is not None:
                self._think = None
                self._group.close_group(interrupted=True)
                self._group = None
            self._add(TurnFooter(f"Interrompido · {fmt_secs(time.time() - self._turn_started)}", ok=False),
                      space=6)
            self._set_busy(False)
        else:
            self.send()

    def _bubble_max(self):
        return max(160, int((self.width() - 32 - 10) * 0.85))     # margens da conversa + rolagem

    def send(self):
        text = self.input.toPlainText().strip()
        images = [p for p in self._attachments if os.path.isfile(p)]
        if not text and not images:
            return
        if self.runner.busy():
            if not self._busy_state:            # resposta pronta, o CLI ainda fechando: envia quando sair
                self._send_when_idle = True
                self.title.set_status("busy", "enviando assim que o anterior fechar…")
            return
        self._send_when_idle = False
        if not text:
            text = "Veja a imagem anexada e faça o que está marcado nela."
        self.input.clear()
        self._attachments = []
        self._refresh_attachments()
        self._dispatch(text, images)

    def _dispatch(self, text, images):
        """Manda um pedido já pronto (do campo ou de "Tentar de novo"); não mexe no rascunho."""
        self._last_request = (text, list(images))
        self.stack.setCurrentIndex(1)

        tags = [self._selection_label()] if (self._use_selection and self._selection()) else []
        bubble = UserBubble(text, tags, [QtGui.QPixmap(p) for p in images], self._bubble_max())
        self._bubbles.append(bubble)
        self._stick = True
        self._add(bubble, space=16 if self._turns else 0, align=QtCore.Qt.AlignRight)
        self._add(TurnHeader(self._turn_meta(), plan=self.mode == "plan"), space=10)
        self._turns += 1
        self._answer = self._group = self._thumb = self._think = None
        self._answers = []

        context = "" if _is_local(self.runner.prov) else self._context_text()
        self._stopping = False
        prompt = f"{context}\n\n{text}" if context else text
        doc = App.ActiveDocument
        cwd = os.path.dirname(doc.FileName) if doc and doc.FileName else os.path.expanduser("~")
        self._turn_started = time.time()
        self._stats_mark = len(self.runner.turn_stats())
        self.runner.send(prompt, model=self._model, plan=self.mode == "plan", cwd=cwd, images=images,
                         use_selection=self._use_selection)        # a IA local monta o próprio contexto
        self._set_busy(self.runner.busy())

    def _retry(self, request):
        """Reenvia o pedido que falhou (guardado no aviso), sem tocar no rascunho nem nos anexos."""
        if not request or self._busy_state:
            return
        text, images = request
        images = [p for p in images if os.path.isfile(p)]
        if self.runner.busy():                  # resposta pronta, o CLI ainda fechando: vai na fila
            self._send_when_idle = (text, images)
            self.title.set_status("busy", "enviando assim que o anterior fechar…")
            return
        self._send_when_idle = False
        self._dispatch(text, images)

    def _turn_meta(self):
        return f"{self.runner.prov.name} · {self._model_title()}"

    def _set_busy(self, busy):
        prov, local = self.runner.prov, _is_local(self.runner.prov)
        if busy:
            state, word = "busy", "trabalhando…"
        elif self._online is None:
            state, word = "idle", "verificando…"
        elif not self._online:
            state, word = "off", "desligado" if local else "desconectado"
        else:
            state, word = "ok", "pronto"
        tip = self._auth_msg if (self._auth_msg and not busy) else None
        full = f"{prov.name} · {self._model_title()} · {word}"
        short = f"{word} · {self._model_title()}"            # painel estreito: o estado vem antes e não some
        self.title.set_status(state, short if self.width() < NARROW else full, tip or full)
        self._busy_state = bool(busy)
        self._sync_send()

    def _on_idle(self):
        """O processo do CLI saiu depois da resposta: o botão volta a Enviar (e sai o que ficou na fila)."""
        self._set_busy(self.runner.busy())
        if self._send_when_idle and not self.runner.busy():
            queued, self._send_when_idle = self._send_when_idle, False
            if isinstance(queued, tuple):       # "Tentar de novo" na fila: o pedido guardado
                self._retry(queued)
            else:
                self.send()

    # --- seleção do FreeCAD --------------------------------------------------

    def _selection(self):
        try:
            return Gui.Selection.getSelectionEx("", 0)
        except Exception:
            return []

    def _selection_text(self, sel=None):
        parts = []
        for s in (sel if sel is not None else self._selection()):
            subs = [n.split(".")[-1] for n in s.SubElementNames if n]
            label = s.Object.Label
            parts.append(f"{label} › {', '.join(subs[:3])}" if subs else label)
        txt = " · ".join(parts)
        return txt if len(txt) <= 44 else txt[:43] + "…"

    def _selection_label(self):
        return self._selection_text()

    def _refresh_selection(self):
        sel = self._selection()
        if not sel:
            self.sel_chip.hide()
            self._use_selection = True
            return
        self.sel_chip.set_selection(self._selection_text(sel), self._use_selection)
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

    def _add(self, w, space=0, align=None):
        if space:
            self.chat.insertSpacing(self.chat.count() - 1, space)
        if align is not None:
            self.chat.insertWidget(self.chat.count() - 1, w, 0, align)
        else:
            self.chat.insertWidget(self.chat.count() - 1, w)
        return w

    def _ensure_group(self):
        if self._group is None:
            self._flush()
            self._answer = None
            self._group = self._add(StepsGroup(), space=6)
            self._thumb = None
        return self._group

    def _close_group(self):
        if self._group is not None:
            self._end_thinking()
            self._group.close_group()
            self._group = None

    def _flush(self):
        if self._answer is not None:
            self._answer.render()

    def _on_thinking(self, chunk):
        if self._stopping:
            return
        if self._think is None:
            self._think = self._ensure_group().add(f"__think{time.time()}", "bulb", "Raciocinando",
                                                   "O que o modelo pensou antes de agir", kind="thinking")
        self._think.append_detail(chunk)

    def _end_thinking(self):
        if self._think is not None:
            if self._think.state == "run":
                self._think.finish(title=f"Pensou por {fmt_secs(time.time() - self._think.t0)}")
            self._think = None

    def _on_break(self):
        self._flush()
        self._answer = None

    def _on_text(self, chunk):
        if self._stopping or not chunk:
            return
        self._close_group()                            # ordem cronológica: texto fecha o grupo
        if self._answer is None:
            self._answer = self._add(AnswerView(), space=6)
            self._answers.append(self._answer)
        self._answer.add_text(chunk)

    def _on_tool(self, tool_id, name, inp):
        if self._stopping or name.split("__")[-1] in HIDDEN_TOOLS:
            return
        self._end_thinking()
        ic, title, tip, hint, detail = describe_tool(name, inp)
        short = name.split("__")[-1]
        kind = "thinking" if short == "thinking" else "tool"
        row = self._ensure_group().add(tool_id, ic, title, tip, hint, detail, kind)
        if short == "thinking":
            row.setToolTip("O modelo está pensando antes de responder")

    def _on_tool_done(self, tool_id, error, images, output=""):
        if self._stopping:
            return
        if self._group is not None:
            row = self._group.rows.get(tool_id)
            title = None
            if row is not None and row.kind == "thinking" and not error:
                title = f"Pensou por {fmt_secs(time.time() - row.t0)}"
            self._group.finish_step(tool_id, error, output, title)
        if images:                                     # 1 miniatura por grupo, atualizada no lugar
            if self._thumb is None:
                self._thumb = self._add(ImageThumb(images[-1]), space=6)
            else:
                self._thumb.set_image(images[-1])

    def _turn_stats(self):
        stats = self.runner.turn_stats()[self._stats_mark:]
        tok = sum(int((s[1] or {}).get("output_tokens") or 0) for s in stats)
        secs = sum(float((s[1] or {}).get("output_s") or 0) for s in stats)
        last = stats[-1][1] if stats else {}
        used = int(last.get("prompt_tokens") or 0) + int(last.get("output_tokens") or 0)
        return (tok / secs if tok and secs > 0 else None), used

    def _on_finished(self, result):
        if self._stopping:              # turno parado: já mostramos "Interrompido"; o resto até o
            self._set_busy(self.runner.busy())          # próximo envio é eco do processo morto
            return
        self._flush()
        self._close_group()
        self._set_busy(False)
        secs = time.time() - self._turn_started
        if result.get("is_error"):
            msg = (result.get("result") or "").strip() or \
                f"{self.runner.prov.name} terminou com erro sem dar detalhes. Veja o log do último pedido."
            self._error_callout(msg)
            if "login" in msg.lower():
                self.auth.run(self.runner.prov)
            return
        md = "\n\n".join(a.source_md for a in self._answers if a.source_md.strip())
        tok_s, used = self._turn_stats()
        extra = (" · " + f"{tok_s:.1f}".replace(".", ",") + " tok/s") if tok_s else ""
        doc = App.ActiveDocument
        has_params = False
        try:
            has_params = bool(doc and doc.getObject("Parametros"))
        except Exception:
            pass
        self._add(TurnFooter(f"Concluído em {fmt_secs(secs)} · {self._model_title()}{extra}",
                             on_copy=(lambda: QtWidgets.QApplication.clipboard().setText(md)) if md else None,
                             on_params=self._open_params if has_params else None), space=8)
        if _is_local(self.runner.prov) and used:
            try:
                ctx = int(self.runner.prov.settings().get("num_ctx") or 0)
            except Exception:
                ctx = 0
            if ctx and used >= 0.8 * ctx:
                self._add(Callout("info", "Conversa longa — comece uma nova para o modelo local não se perder.",
                                  [("Nova conversa", self.new_chat)]), space=8)
        try:
            if doc:
                doc.recompute()
                Gui.updateGui()
        except Exception:
            pass

    def _open_params(self):
        try:
            Gui.ActiveDocument.setEdit("Parametros")
        except Exception:
            pass

    def _error_callout(self, msg):
        # cada aviso guarda o pedido que falhou: "Tentar de novo" num aviso antigo não manda o último
        actions = [("Tentar de novo", lambda _=False, r=self._last_request: self._retry(r))]
        if not _is_local(self.runner.prov) and os.path.exists(self._log_path()):
            actions.append(("Ver log", self._open_log))
        self._add(Callout("error", msg, actions), space=8)

    def _on_failed(self, msg):
        if self._stopping:
            self._set_busy(self.runner.busy())
            return
        self._flush()
        self._close_group()
        self._set_busy(False)
        if self.stack.currentIndex() != 1:
            self.stack.setCurrentIndex(1)
        self._error_callout(msg)
        if _is_local(self.runner.prov):
            self.auth.run(self.runner.prov)

    # --- conexão com o provedor ----------------------------------------------

    def _on_auth(self, ok, msg):
        prov = self.runner.prov
        local = _is_local(prov)
        if local:                       # a lista de modelos vem do servidor: atualiza o seletor
            self._fill_models(prov)
        self._online = bool(ok)
        self._auth_msg = msg or ""
        if ok:
            self.banner.hide()
            if local:
                self.runner.prewarm(self._model)
        elif local:
            title, body = self._local_problem(msg)
            self.banner.show_message("warn", title, body, prov.connect_label)
        elif prov.exe() is None:
            self.banner.show_message("error", f"{prov.name} não está instalado",
                                     html.escape(msg or "") + " Instale o CLI e clique em Verificar de novo.")
        else:
            self.banner.show_message("warn", f"{prov.name} não está conectado",
                                     html.escape(f"Clique em Conectar. {prov.login_hint} "
                                                 "Depois clique em Verificar de novo."),
                                     getattr(prov, "login_label", "Conectar"))
        self._set_busy(self.runner.busy())

    def _local_problem(self, msg):
        """(título, html) do aviso da IA local; o erro técnico fica na dica do ponto de status."""
        local = next((p for p in PROVIDERS if _is_local(p)), None)
        try:
            ollama = local.settings()["backend"] == "ollama"
        except Exception:
            ollama = True
        if "não respondeu" in (msg or ""):
            if ollama:
                private = local is not None and _local_host(local) is None
                return ("O Ollama não está respondendo",
                        "Abra o app do Ollama ou rode <code>ollama serve</code>."
                        + (" Nada sai do seu computador." if private else ""))
            return ("O servidor de IA local não está respondendo",
                    "Abra o servidor (LM Studio, llama.cpp…) e confira o endereço em Configurar.")
        return "A IA local não está pronta", html.escape(msg or "")

    def _open_settings(self):
        from chatcad_settings import LocalSettingsDialog
        prov = self.runner.prov if _is_local(self.runner.prov) else next(
            (p for p in PROVIDERS if _is_local(p)), None)
        if prov is None:
            return
        dlg = LocalSettingsDialog(prov, Gui.getMainWindow())
        try:
            accepted = dlg.exec() == QtWidgets.QDialog.Accepted
        finally:
            dlg.deleteLater()
        if accepted:
            self._sync_footnote()        # o endereço pode ter saído (ou voltado) deste computador
        if accepted and prov is self.runner.prov:
            if self._busy_state:
                self._send_or_stop()     # para como o botão Parar (grupo fecha, "Interrompido")
            self.new_chat()              # servidor/modelo podem ter mudado: o agente esquece a conversa
            self._recheck()

    def _open_login(self):
        prov = self.runner.prov
        if _is_local(prov):
            if prov.connect_label == "Configurar":
                self._open_settings()
                return
            prov.start_server()          # abre o Ollama e confere de novo em alguns segundos
            for ms in (3000, 8000, 15000):
                QtCore.QTimer.singleShot(ms, lambda p=prov: self.auth.run(p) if self.runner.prov is p else None)
            self.banner.show_message("info", "Abrindo o Ollama…",
                                     "Vou conferir de novo em alguns segundos."
                                     + (" Nada sai do seu computador." if _local_host(prov) is None else ""))
            return
        cmd = prov.login_cmd()
        if not (cmd and cmd[0]):
            return
        import shlex
        title = "Login - " + prov.name
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
            self, title, f"Abra um terminal e rode:\n\n{line}\n\nDepois clique em Verificar de novo no painel.")

    def _apply_provider(self, key, initial=False):
        prov = provider(key)
        if not initial and prov is self.runner.prov:
            return
        if not initial:
            self.new_chat()
        self.runner.set_provider(prov)
        App.ParamGet(PREFS).SetString("Provider", prov.key)
        self._sync_gear()
        self._sync_footnote()
        self._fill_models(prov)
        self._online, self._auth_msg = None, ""
        self.banner.hide()
        self._set_busy(False)
        self._refresh_attachments()
        self.auth.run(prov)

    def _sync_footnote(self):
        prov = self.runner.prov
        if not _is_local(prov):
            text = "Enter envia · Shift+Enter quebra linha · Ctrl+V cola um print"
        else:
            host = _local_host(prov)
            text = ("Modelo local: nada sai do seu computador. Pode ser mais lento no notebook." if host is None
                    else f"IA em {host}: os pedidos, os dados do documento e as imagens vão para esse servidor.")
        self.footnote.setText(text)

    def _fill_models(self, prov):
        fetch = getattr(prov, "fetch_models", None)     # Antigravity: "agy models" leva até 20 s
        if fetch is not None:
            fetch(self, lambda p=prov: self._fill_models(p) if self.runner.prov is p else None)
        opts = self._model_options(prov, cached=True)
        self._labels = {v: label for label, v in opts}        # sem ler arquivos a cada resize
        values = [v for _l, v in opts]
        saved = App.ParamGet(PREFS).GetString("Model_" + prov.key, "")
        if saved in values:
            self._model = saved
        elif self._model in values and prov is self.runner.prov:
            pass
        else:
            self._model = values[0] if values else ""
        self._update_model_pill()


# ---------------------------------------------------------------------------

def autostart(attempt=1):
    """Chamado pelo InitGui no início do FreeCAD. Se a janela principal ainda não estiver pronta,
    tenta de novo algumas vezes; qualquer falha vai para chatcad_startup.log (nunca em silêncio)."""
    import traceback
    log = os.path.join(App.getUserAppDataDir(), "chatcad_startup.log")
    try:
        mw = Gui.getMainWindow()
        if mw is None or not mw.isVisible():
            raise RuntimeError("a janela principal do FreeCAD ainda não está pronta")
        install(mw)
        if mw.findChild(QtWidgets.QDockWidget, DOCK_NAME) is None:
            raise RuntimeError("o painel não ficou na janela principal")
    except Exception as e:
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"tentativa {attempt}: {e}\n{traceback.format_exc()}\n")
        except OSError:
            pass
        if attempt < 5:
            QtCore.QTimer.singleShot(2000 * attempt, lambda: autostart(attempt + 1))
        else:
            App.Console.PrintError(f"Chat CAD: falha ao abrir o painel: {e} (detalhes em {log})\n")


def install(mw=None):
    mw = mw or Gui.getMainWindow()
    old = mw.findChild(QtWidgets.QDockWidget, DOCK_NAME)
    if old:
        return old
    TH.tokens()
    dock = QtWidgets.QDockWidget("Chat CAD", mw)            # windowTitle ainda nomeia o item em Exibir › Painéis
    dock.setObjectName(DOCK_NAME)
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable | QtWidgets.QDockWidget.DockWidgetMovable
                     | QtWidgets.QDockWidget.DockWidgetFloatable)
    dock.setStyleSheet(TH.load_qss())                       # 1 folha só: barra + painel + menus + dicas
    dock.setFont(TH.ui_font(13))
    panel = ChatPanel(dock)
    dock.setWidget(panel)
    title = panel.title
    if App.ParamGet(PREFS).GetBool("NativeTitleBar", False):   # fallback se o Overlay do FreeCAD brigar
        title.set_native(True)
        panel.layout().insertWidget(0, title)
    else:
        title.bind(dock)
        dock.setTitleBarWidget(title)

        def keep(*_):
            if dock.titleBarWidget() is not title:
                dock.setTitleBarWidget(title)
        dock.topLevelChanged.connect(keep)
        dock.dockLocationChanged.connect(keep)
        QtCore.QTimer.singleShot(0, keep)
        QtCore.QTimer.singleShot(2000, keep)
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
    toggle.triggered.connect(lambda on: on and QtCore.QTimer.singleShot(0, panel.input.setFocus))
    mw.addAction(toggle)
    dock.show()
    return dock
