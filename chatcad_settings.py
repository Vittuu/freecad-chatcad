"""Janela de configurações da IA local (Ollama ou servidor compatível com OpenAI)."""

try:
    from PySide import QtCore, QtGui, QtWidgets
except ImportError:                       # testes fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtWidgets

import chatcad_icons as I
import chatcad_llm as L
import chatcad_theme as TH
from chatcad_local import Catalog, _base, _is_loopback
from chatcad_theme import T, set_prop

BACKENDS = [("Ollama", "ollama"), ("Compatível com OpenAI (LM Studio, llama.cpp…)", "openai")]
DEFAULT_URLS = {"ollama": "http://127.0.0.1:11434", "openai": "http://127.0.0.1:1234/v1"}
THINK = [("Desligado (mais rápido)", "off"), ("Só no modo Planejar", "auto"), ("Sempre", "on")]
KEEP = [("30 minutos (respostas seguintes mais rápidas)", "30m"), ("5 minutos", "5m"),
        ("1 hora", "1h")]


class Combo(QtWidgets.QComboBox):
    """QComboBox com a seta pintada (o QSS esconde ::down-arrow; sem a folha do FreeCAD não haveria seta)."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtGui.QPainter(self)
        p.drawPixmap(self.width() - 20, (self.height() - 12) // 2,
                     I.pixmap("chevron-down", T["text3"] if self.isEnabled() else T["text4"], 12))
        p.end()


def privacy_note(base_url):
    """Só promete "nada sai da máquina" com o servidor no próprio computador (loopback)."""
    url = _base({"base_url": base_url or ""})
    if _is_loopback(url):
        return ("Os modelos rodam no seu computador: nada sai da máquina. No processador (sem placa de "
                "vídeo), conte com 1 a 2 minutos por passo.")
    host = QtCore.QUrl(url).host() or url
    return (f"Atenção: o servidor {host} não é este computador. Os pedidos, os dados do documento "
            "(nomes, parâmetros) e as imagens vão para ele.")


def _section(text):
    lab = QtWidgets.QLabel(text.upper())
    lab.setObjectName("ccSection")
    return lab


def _hair():
    f = QtWidgets.QFrame()
    f.setObjectName("ccHair")
    return f


def _form():
    form = QtWidgets.QFormLayout()
    form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    return form


class LocalSettingsDialog(QtWidgets.QDialog):
    """Edita as preferências de User parameter:Plugins/ChatCAD/Local. "Testar conexão" roda o
    mesmo Catalog do painel e mostra o resultado na própria janela."""

    LABEL_W = 132

    def __init__(self, prov, parent=None):
        super().__init__(parent)
        TH.ensure()
        self.prov = prov
        self.setObjectName("ccSettings")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        self.setStyleSheet(TH.load_qss())          # filho da janela principal: não herda a folha do dock
        self.setFont(TH.ui_font(13))
        self.setWindowTitle("Chat CAD — IA local")
        self.setMinimumWidth(540)
        cfg = prov.settings()

        # cabeçalho ------------------------------------------------------------------
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        tile = QtWidgets.QLabel()
        tile.setObjectName("ccTile")
        tile.setAlignment(QtCore.Qt.AlignCenter)
        tile.setPixmap(I.pixmap("cpu", T["brand"], 16))
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        title = QtWidgets.QLabel("IA local")
        title.setObjectName("ccDlgTitle")
        self.privacy = hint = QtWidgets.QLabel(privacy_note(cfg["base_url"]))
        hint.setObjectName("ccHint")
        hint.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(hint)
        head.addWidget(tile, 0, QtCore.Qt.AlignTop)
        head.addLayout(col, 1)

        # servidor ---------------------------------------------------------------------
        server = _form()
        self.backend = Combo()
        for label, key in BACKENDS:
            self.backend.addItem(label, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(cfg["backend"])))
        self.backend.currentIndexChanged.connect(self._backend_changed)
        server.addRow(self._label("Servidor"), self.backend)

        self.url = QtWidgets.QLineEdit(cfg["base_url"])
        self.url.setPlaceholderText(DEFAULT_URLS["ollama"])
        self.url.textChanged.connect(self._sync_privacy)
        server.addRow(self._label("Endereço"), self.url)

        self.key = QtWidgets.QLineEdit(cfg["api_key"])
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setPlaceholderText("opcional (só se o servidor pedir)")
        self.key_label = self._label("Chave")
        server.addRow(self.key_label, self.key)

        self.test_btn = QtWidgets.QPushButton("Testar conexão")
        self.test_btn.setObjectName("ccBtn")
        self.test_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.test_btn.setIcon(I.icon("refresh", T["text2"], 14))
        self.test_btn.setIconSize(QtCore.QSize(14, 14))
        self.test_btn.setAutoDefault(False)
        self.test_btn.clicked.connect(self._test)
        self.test_icon = QtWidgets.QLabel()
        self.test_icon.setFixedSize(14, 14)
        self.test_icon.hide()
        self.test_out = QtWidgets.QLabel("")
        self.test_out.setObjectName("ccTestOut")
        self.test_out.setWordWrap(True)
        test_row = QtWidgets.QHBoxLayout()
        test_row.setSpacing(8)
        test_row.addWidget(self.test_btn, 0, QtCore.Qt.AlignTop)
        test_row.addWidget(self.test_icon, 0, QtCore.Qt.AlignVCenter)
        test_row.addWidget(self.test_out, 1)
        server.addRow(self._label(""), test_row)

        # modelo -----------------------------------------------------------------------
        model = _form()
        self.vision = Combo()
        self.vision.setToolTip("Quando o modelo principal não vê imagens, este modelo descreve as imagens "
                               "anexadas em texto antes (ex.: gemma3:4b).")
        model.addRow(self._label("Modelo de visão"), self.vision)

        self.openai_vision = QtWidgets.QCheckBox("O modelo escolhido vê imagens")
        self.openai_vision.setChecked(bool(cfg["openai_vision"]))
        model.addRow(self._label(""), self.openai_vision)

        self.think = Combo()
        for label, key in THINK:
            self.think.addItem(label, key)
        self.think.setCurrentIndex(max(0, self.think.findData(cfg["think"])))
        self.think.setToolTip("Raciocínio deixa o modelo mais cuidadoso, mas no processador do notebook "
                              "pode levar minutos por passo.")
        model.addRow(self._label("Raciocínio"), self.think)

        self.ctx = Combo()
        for n in (4096, 8192, 16384, 32768):
            self.ctx.addItem(f"{n // 1024}k tokens", n)
        self.ctx.setCurrentIndex(max(0, self.ctx.findData(int(cfg["num_ctx"]))))
        self.ctx.setToolTip("Memória de conversa do modelo. 8k é o equilíbrio no CPU; mais contexto usa mais RAM.")
        model.addRow(self._label("Contexto"), self.ctx)

        self.steps = QtWidgets.QSpinBox()
        self.steps.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)    # setas nativas destoam do tema
        self.steps.setToolTip("Digite de 2 a 30 (ou use a roda do mouse / setas do teclado).")
        self.steps.setRange(2, 30)
        self.steps.setValue(int(cfg["max_steps"]))
        self.steps.setSuffix(" passos por pedido")
        model.addRow(self._label("Limite"), self.steps)

        # memória e segurança ------------------------------------------------------------
        mem = _form()
        self.keep = Combo()
        for label, key in KEEP:
            self.keep.addItem(label, key)
        idx = self.keep.findData(cfg["keep_alive"])
        if idx < 0:
            self.keep.addItem(str(cfg["keep_alive"]), cfg["keep_alive"])
            idx = self.keep.count() - 1
        self.keep.setCurrentIndex(idx)
        mem.addRow(self._label("Manter na memória"), self.keep)

        self.unload = QtWidgets.QCheckBox("Liberar a memória ao terminar cada resposta")
        self.unload.setChecked(bool(cfg["unload_after"]))
        self.unload.setToolTip("Devolve a RAM ao sistema entre respostas. O próximo pedido demora mais "
                               "para começar (o modelo é carregado de novo).")
        mem.addRow(self._label(""), self.unload)

        self.backup = QtWidgets.QCheckBox("Fazer backup do arquivo antes de cada mudança")
        self.backup.setChecked(bool(cfg["backup"]))
        self.autosave = QtWidgets.QCheckBox("Salvar o documento ao fim de cada resposta")
        self.autosave.setChecked(bool(cfg["autosave"]))
        mem.addRow(self._label("Segurança"), self.backup)
        mem.addRow(self._label(""), self.autosave)

        # botões -------------------------------------------------------------------------
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        save = buttons.button(QtWidgets.QDialogButtonBox.Save)
        save.setText("Salvar")
        save.setObjectName("ccPrimary")
        save.setCursor(QtCore.Qt.PointingHandCursor)
        cancel = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        cancel.setText("Cancelar")
        cancel.setObjectName("ccSecondary")
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addLayout(head)
        lay.addSpacing(4)
        for name, form in (("Servidor", server), ("Modelo", model), ("Memória e segurança", mem)):
            lay.addWidget(_hair())
            lay.addWidget(_section(name))
            lay.addLayout(form)
        lay.addSpacing(6)
        lay.addWidget(_hair())
        lay.addWidget(buttons)
        self._server_form, self._model_form = server, model

        self._catalog = None
        self._fill_vision(cfg)
        self._backend_changed(initial=True)
        # ao abrir, o foco fica na janela (anel de foco só navegando com Tab)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def showEvent(self, e):
        super().showEvent(e)
        QtCore.QTimer.singleShot(0, self, lambda: self.setFocus(QtCore.Qt.OtherFocusReason))

    def _label(self, text):
        lab = QtWidgets.QLabel(text)
        lab.setMinimumWidth(self.LABEL_W)
        lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return lab

    # ----------------------------------------------------------------------------
    def _fill_vision(self, cfg, catalog=None):
        self.vision.clear()
        self.vision.addItem("Automático (usa um modelo de visão instalado)", "auto")
        self.vision.addItem("Nenhum", "")
        for info in (self.prov.catalog() if catalog is None else catalog):
            if info.get("vision"):
                self.vision.addItem(info["name"], info["name"])
        idx = self.vision.findData(cfg["vision_model"])
        if idx < 0 and cfg["vision_model"]:
            self.vision.addItem(cfg["vision_model"], cfg["vision_model"])
            idx = self.vision.count() - 1
        self.vision.setCurrentIndex(max(0, idx))

    def _sync_privacy(self, *_):
        self.privacy.setText(privacy_note(self.url.text().strip() or DEFAULT_URLS[self.backend.currentData()]))

    def _backend_changed(self, *_, initial=False):
        key = self.backend.currentData()
        ollama = key == "ollama"
        if not initial:
            self.url.setText(DEFAULT_URLS[key])
        self.url.setPlaceholderText(DEFAULT_URLS[key])
        self._server_form.setRowVisible(self.key, not ollama)          # some a linha inteira, sem buraco
        self._model_form.setRowVisible(self.openai_vision, not ollama)
        for w in (self.vision, self.keep, self.unload):
            w.setEnabled(ollama)

    def _values(self):
        return {
            "backend": self.backend.currentData(),
            "base_url": self.url.text().strip() or DEFAULT_URLS[self.backend.currentData()],
            "api_key": self.key.text().strip(),
            "vision_model": self.vision.currentData(),
            "openai_vision": self.openai_vision.isChecked(),
            "think": self.think.currentData(),
            "num_ctx": int(self.ctx.currentData()),
            "max_steps": int(self.steps.value()),
            "keep_alive": self.keep.currentData(),
            "unload_after": self.unload.isChecked(),
            "backup": self.backup.isChecked(),
            "autosave": self.autosave.isChecked(),
        }

    def _test(self):
        """Testa com os valores da tela (sem salvar), usando um provedor temporário em memória."""
        from chatcad_local import LocalProvider, Settings
        tmp = LocalProvider(Settings(dict(self.prov.settings(), **self._values())))
        self._catalog = Catalog(tmp, self)
        self._catalog.ready.connect(self._tested)
        self.test_btn.setEnabled(False)
        self.show_result("", "Testando…")
        self._tmp = tmp
        self._catalog.refresh()

    def show_result(self, state, msg):
        """state: "" (neutro) | ok | error."""
        set_prop(self.test_out, "state", state)
        self.test_out.setText(msg)
        if state:
            ok = state == "ok"
            self.test_icon.setPixmap(I.pixmap("check-circle" if ok else "x-circle",
                                              T["success"] if ok else T["danger"], 14))
        self.test_icon.setVisible(bool(state))
        QtCore.QTimer.singleShot(0, self._grow)

    def _grow(self):
        lay = self.layout()
        need = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        if self.isVisible() and self.height() < need:
            self.resize(self.width(), need)

    def _tested(self, ok, msg):
        self.test_btn.setEnabled(True)
        self.test_out.setToolTip("" if ok else msg)              # o erro técnico fica na dica
        if ok:
            names = [i["name"] for i in L.rank_models(self._tmp.catalog())][:4]
            msg += (": " + ", ".join(names)) if names else ""
        elif "não respondeu" in msg:
            msg = (f"Não consegui falar com {self._tmp.settings()['base_url']}. Confira se o servidor "
                   "está aberto e se o endereço está certo.")
        self.show_result("ok" if ok else "error", msg)
        if ok:                                                   # modelos de visão do servidor testado
            vision = self.vision.currentData()
            self._fill_vision(dict(self._tmp.settings(), vision_model=vision), self._tmp.catalog())

    def _save(self):
        values = self._values()
        if self._catalog is not None and getattr(self, "_tmp", None) is not None:
            values["catalog"] = self._tmp.settings()["catalog"]     # aproveita a lista testada
        self.prov.store.save(**values)
        self.accept()
