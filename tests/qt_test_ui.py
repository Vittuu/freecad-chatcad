"""Testes offscreen da interface do Chat CAD (tema, ícones, markdown, passos, painel inteiro).
Não abre o FreeCAD nem fala com o Ollama/CLIs: o FreeCADGui é falso, a verificação de conexão,
o pré-aquecimento e o envio são trocados por versões de mentira. A pasta de usuário do FreeCAD
é sempre uma pasta temporária nova.
Rodar com o Python do FreeCAD (tem PySide6 e o módulo FreeCAD):
  set QT_QPA_PLATFORM=offscreen
  "<FreeCAD>/bin/python.exe" -I tests/qt_test_ui.py
Opcional: CHATCAD_SHOTS=<pasta> salva capturas de tela (README / revisão visual).
"""
import json
import os
import sys
import tempfile
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
_ext = os.path.normpath(os.path.join(os.path.dirname(sys.executable), "..", "Ext"))
if os.path.isdir(_ext):                       # shim "PySide" do FreeCAD (o painel importa por ele)
    sys.path.append(_ext)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":                   # o "offscreen" não acha as fontes do sistema sozinho
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
_home = tempfile.mkdtemp(prefix="chatcad_ui_")      # nunca a pasta de usuário de verdade
for _k in ("FREECAD_USER_HOME", "FREECAD_USER_DATA", "FREECAD_USER_TEMP"):
    os.environ[_k] = _home

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402

QT_WARNINGS = []


def _qt_msg(mode, ctx, msg):
    QT_WARNINGS.append(msg)


QtCore.qInstallMessageHandler(_qt_msg)
APP = QtWidgets.QApplication(sys.argv)
APP.setStyle("Fusion")
DARK = os.environ.get("CHATCAD_THEME", "dark") != "light"
if DARK:                                      # paleta escura, como o tema "FreeCAD Dark"
    pal = QtGui.QPalette()
    for role, col in ((QtGui.QPalette.Window, "#2a2a2a"), (QtGui.QPalette.WindowText, "#e0e0e0"),
                      (QtGui.QPalette.Base, "#1e1e1e"), (QtGui.QPalette.Text, "#e0e0e0"),
                      (QtGui.QPalette.Button, "#333333"), (QtGui.QPalette.ButtonText, "#e0e0e0")):
        pal.setColor(role, QtGui.QColor(col))
    APP.setPalette(pal)

import FreeCAD as App  # noqa: E402

# ---------------------------------------------------------------------------
# FreeCADGui falso
# ---------------------------------------------------------------------------


class _Pt:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _SelObj:
    def __init__(self, name, label, subs):
        self.ObjectName = name
        self.Object = types.SimpleNamespace(Label=label, Name=name)
        self.SubElementNames = subs
        self.PickedPoints = [_Pt(1, 2, 3)]


class _Selection:
    items = []

    def getSelectionEx(self, *_a):
        return list(self.items)


MW = QtWidgets.QMainWindow()
_view = QtWidgets.QLabel("vista 3D")
_view.setAlignment(QtCore.Qt.AlignCenter)
_view.setStyleSheet("background: #3b4250; color: #8a93a3;")
MW.setCentralWidget(_view)
MW.resize(1280, 820)

Gui = types.ModuleType("FreeCADGui")
Gui.Selection = _Selection()
Gui.ActiveDocument = None
Gui.getMainWindow = lambda: MW
Gui.updateGui = lambda: None
sys.modules["FreeCADGui"] = Gui

import chatcad_backend as B  # noqa: E402
import chatcad_icons as I  # noqa: E402
import chatcad_theme as TH  # noqa: E402
import chatcad_widgets as W  # noqa: E402

RESULTS = []
ERRORS = []


def check(name, cond, info=""):
    RESULTS.append((name, bool(cond), info))


def _hook(*exc):
    ERRORS.append("".join(traceback.format_exception(*exc)))


sys.excepthook = _hook

# nada de rede, processos ou modelos carregados ---------------------------------------
AUTH = {"ok": True, "msg": ""}
PREWARM = []


def _fake_auth(self, prov):
    self.prov = prov
    QtCore.QTimer.singleShot(0, lambda: self.finished.emit(AUTH["ok"], AUTH["msg"]))


B.AuthCheck.run = _fake_auth
B.ClaudeRunner.prewarm = lambda self, model="": PREWARM.append(model)
B.AntigravityProvider._models = [("Padrão", ""), ("Gemini 3 Pro", "gemini-3-pro")]
LOCAL = next((p for p in B.PROVIDERS if getattr(p, "in_process", False)), None)
CATALOG = [
    {"name": "qwen3:8b", "tools": True, "vision": False, "thinking": [False, True], "ctx": 40960,
     "size": "8.2B", "family": "qwen3"},
    {"name": "qwen2.5vl:7b", "tools": True, "vision": True, "thinking": [], "ctx": 32768,
     "size": "8.3B", "family": "qwen25vl"},
    {"name": "gemma3:4b", "tools": False, "vision": True, "thinking": [], "ctx": 131072,
     "size": "4.3B", "family": "gemma3"},
]
if LOCAL is not None:
    LOCAL.store.save(catalog=json.dumps(CATALOG), vision_model="")

import chatcad_panel as P  # noqa: E402

SHOTS = os.environ.get("CHATCAD_SHOTS")


def pump(ms=0):
    end = QtCore.QDeadlineTimer(ms)
    while True:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 20)
        if end.hasExpired():
            break
        QtTest.QTest.qWait(10)


def shot(widget, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    pump(60)
    img = widget.grab().toImage()
    if img.hasAlphaChannel():                        # menus translúcidos: fundo do painel por trás
        bg = QtGui.QImage(img.size(), QtGui.QImage.Format_ARGB32)
        bg.setDevicePixelRatio(img.devicePixelRatio())
        bg.fill(QtGui.QColor(TH.T["bg"]))
        p = QtGui.QPainter(bg)
        p.drawImage(0, 0, img)
        p.end()
        img = bg
    img.save(os.path.join(SHOTS, name + ".png"))


def png_bytes(w=320, h=200, color="#5a6a8a"):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(color))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor("#c9ced8"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawRoundedRect(QtCore.QRectF(w * 0.25, h * 0.3, w * 0.5, h * 0.4), 10, 10)
    p.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# ---------------------------------------------------------------------------
# tema, ícones, peças
# ---------------------------------------------------------------------------

def test_theme():
    t = TH.tokens()
    check("theme_dark_detected", t.get("dark") == DARK, str(t.get("dark")))
    css = TH.load_qss()
    import re
    left = sorted(set(re.findall(r"@[a-z][a-z0-9_]*", css)))
    check("qss_tokens_resolved", not left, str(left))
    QT_WARNINGS.clear()
    w = QtWidgets.QWidget()
    w.setStyleSheet(css)
    lay = QtWidgets.QVBoxLayout(w)
    for cls in (QtWidgets.QLabel, QtWidgets.QPushButton, QtWidgets.QToolButton, QtWidgets.QPlainTextEdit):
        lay.addWidget(cls())
    w.show()
    pump(30)
    w.close()
    bad = [m for m in QT_WARNINGS if "parse" in m.lower() and "style" in m.lower()]
    check("qss_parses", not bad, "; ".join(bad))
    for sheet in ("chatcad_legacy.qss",):
        check("legacy_qss_removed", not os.path.exists(os.path.join(REPO, sheet)))


def test_icons():
    empty = []
    for name in I.ICONS:
        img = I.pixmap(name, "#ffffff", 16).toImage()
        if not any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height())):
            empty.append(name)
    check("icons_render", not empty, str(empty))
    ic = I.icon("sparkle", "#888888", 14, on="check")
    check("icon_on_state", not ic.pixmap(14, 14, QtGui.QIcon.Normal, QtGui.QIcon.On).isNull())


def test_set_prop():
    TH.tokens()
    host = QtWidgets.QWidget()
    host.setStyleSheet(TH.load_qss())
    dot = QtWidgets.QLabel(host)
    dot.setObjectName("ccDot")
    host.resize(40, 40)
    host.show()
    pump(20)
    TH.set_prop(dot, "state", "ok")
    pump(20)
    c1 = dot.grab().toImage().pixelColor(3, 3).name()
    TH.set_prop(dot, "state", "off")
    pump(20)
    c2 = dot.grab().toImage().pixelColor(3, 3).name()
    check("set_prop_repolish", c1 == TH.T["success"] and c2 == TH.T["danger"], f"{c1} {c2}")
    host.close()


def test_describe_tool():
    ic, title, tip, hint, detail = W.describe_tool("mcp__freecad__execute_code",
                                                   {"code": "# Cria a planilha Parametros.\nimport FreeCAD"})
    check("describe_comment_title", title == "Cria a planilha Parametros" and ic == "code", title)
    check("describe_detail_code", "import FreeCAD" in detail)
    _ic, title, _t, hint, detail = W.describe_tool("mcp__freecad__get_object", {"obj_name": "Caixa"})
    check("describe_hint", hint == "Caixa" and '"obj_name"' in detail, hint)
    _ic, title, *_ = W.describe_tool("run_python", {"code": "x = 1", "resumo": "aumenta a largura"})
    check("describe_local_resumo", title == "Aumenta a largura", title)
    _ic, title, *_ = W.describe_tool("algo_novo", {})
    check("describe_unknown", title == "algo novo", title)


def test_markdown():
    TH.tokens()
    host = QtWidgets.QWidget()
    host.setStyleSheet(TH.load_qss())
    host.setFont(TH.ui_font(13))
    lay = QtWidgets.QVBoxLayout(host)
    view = W.AnswerView()
    lay.addWidget(view)
    host.resize(300, 600)
    host.show()
    long_line = "x = " + " + ".join(f"Parametros.largura_{i}" for i in range(12))
    view.add_text("# Título grande\n\nTexto com `largura` e [link](https://freecad.org).\n\n"
                  "```python\n" + long_line + "\nprint(x)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    view.render()
    pump(30)
    doc = view.document()
    base = doc.defaultFont().pixelSize()
    b = doc.begin()
    h1 = code_px = None
    while b.isValid():
        if b.blockFormat().headingLevel() == 1 and h1 is None:
            h1 = b.begin().fragment().charFormat().font().pixelSize()
        if b.blockFormat().hasProperty(QtGui.QTextFormat.BlockCodeFence) and code_px is None:
            code_px = b.begin().fragment().charFormat().font().pixelSize()
        b = b.next()
    # tamanhos em px (pt só bate com px a 96 dpi): corpo 13, título 15, código 12
    check("md_h1_scale", base == 13 and h1 is not None and base < h1 <= round(base * 1.15), f"h1={h1} base={base}")
    check("md_code_px", code_px == 12, f"code={code_px}")
    check("md_code_wraps", doc.idealWidth() <= view.viewport().width() + 1 and view.width() <= 300,
          f"ideal={doc.idealWidth()} vw={view.viewport().width()}")
    accent = QtGui.QColor(TH.T["code_accent"])
    found = False
    b = doc.begin()
    while b.isValid() and not found:
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            if fr.isValid() and fr.text() == "largura" and fr.charFormat().foreground().color() == accent:
                found = True
            it += 1
        b = b.next()
    check("md_inline_code_colored", found)
    check("md_fixed_height", view.height() > 60 and view.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff,
          str(view.height()))
    # link sem esquema ("peca.stl") abre fora e não troca a resposta pelo "documento" peca.stl
    opened = []
    real_open = QtGui.QDesktopServices.openUrl
    QtGui.QDesktopServices.openUrl = lambda u: opened.append(QtCore.QUrl(u))
    try:
        link = W.AnswerView()
        lay.addWidget(link)
        link.add_text("Exportei o arquivo [peca.stl](peca.stl) na pasta.")
        link.render()
        pump(30)
        before = link.toPlainText()
        cur = link.document().find("peca.stl")
        r = link.cursorRect(cur)
        QtTest.QTest.mouseClick(link.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                                QtCore.QPoint(r.left() - 8, r.center().y()))
        pump(30)
        check("md_relative_link_safe", link.toPlainText() == before and not link.source().toString()
              and opened and opened[-1].isLocalFile() and opened[-1].toLocalFile().endswith("peca.stl"),
              f"{link.toPlainText()!r} {[u.toString() for u in opened]}")
    finally:
        QtGui.QDesktopServices.openUrl = real_open
    host.close()


def test_fit_widgets():
    """Caixas de código, campo de digitação e balão: altura/largura certas na largura real."""
    TH.tokens()
    host = QtWidgets.QWidget()
    host.setStyleSheet(TH.load_qss())
    host.setFont(TH.ui_font(13))
    lay = QtWidgets.QVBoxLayout(host)
    code = W.text_view("# Cria a planilha Parametros com largura, altura, espessura e folga\nx=1", 10)
    lay.addWidget(code)
    prompt = W.Prompt()
    lay.addWidget(prompt)
    lay.addStretch()
    host.resize(300, 500)
    host.show()
    pump(60)

    def code_ok():
        shown = (code.height() - code.contentsMargins().top() - code.contentsMargins().bottom()
                 - 2 * code.document().documentMargin()) / code.fontMetrics().lineSpacing()
        return code.verticalScrollBar().maximum() == 0 and int(shown) >= code.line_count() >= 3, \
            f"h={code.height()} lines={code.line_count()} vmax={code.verticalScrollBar().maximum()}"
    ok, info = code_ok()
    check("code_box_fits_wrapped_lines", ok, info)
    n300, h300 = code.line_count(), code.height()
    host.resize(560, 500)
    pump(60)
    ok, info = code_ok()
    check("code_box_refits_on_widen", code.verticalScrollBar().maximum() == 0 and code.line_count() < n300
          and code.height() < h300, info)
    bad = []
    for n in (2, 3, 5, 8):
        prompt.setPlainText("\n".join(f"linha {i}" for i in range(n)))
        pump(20)
        if prompt.verticalScrollBar().maximum() != 0:
            bad.append(n)
    prompt.setPlainText("\n".join(f"linha {i}" for i in range(12)))
    pump(20)
    check("prompt_no_scrollbar_until_max", not bad and prompt.verticalScrollBar().maximum() > 0, str(bad))
    pal = prompt.palette()
    check("prompt_placeholder_color", pal.color(QtGui.QPalette.PlaceholderText).name() == QtGui.QColor(TH.T["text3"]).name(),
          pal.color(QtGui.QPalette.PlaceholderText).name(QtGui.QColor.HexArgb))
    # balão: caminho longo quebra (não some na borda); etiqueta da seleção não é cortada à toa
    sep = chr(92)
    path = sep.join(["C:", "Users", "usuario", "Documents", "projetos", "suportes", "suporte_parede_v2.FCStd"])
    text = "salve em " + path
    shown = W.breakable(text)
    check("bubble_breakable", shown.replace(chr(0x200B), "") == text and shown.count(chr(0x200B)) >= 6, repr(shown))
    bub = W.UserBubble("Arredonde", ["Suporte_parede › Face6"], [], 219)
    lay.insertWidget(0, bub, 0, QtCore.Qt.AlignRight)
    pump(40)
    tag = bub.findChild(QtWidgets.QLabel, "ccCtxTag")
    check("bubble_tag_not_cut", QtWidgets.QLabel.text(tag) == "Suporte_parede › Face6" and bub.width() <= 219,
          f"{QtWidgets.QLabel.text(tag)!r} w={bub.width()}")
    big = W.UserBubble(text, [], [], 219)
    lay.insertWidget(0, big, 0, QtCore.Qt.AlignRight)
    pump(40)
    lab = big.label
    # (e o balão fica alto o bastante: a coluna mais larga que ele não pode encurtar o texto)
    check("bubble_long_path_wraps", big.width() <= 219 and lab.heightForWidth(lab.width()) > lab.fontMetrics().height() * 2
          and lab.height() >= lab.heightForWidth(lab.width()),
          f"w={big.width()} hfw={lab.heightForWidth(lab.width())} h={lab.height()}")
    host.close()


def test_steps_group():
    TH.tokens()
    g = W.StepsGroup()
    g.add("a", "code", "Cria a base", "x", detail="import FreeCAD")
    g.add("b", "eye", "Olhando a peça", "y")
    check("steps_running_title", g.title.text().startswith("Olhando a peça"), g.title.text())
    g.finish_step("a", False, "ok")
    g.finish_step("b", False, "")
    check("steps_rows_ok", all(r.state == "ok" for r in g.rows.values()))
    g.close_group()
    check("steps_autocollapse", g.done and g.list.isHidden(), g.title.text())
    check("steps_done_title", g.title.text() == "2 passos", g.title.text())
    g.toggle()
    check("steps_toggle_open", not g.list.isHidden())
    row = g.rows["a"]
    row.toggle()
    check("step_details_open", row.expanded() and row._body.findChild(QtWidgets.QPlainTextEdit) is not None)

    g2 = W.StepsGroup()
    g2.add("x", "code", "Falha", "")
    g2.add("y", "code", "Rodando", "")
    g2.finish_step("x", True, "Traceback: erro")
    g2.close_group(interrupted=True)
    check("steps_error_stays_open", not g2.list.isHidden() and "1 com erro" in g2.title.text(), g2.title.text())
    check("steps_interrupted_row", g2.rows["y"].state == "stop")
    check("steps_error_row", g2.rows["x"].state == "err")
    g3 = W.StepsGroup()
    g3.add("z", "code", "Rodando", "")
    g3.close_group(interrupted=True)
    check("steps_interrupted_title", "interrompido" in g3.title.text() and not g3._timer.isActive(), g3.title.text())
    return g, g2


# ---------------------------------------------------------------------------
# painel inteiro
# ---------------------------------------------------------------------------

def make_dock():
    dock = P.install(MW)
    MW.show()
    pump(80)
    return dock, dock.widget()


def find(root, cls, name):
    return root.findChildren(cls, name)


def test_backend():
    calls = []
    r = B.ClaudeRunner()
    r.toolStarted.connect(lambda i, n, inp: calls.append(("start", i, n, inp)))
    r.toolFinished.connect(lambda i, e, imgs, out: calls.append(("end", i, e, out)))
    claude = B.provider("claude")
    claude.handle({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tu1", "name": "mcp__freecad__execute_code",
         "input": {"code": "# Cria a caixa\nbox = 1"}}]}}, r)
    claude.handle({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "Code executed"}]}]}}, r)
    check("claude_tool_input", calls and calls[0][3].get("code", "").startswith("# Cria"), str(calls[:1]))
    check("claude_tool_output", len(calls) > 1 and calls[1][3] == "Code executed", str(calls[1:2]))

    calls.clear()
    codex = B.provider("codex")
    codex.handle({"type": "item.started", "item": {"id": "c1", "type": "mcp_tool_call", "tool": "get_view",
                                                   "arguments": '{"view_name": "Isometric"}'}}, r)
    codex.handle({"type": "item.completed", "item": {"id": "c1", "type": "mcp_tool_call", "tool": "get_view",
                                                     "error": {"message": "falhou"}}}, r)
    check("codex_tool_io", calls[0][3] == {"view_name": "Isometric"} and calls[1][2] is True
          and calls[1][3] == "falhou", str(calls))

    done = []
    r.finished.connect(done.append)
    agy = B.provider("agy")
    r._done = False
    agy.handle({"event": "result", "result": {
        "status": "ERROR", "response": "",
        "error": "There was a network issue connecting to the server, please try again."}}, r)
    msg = done[-1]["result"] if done else ""
    check("agy_error_message", done and done[-1]["is_error"] and msg.startswith("O Antigravity falhou:")
          and "rede" in msg and "Algo deu errado" not in msg, msg)
    r._done = False
    agy.handle({"event": "result", "result": {"status": "ERROR", "response": ""}}, r)
    check("agy_error_no_detail", done[-1]["result"].startswith("O Antigravity falhou"), done[-1]["result"])

    sp = B.SYSTEM_PROMPT
    check("prompt_active_doc_only", "SÓ no documento ativo" in sp and "outros documentos" in sp, "")
    check("prompt_no_saveas", "caminho novo" in sp)
    check("prompt_comment_title", "Comece TODO execute_code" in sp)

    if LOCAL is not None:                       # agente local (3 args) -> runner (4 args)
        r2 = B.ClaudeRunner()
        r2.set_provider(LOCAL)
        got = []
        r2.toolFinished.connect(lambda i, e, imgs, out: got.append((i, e, len(imgs), out)))
        agent = r2._local()
        agent.toolFinished.emit("local-1", False, [b"x"])
        check("local_toolfinished_bridge", got == [("local-1", False, 1, "")], str(got))


def test_panel(dock, panel):
    check("panel_objectnames", all(find(dock, cls, n) for cls, n in (
        (QtWidgets.QToolButton, "ccSend"), (QtWidgets.QToolButton, "ccPill"), (QtWidgets.QPushButton, "ccQuick"),
        (QtWidgets.QFrame, "ccBanner"), (QtWidgets.QFrame, "ccTitle"))))
    check("dock_titlebar", dock.titleBarWidget() is panel.title)
    check("panel_fits_300px", dock.minimumSizeHint().width() <= 300,
          f"dock={dock.minimumSizeHint().width()} panel={panel.minimumSizeHint().width()} "
          f"composer={panel.composer.minimumSizeHint().width()} title={panel.title.minimumSizeHint().width()} "
          f"empty={panel.stack.widget(0).minimumSizeHint().width()}")
    check("install_idempotent", P.install(MW) is dock and len(MW.findChildren(QtWidgets.QDockWidget, "ChatCAD")) == 1)
    check("one_stylesheet", panel.styleSheet() == "" and "ccSend" in dock.styleSheet())
    # todos os provedores
    for prov in B.PROVIDERS:
        panel._apply_provider(prov.key)
        pump(30)
        ok = panel.runner.prov is prov and App.ParamGet(P.PREFS).GetString("Provider") == prov.key
        local = getattr(prov, "in_process", False)
        ok = ok and panel.title.gear_btn.isVisibleTo(panel.title) == local
        check(f"provider_{prov.key}", ok, panel.title.state.text())
        check(f"provider_{prov.key}_status", panel.title.state.text().endswith("pronto"), panel.title.state.text())
    # escolha de modelo salva por provedor
    claude = B.provider("claude")
    panel._choose_model(claude, "sonnet")
    check("model_saved", App.ParamGet(P.PREFS).GetString("Model_claude") == "sonnet" and panel._model == "sonnet")
    check("model_pill_text", "Sonnet" in panel.model_pill.full_text, panel.model_pill.full_text)
    if LOCAL is not None:
        PREWARM.clear()
        panel._choose_model(LOCAL, "qwen3:8b")
        pump(30)
        check("local_prewarm_on_choose", "qwen3:8b" in PREWARM, str(PREWARM))
        check("local_model_kept", panel._model == "qwen3:8b")
        # menu de modelos: sem ferramentas => desativado (o agente local sempre manda ferramentas)
        for mode in ("agent", "plan"):
            panel._set_mode(mode)
            panel._build_model_menu()
            acts = {a.text().split("\t")[0]: a for a in panel.model_pill.menu().actions() if a.text()}
            check(f"model_menu_{mode}_tools_enabled", acts.get("qwen3:8b") and acts["qwen3:8b"].isEnabled())
            check(f"model_menu_{mode}_notools_disabled", acts.get("gemma3:4b") and not acts["gemma3:4b"].isEnabled())
            check(f"model_menu_{mode}_checked", acts.get("qwen3:8b") and acts["qwen3:8b"].isChecked())
        panel._set_mode("agent")
        check("model_menu_cloud_section", any("Claude Code · " in a.text() for a in panel.model_pill.menu().actions()))
        # sem nenhum modelo (instalação nova / Ollama fechado): a IA local continua escolhível
        LOCAL.store.save(catalog="[]")
        panel._apply_provider("claude")
        pump(30)
        panel._build_model_menu()
        acts = [a for a in panel.model_pill.menu().actions() if a.text().startswith(LOCAL.name)]
        ok = len(acts) == 1 and acts[0].isEnabled()
        if ok:
            acts[0].trigger()
            pump(30)
        check("local_selectable_without_models", ok and panel.runner.prov is LOCAL
              and panel.title.gear_btn.isVisibleTo(panel.title), str([a.text() for a in acts]))
        LOCAL.store.save(catalog=json.dumps(CATALOG))
        panel._choose_model(LOCAL, "qwen3:8b")
        pump(20)
    # menu de modelos: exe() dos CLIs não roda de novo a cada abertura
    calls = []
    for p in B.PROVIDERS:
        p.exe = (lambda f=p.exe: calls.append(1) or f())
    try:
        panel._exe_cache.clear()
        panel._build_model_menu()
        n1 = len(calls)
        panel._build_model_menu()
        check("model_menu_exe_cached", n1 >= 3 and len(calls) == n1, f"{n1} {len(calls)}")
    finally:
        for p in B.PROVIDERS:
            del p.exe
    # Antigravity: "agy models" (até 20 s) roda em segundo plano, a troca não trava
    agy = B.provider("agy")
    saved = B.AntigravityProvider._models
    B.AntigravityProvider._models = None
    agy.exe = lambda: sys.executable                 # "python models" falha na hora, sem saída
    try:
        t0 = time.perf_counter()
        panel._apply_provider("agy")
        dt = time.perf_counter() - t0
        pending = B.AntigravityProvider._fetch is not None
        for _ in range(100):
            if B.AntigravityProvider._models is not None:
                break
            pump(50)
        check("agy_models_async", dt < 5.0 and pending and B.AntigravityProvider._models == [("Padrão", "")],
              f"{dt:.2f}s pending={pending} {B.AntigravityProvider._models}")
    finally:
        del agy.exe
        B.AntigravityProvider._models = saved
    # aviso: a cor do texto acompanha o tipo (o filho é repolido)
    cols = []
    for kind in ("warn", "info", "error"):
        panel.banner.show_message(kind, "t", "texto")
        pump(10)
        cols.append(panel.banner.text.palette().color(QtGui.QPalette.WindowText).name())
    exp = [QtGui.QColor(TH.T[k]).name() for k in ("warn_text", "info_text", "danger_text")]
    check("banner_kind_repolish", cols == exp, f"{cols} {exp}")
    panel.banner.hide()
    # banner (CLI sem login e local desligado)
    AUTH.update(ok=False, msg="Claude Code não está conectado.")
    panel._apply_provider("claude")
    pump(30)
    check("banner_cli", panel.banner.isVisible() and "desconectado" in panel.title.state.text(),
          panel.title.state.text())
    AUTH.update(ok=True, msg="")
    panel._recheck()
    pump(30)
    check("banner_cleared", not panel.banner.isVisible() and panel.title.state.text().endswith("pronto"))


def simulate_turn(panel, with_error=False):
    state = {"busy": False, "sent": None, "stopped": 0}
    r = panel.runner

    def fake_send(prompt, model="", plan=False, cwd=None, images=(), use_selection=True):
        state.update(busy=True, sent=dict(prompt=prompt, model=model, plan=plan, images=list(images),
                                          use_selection=use_selection))

    r.send = fake_send
    r.busy = lambda: state["busy"]

    def fake_stop():
        state["stopped"] += 1
        state["busy"] = False
    r.stop = fake_stop
    return state


def test_conversation(dock, panel):
    panel._apply_provider("claude")
    pump(20)
    st = simulate_turn(panel)
    Gui.Selection.items = [_SelObj("Box", "Caixa", ["Face6"])]
    panel._refresh_selection()
    check("selection_chip", panel.sel_chip.isVisible() and "Caixa › Face6" in panel.sel_chip.text(),
          panel.sel_chip.text())
    panel._toggle_selection()
    check("selection_toggle_off", panel.sel_chip.property("off") is True and "Ignorando" in panel.sel_chip.text())
    panel._toggle_selection()
    # seleção comprida (e ignorada): o chip encolhe com "…", o painel continua cabendo em 300 px
    Gui.Selection.items = [_SelObj("Pad001", "CaixaPrincipalDoSuporte", ["Face12", "Face13", "Edge4"]),
                           _SelObj("Pocket002", "Pocket002", ["Edge12"])]
    panel._refresh_selection()
    panel._toggle_selection()
    pump(20)
    check("chip_fits_300px", dock.minimumSizeHint().width() <= 300 and panel.sel_chip.text().startswith("Ignorando")
          and "CaixaPrincipalDoSuporte" in panel.sel_chip.toolTip(),
          f"dock={dock.minimumSizeHint().width()} chip={panel.sel_chip.minimumSizeHint().width()}")
    panel._toggle_selection()
    Gui.Selection.items = [_SelObj("Box", "Caixa", ["Face6"])]
    panel._refresh_selection()

    # anexos
    folder = tempfile.mkdtemp(prefix="anexos_", dir=_home)
    paths = []
    for i, col in enumerate(("#d9534f", "#5bc0de")):
        pth = os.path.join(folder, f"img{i}.png")
        QtGui.QImage.fromData(png_bytes(200, 140, col)).save(pth)
        paths.append(pth)
    panel._attach_files(paths)
    pump(20)
    thumbs = panel.attach_box.findChildren(W.AttachThumb)
    check("attach_add", len(panel._attachments) == 2 and len([t for t in thumbs if t.isVisibleTo(panel)]) == 2)
    panel._remove_attachment(paths[1])
    pump(20)
    check("attach_remove", panel._attachments == [paths[0]])
    md = QtCore.QMimeData()
    md.setImageData(QtGui.QImage.fromData(png_bytes(120, 90, "#8a6d3b")))
    panel.input.insertFromMimeData(md)
    pump(20)
    check("attach_paste", len(panel._attachments) == 2, str(panel._attachments))
    panel._remove_attachment(panel._attachments[1])

    panel.input.setPlainText("Faça uma caixa 60 x 40 x 20 com tampa")
    check("send_enabled", panel.send_btn.isEnabled())
    QtTest.QTest.keyClick(panel.input, QtCore.Qt.Key_Return)
    pump(20)
    sent = st["sent"] or {}
    check("send_called", sent.get("images") == [paths[0]] and sent.get("prompt", "").endswith("com tampa")
          and sent.get("model") == "sonnet" and sent.get("plan") is False, str(sent)[:300])
    check("send_context", "Contexto da seleção" in sent.get("prompt", ""), sent.get("prompt", "")[:200])
    check("send_busy_ui", panel.send_btn.busy and "trabalhando" in panel.title.state.text())
    check("bubble_shown", panel.stack.currentIndex() == 1 and panel.findChildren(W.UserBubble))

    r = panel.runner
    r.turnBreak.emit()
    r.textDelta.emit("Vou criar a caixa com **parâmetros**.")
    r.toolStarted.emit("t1", "mcp__freecad__execute_code", {"code": "# Cria a planilha Parametros\nimport FreeCAD"})
    r.toolStarted.emit("hid", "ToolSearch", {})
    r.toolFinished.emit("t1", False, [], "Planilha criada")
    r.toolStarted.emit("t2", "mcp__freecad__execute_code", {"code": "# Faz a caixa e a tampa\nbox = 1"})
    r.toolFinished.emit("t2", False, [], "")
    r.toolStarted.emit("t3", "mcp__freecad__get_view", {"view_name": "Isometric"})
    r.toolFinished.emit("t3", False, [png_bytes()], "")
    pump(30)
    groups = panel.findChildren(W.StepsGroup)
    check("steps_group_rows", groups and len(groups[-1].rows) == 3, str([len(g.rows) for g in groups]))
    check("hidden_tool_skipped", "hid" not in groups[-1].rows)
    check("thumb_added", panel._thumb is not None and not panel._thumb.pix.isNull())
    r.turnBreak.emit()
    r.textDelta.emit("## Pronto\n\nCriei a caixa. Ajuste `largura` na planilha **Parametros**:\n\n"
                     "| Parâmetro | Valor |\n|---|---|\n| largura | 60 mm |\n| altura | 20 mm |\n")
    pump(120)
    st["busy"] = False
    App.newDocument("Teste")
    r.finished.emit({"is_error": False, "result": ""})
    pump(80)
    footers = panel.findChildren(W.TurnFooter)
    check("footer_done", footers and footers[-1].label.text().startswith("Concluído em"),
          footers[-1].label.text() if footers else "")
    check("group_collapsed_after_text", groups[-1].done and groups[-1].list.isHidden())
    answers = panel.findChildren(W.AnswerView)
    check("answers_rendered", len(answers) == 2 and "Pronto" in answers[-1].toPlainText())
    check("status_ready_again", panel.title.state.text().endswith("pronto") and not panel.send_btn.busy)
    App.closeDocument("Teste")

    # segundo turno com erro de ferramenta (grupo fica aberto) e o usuário para
    panel.input.setPlainText("Agora arredonde as bordas")
    panel.send()
    pump(10)
    r.toolStarted.emit("e1", "mcp__freecad__execute_code", {"code": "# Arredonda as bordas\nfillet()"})
    r.toolFinished.emit("e1", True, [], "Traceback (most recent call last):\n  ...\nPart.OCCError: BRep_API")
    r.toolStarted.emit("e2", "mcp__freecad__execute_code", {"code": "# Tenta de novo com raio menor"})
    pump(20)
    QtTest.QTest.keyClick(panel.input, QtCore.Qt.Key_Escape)          # Esc para
    pump(20)
    r.finished.emit({"is_error": True, "result": "Interrompido."})    # o processo morto ainda avisa
    r.textDelta.emit("texto atrasado")
    pump(30)
    texts = [f.label.text() for f in panel.findChildren(W.TurnFooter)]
    notes = [t for t in texts if "Interrompido" in t]
    callouts = panel.findChildren(W.Callout)
    check("stop_one_note", len(notes) == 1 and st["stopped"] == 1 and not callouts, f"{texts} {st}")
    g = panel.findChildren(W.StepsGroup)[-1]
    check("stop_group_open_with_error", g.done and not g.list.isHidden() and g.rows["e2"].state == "stop")
    check("late_text_ignored", "texto atrasado" not in "".join(a.source_md for a in panel.findChildren(W.AnswerView)))

    # erro do provedor => aviso com "Tentar de novo"
    panel.input.setPlainText("De novo")
    panel.send()
    st["busy"] = False
    r.finished.emit({"is_error": True, "result": ""})
    pump(20)
    callouts = panel.findChildren(W.Callout)
    check("error_callout", callouts and "sem dar detalhes" in callouts[-1].text.text()
          and callouts[-1].buttons and callouts[-1].buttons[0].text() == "Tentar de novo",
          callouts[-1].text.text() if callouts else "")
    check("error_not_generic", "Algo deu errado" not in callouts[-1].text.text())

    # C19: "Tentar de novo" num aviso antigo manda o pedido daquele aviso e não mexe no rascunho
    first = callouts[-1]
    panel.input.setPlainText("Outro pedido")
    panel.send()
    st["busy"] = False
    r.finished.emit({"is_error": True, "result": "falhou de novo"})
    pump(20)
    draft_img = os.path.join(_home, "rascunho.png")
    QtGui.QImage.fromData(png_bytes(80, 60)).save(draft_img)
    panel.input.setPlainText("rascunho longo")
    panel._attach_files([draft_img])
    st["sent"] = None
    first.buttons[0].click()
    pump(20)
    sent = st["sent"] or {}
    check("retry_uses_callout_request", sent.get("prompt", "").endswith("De novo") and sent.get("images") == [],
          str(sent)[:200])
    check("retry_keeps_draft", panel.input.toPlainText() == "rascunho longo"
          and panel._attachments == [draft_img], f"{panel.input.toPlainText()!r} {panel._attachments}")
    # o CLI ainda fecha depois da resposta: "Tentar de novo" entra na fila e sai no idle
    st["busy"] = True
    panel._set_busy(False)
    st["sent"] = None
    panel._retry(("Pedido na fila", []))
    queued = panel._send_when_idle == ("Pedido na fila", []) and st["sent"] is None
    st["busy"] = False
    panel._on_idle()
    pump(20)
    sent = st["sent"] or {}
    check("retry_queued_while_closing", queued and sent.get("prompt", "").endswith("Pedido na fila")
          and panel.input.toPlainText() == "rascunho longo", str(sent)[:200])
    st["busy"] = False
    r.finished.emit({"is_error": False, "result": ""})
    pump(20)
    panel.input.clear()
    panel._remove_attachment(draft_img)
    return st


def test_local_turn(panel):
    if LOCAL is None:
        return
    panel.new_chat()
    panel._choose_model(LOCAL, "qwen3:8b")
    pump(20)
    st = simulate_turn(panel)
    panel.input.setPlainText("Faça um suporte de parede")
    panel.send()
    sent = st["sent"] or {}
    check("local_no_panel_context", "Contexto da seleção" not in sent.get("prompt", "x")
          and sent.get("model") == "qwen3:8b" and sent.get("use_selection") is True, str(sent)[:200])
    r = panel.runner
    r.toolStarted.emit("local-1", "thinking", {})
    r.toolFinished.emit("local-1", False, [], "")
    r.toolStarted.emit("call_1", "run_python", {"code": "# Cria o suporte\nPart.makeBox(1,1,1)",
                                                "resumo": "cria o suporte"})
    r.toolFinished.emit("call_1", False, [], "")
    r.toolStarted.emit("call_2", "set_parameter", {"nome": "largura", "valor": "60"})
    r.toolFinished.emit("call_2", False, [], "")
    r.toolStarted.emit("local-2", "view_screenshot", {})
    r.toolFinished.emit("local-2", False, [png_bytes(400, 260, "#4a5568")], "")
    r.turnBreak.emit()
    r.textDelta.emit("Pronto: o suporte tem **60 mm**. Mude `largura` para ajustar.")
    st["busy"] = False
    r.finished.emit({"is_error": False, "result": "ok"})
    pump(80)
    g = panel.findChildren(W.StepsGroup)[-1]
    titles = [row.title.text() for row in g.rows.values()]
    check("local_rows", titles[:1] and titles[0].startswith("Pensou por") and "Cria o suporte" in titles,
          str(titles))
    st["busy"] = True
    r.failed.emit("O servidor de IA local não respondeu.")
    st["busy"] = False
    pump(20)
    check("local_failed_callout", panel.findChildren(W.Callout))
    # anexo + modelo sem visão e sem ponte => aviso
    pth = os.path.join(_home, "v.png")
    QtGui.QImage.fromData(png_bytes()).save(pth)
    LOCAL.store.save(vision_model="")
    panel._attach_files([pth])
    pump(20)
    warn_shown = panel.vision_warn.isVisibleTo(panel)
    bridge = LOCAL.vision_model("qwen3:8b")
    check("vision_warning", warn_shown == (bridge is None), f"{warn_shown} {bridge}")
    panel._remove_attachment(pth)

    # C17: chip "Ignorando" com a IA local: o runner recebe use_selection=False (o agente monta o contexto)
    Gui.Selection.items = [_SelObj("Pad", "Pad", ["Face6"])]
    panel._refresh_selection()
    panel._toggle_selection()
    panel.input.setPlainText("Arredonde só esta face")
    panel.send()
    sent = st["sent"] or {}
    check("local_selection_ignored", sent.get("use_selection") is False
          and sent.get("prompt") == "Arredonde só esta face", str(sent)[:200])
    st["busy"] = False
    r.finished.emit({"is_error": False, "result": "ok"})
    pump(40)
    panel._toggle_selection()
    Gui.Selection.items = [_SelObj("Box", "Caixa", ["Face6"])]
    panel._refresh_selection()


class FakeCLI(B.Provider):
    """CLI de mentira com processo de verdade (o próprio Python), para o caminho do QProcess."""
    key = "fakecli"
    name = "FakeCLI"
    login_hint = ""
    script = "import time; time.sleep(3)"

    def exe(self):
        return sys.executable

    def models(self):
        return [("Padrão", "")]

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        return ["-I", "-c", self.script], {}, None

    def handle(self, ev, sink):
        if ev.get("type") == "result":
            sink.text("Pronto.")
            sink.done(False, "")


def chat_items(panel):
    out = []
    for i in range(panel.chat.count()):
        w = panel.chat.itemAt(i).widget()
        if w is not None:
            out.append(type(w).__name__ + (":" + w.text.text() if isinstance(w, W.Callout) else "")
                       + (":" + w.label.text() if isinstance(w, W.TurnFooter) else ""))
    return out


def test_cli_process(dock, panel):
    """Parar, nova conversa no meio e o CLI que demora a sair depois do "result" (QProcess real)."""
    r = panel.runner
    for n in ("send", "busy", "stop"):                 # tira os falsos do simulate_turn
        try:
            delattr(r, n)
        except AttributeError:
            pass
    fake = FakeCLI()
    B.PROVIDERS.insert(0, fake)
    try:
        panel._apply_provider("fakecli")
        pump(50)
        # 1. Parar: botão volta a Enviar na hora; 2º clique e Esc não somam outro "Interrompido"
        panel.input.setPlainText("oi")
        panel.send()
        pump(200)
        busy_before = r.busy()
        panel._send_or_stop()
        state_now = (r.busy(), panel.send_btn.busy)
        panel._send_or_stop()
        QtTest.QTest.keyClick(panel.input, QtCore.Qt.Key_Escape)
        pump(600)
        items = chat_items(panel)
        stops = [i for i in items if "Interrompido" in i]
        check("cli_stop_once", busy_before and state_now == (False, False) and len(stops) == 1
              and not any(i.startswith("Callout") for i in items), f"{state_now} {items}")
        # 2. Nova conversa com o turno rodando: o processo morto não deixa erro na conversa nova
        panel.input.setPlainText("de novo")
        panel.send()
        pump(200)
        panel.new_chat()
        pump(600)
        panel.input.setPlainText("terceiro")
        panel.send()
        pump(100)
        items = chat_items(panel)
        check("cli_new_chat_no_stale_error", items == ["UserBubble", "TurnHeader"], str(items))
        panel._send_or_stop()
        pump(200)
        # 3. "result" chega e o processo ainda leva 1,5 s para sair: botão já é Enviar e o Enter
        #    nesse meio-tempo entra na fila (sai quando o processo fecha)
        fake.script = ("import json, sys, time; print(json.dumps({'type': 'result'})); "
                       "sys.stdout.flush(); time.sleep(2.5)")
        panel.new_chat()
        panel.input.setPlainText("rápido")
        panel.send()
        for _ in range(100):                           # espera o "result" (Python pode demorar a abrir)
            pump(50)
            if any(i.startswith("TurnFooter:Concluído") for i in chat_items(panel)):
                break
        mid = (r.busy(), panel.send_btn.busy, panel.send_btn.toolTip(), panel.title.state.text())
        panel.input.setPlainText("na fila")
        # C12/C16: Esc e o botão (que já mostra Enviar) não param o turno que terminou; o clique entra na fila
        QtTest.QTest.keyClick(panel.input, QtCore.Qt.Key_Escape)
        panel.send_btn.click()
        pump(10)
        queued = panel._send_when_idle and panel.input.toPlainText() == "na fila"
        check("cli_click_after_result_queues", queued and r.busy()
              and not any("Interrompido" in i for i in chat_items(panel)), str(chat_items(panel)))
        check("cli_button_after_result", mid[0] and not mid[1] and mid[2].startswith("Enviar")
              and mid[3].endswith("pronto"), str(mid))
        for _ in range(200):
            pump(50)
            if len(panel._bubbles) == 2:
                break
        check("cli_queued_send_after_exit", queued and len(panel._bubbles) == 2
              and panel._bubbles[-1].label.text() == "na fila" and not panel.input.toPlainText(),
              f"queued={queued} bubbles={len(panel._bubbles)}")
        for _ in range(200):
            pump(50)
            if not r.busy():
                break
        pump(50)
        check("cli_idle_button", not r.busy() and not panel.send_btn.busy, panel.title.state.text())
    finally:
        panel.new_chat()
        B.PROVIDERS.remove(fake)
        panel._apply_provider("claude")
        for _ in range(200):                           # nenhum processo vivo no fim (os._exit no Windows)
            pump(25)
            if not any(p.state() != QtCore.QProcess.NotRunning for p in panel.findChildren(QtCore.QProcess)):
                break
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def test_settings_during_turn(panel):
    """Salvar a configuração da IA local no meio de um turno para como o botão Parar e limpa a conversa."""
    if LOCAL is None:
        return
    import chatcad_settings as CS
    panel._choose_model(LOCAL, "qwen3:8b")
    pump(20)
    st = simulate_turn(panel)
    panel.input.setPlainText("Faça um suporte")
    panel.send()
    panel.runner.toolStarted.emit("local-9", "reading", {})
    pump(20)
    group = panel._group
    real_exec = CS.LocalSettingsDialog.exec
    CS.LocalSettingsDialog.exec = lambda self: QtWidgets.QDialog.Accepted
    try:
        panel._open_settings()
        pump(30)
    finally:
        CS.LocalSettingsDialog.exec = real_exec
    check("settings_save_stops_turn", st["stopped"] >= 1 and group is not None and group.done
          and group.interrupted and panel.stack.currentIndex() == 0 and not chat_items(panel),
          f"stopped={st['stopped']} items={chat_items(panel)}")


def test_panel_dialogs(panel):
    """A janela de desenho é liberada depois de usada (não fica pendurada na janela principal)."""
    import chatcad_sketch as S
    real_exec = S.SketchDialog.exec
    S.SketchDialog.exec = lambda self: QtWidgets.QDialog.Accepted
    try:
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        before = len(MW.findChildren(S.SketchDialog))
        img = panel._sketch(S.blank_sheet(), "Folha")
        pump(20)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        after = len(MW.findChildren(S.SketchDialog))
    finally:
        S.SketchDialog.exec = real_exec
    check("sketch_dialog_freed", img is not None and not img.isNull() and after == before, f"{before} -> {after}")


def test_review_fixes(panel):
    """Regressões da revisão: colar do Office, texto puro, visualizador liberado, desfazer do croqui,
    imagem vazia e aviso de privacidade da IA local."""
    import chatcad_settings as CS
    import chatcad_sketch as S
    panel.new_chat()
    panel._apply_provider("claude")
    pump(20)
    # C20: texto + bitmap (Excel) cola o texto; o botão "Colar print" ainda anexa a imagem
    panel.input.clear()
    md = QtCore.QMimeData()
    md.setText("60\t40\t20")
    md.setImageData(QtGui.QImage.fromData(png_bytes(120, 90)))
    panel.input.insertFromMimeData(md)
    pump(10)
    check("paste_office_text_wins", panel.input.toPlainText() == "60\t40\t20" and not panel._attachments,
          f"{panel.input.toPlainText()!r} {panel._attachments}")
    panel.input.clear()
    md2 = QtCore.QMimeData()
    md2.setText("60\t40\t20")
    md2.setImageData(QtGui.QImage.fromData(png_bytes(120, 90)))
    APP.clipboard().setMimeData(md2)
    panel._paste_clipboard()
    pump(10)
    check("paste_button_attaches_image", len(panel._attachments) == 1 and not panel.input.toPlainText(),
          str(panel._attachments))
    for p in list(panel._attachments):
        panel._remove_attachment(p)

    # D4: título/dica do modelo e Label do documento são texto puro
    lab = W.ElidedLabel('Cria <a href="x">clique</a> <b>ok</b>')
    lab.resize(600, 20)
    tip = lab.toolTip()
    check("elided_plain_text", lab.textFormat() == QtCore.Qt.PlainText and "&lt;a href" in tip
          and "<a href" not in tip, tip)
    chip = W.ContextChip()
    chip.set_selection("<b>Caixa</b>", True)
    check("chip_tip_plain", "&lt;b&gt;Caixa" in chip.toolTip() and "<b>Caixa" not in chip.toolTip(),
          chip.toolTip())
    check("plain_tip_untouched", W.plain_tip("Caixa › Face6") == "Caixa › Face6")

    # D2: o visualizador da miniatura é liberado depois de fechar
    real_exec = W.ImageViewer.exec
    W.ImageViewer.exec = lambda self: QtWidgets.QDialog.Accepted
    try:
        thumb = W.ImageThumb(png_bytes())
        thumb.setParent(MW)                            # pai real: a janela principal
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        before = len(MW.findChildren(W.ImageViewer))
        thumb.open_viewer()
        thumb.open_viewer()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        after = len(MW.findChildren(W.ImageViewer))
    finally:
        W.ImageViewer.exec = real_exec
    check("image_viewer_freed", after == before, f"{before} -> {after}")
    thumb.setParent(None)
    thumb.deleteLater()

    # C34: clique sem traço não vira passo de desfazer nem apaga o refazer
    dlg = S.SketchDialog(S.blank_sheet(), "Folha", MW)
    dlg.resize(1000, 760)
    dlg.show()
    pump(20)
    c = dlg.canvas
    r = c._target()
    a = QtCore.QPoint(int(r.x() + r.width() * 0.3), int(r.y() + r.height() * 0.3))
    b = QtCore.QPoint(int(r.x() + r.width() * 0.6), int(r.y() + r.height() * 0.6))
    for _ in range(2):
        QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, a)
        QtTest.QTest.mouseMove(c, b)
        QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, b)
    dlg.undo_btn.click()
    dlg.undo_btn.click()
    for tool in ("pen", "line", "arrow"):
        dlg.tool_buttons[tool].click()
        QtTest.QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, a)
    pump(10)
    check("sketch_click_keeps_redo", len(c.redo_stack) == 2 and not c.undo_stack and dlg.redo_btn.isEnabled()
          and not dlg.undo_btn.isEnabled(), f"undo={len(c.undo_stack)} redo={len(c.redo_stack)}")
    dlg.tool_buttons["line"].click()
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, a)
    QtTest.QTest.mouseMove(c, b)
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, b)
    check("sketch_stroke_still_undoable", len(c.undo_stack) == 1 and not c.redo_stack,
          f"undo={len(c.undo_stack)} redo={len(c.redo_stack)}")
    dlg.close()
    dlg.deleteLater()

    # D3: imagem vazia não divide por zero nem vira anexo fantasma
    n_err = len(ERRORS)
    cv = S.Canvas(QtGui.QImage())
    cv.resize(400, 300)
    cv.tool = "line"
    cv.preview = (QtCore.QPointF(0, 0), QtCore.QPointF(5, 5))
    cv.grab()
    QtTest.QTest.mouseClick(cv, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(10, 10))
    pump(10)
    check("sketch_null_image_no_crash", len(ERRORS) == n_err, "\n".join(ERRORS[n_err:])[:600])
    cv.deleteLater()
    got = panel._sketch(QtGui.QImage(), "Desenhar por cima")
    panel._attach_qimage(QtGui.QImage(), "vista")
    check("sketch_null_image_refused", got is None and not panel._attachments
          and panel.vision_warn.isVisibleTo(panel), panel.vision_warn.text())

    # C35: "nada sai do seu computador" só com o servidor local (loopback)
    check("privacy_note_loopback", "nada sai" in CS.privacy_note("http://localhost:11434"))
    remote = CS.privacy_note("https://ia.exemplo.com/v1")
    check("privacy_note_remote", "nada sai" not in remote and "ia.exemplo.com" in remote, remote)
    if LOCAL is not None:
        old_url = LOCAL.settings()["base_url"]
        try:
            LOCAL.store.save(base_url="http://192.168.0.50:11434")
            panel._apply_provider(LOCAL.key)
            pump(20)
            foot = panel.footnote.text()
            _t, body = panel._local_problem("O servidor de IA local não respondeu.")
            sd = CS.LocalSettingsDialog(LOCAL, MW)
            hint = sd.privacy.text()
            sd.url.setText("http://127.0.0.1:11434")
            hint_local = sd.privacy.text()
            sd.deleteLater()
            check("privacy_remote_local_server", "nada sai" not in foot.lower() and "192.168.0.50" in foot
                  and "nada sai" not in body.lower() and "nada sai" not in hint and "nada sai" in hint_local,
                  f"{foot} | {body} | {hint}")
            LOCAL.store.save(base_url="http://127.0.0.1:11434")
            panel._sync_footnote()
            check("privacy_loopback_footnote", "nada sai" in panel.footnote.text(), panel.footnote.text())
        finally:
            LOCAL.store.save(base_url=old_url)
            panel._apply_provider("claude")
            pump(20)


def test_sketch_and_settings():
    import chatcad_sketch as S
    dlg = S.SketchDialog(S.blank_sheet(), "Folha em branco", MW)
    dlg.resize(1000, 760)                            # (a tela offscreen tem 800 px: abriria compacta)
    dlg.show()
    pump(30)
    check("sketch_wide_labels", dlg.tool_buttons["pen"].toolButtonStyle() == QtCore.Qt.ToolButtonTextBesideIcon
          and dlg.redo_btn.toolButtonStyle() == QtCore.Qt.ToolButtonTextBesideIcon)
    check("sketch_objectname", dlg.objectName() == "ccSketch" and dlg.findChild(QtWidgets.QFrame, "ccSketchBar"))
    dlg.tool_buttons["arrow"].click()
    check("sketch_tool", dlg.canvas.tool == "arrow")
    dlg.swatches[1].click()
    check("sketch_color", dlg.canvas.color.name() == "#2f7bff")
    dlg.size_buttons[12].click()
    check("sketch_size", dlg.canvas.pen_width == 12)
    c = dlg.canvas
    r = c._target()
    a = QtCore.QPointF(r.x() + r.width() * 0.3, r.y() + r.height() * 0.7)
    b = QtCore.QPointF(r.x() + r.width() * 0.6, r.y() + r.height() * 0.35)
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, a.toPoint())
    QtTest.QTest.mouseMove(c, b.toPoint())
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, b.toPoint())
    dlg.tool_buttons["pen"].click()
    dlg.swatches[0].click()
    dlg.size_buttons[6].click()
    pts = [QtCore.QPointF(r.x() + r.width() * (0.2 + i * 0.02), r.y() + r.height() * (0.3 + (i % 5) * 0.01))
           for i in range(20)]
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pts[0].toPoint())
    for p in pts[1:]:
        QtTest.QTest.mouseMove(c, p.toPoint())
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pts[-1].toPoint())
    pump(20)
    check("sketch_undo_enabled", dlg.undo_btn.isEnabled() and not dlg.redo_btn.isEnabled())
    shot(dlg, "05_desenho")
    dlg.undo_btn.click()
    check("sketch_redo", dlg.redo_btn.isEnabled())
    dlg.redo_btn.click()
    check("sketch_image", not dlg.image().isNull())
    dlg.close()
    narrow = S.SketchDialog(S.blank_sheet(), "Folha em branco", MW)
    narrow.resize(700, 500)
    narrow.show()
    pump(30)
    check("sketch_narrow_icon_only", narrow.width() <= 720 and narrow.minimumSizeHint().width() <= 700
          and narrow.tool_buttons["pen"].toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
          and narrow.undo_btn.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly,
          f"w={narrow.width()} min={narrow.minimumSizeHint().width()}")
    narrow.resize(1000, 600)
    pump(30)
    check("sketch_widen_labels_back", narrow.tool_buttons["pen"].toolButtonStyle() == QtCore.Qt.ToolButtonTextBesideIcon)
    narrow.close()
    narrow.deleteLater()

    if LOCAL is not None:
        import chatcad_settings as CS
        sd = CS.LocalSettingsDialog(LOCAL, MW)
        sd.show()
        pump(30)
        sd.show_result("ok", "Ollama 0.40.2 · 2 modelo(s) com ferramentas: qwen3:8b, qwen2.5vl:7b")
        pump(20)
        check("settings_themed", sd.objectName() == "ccSettings" and sd.findChild(QtWidgets.QPushButton, "ccPrimary"))
        shot(sd, "06_config_ia_local")
        check("settings_combo_chevron", isinstance(sd.backend, CS.Combo) and isinstance(sd.vision, CS.Combo))
        fw = sd.focusWidget()
        check("settings_no_focus_ring_on_open", not isinstance(fw, (QtWidgets.QComboBox, QtWidgets.QLineEdit)),
              type(fw).__name__)
        tested = CATALOG + [{"name": "llava:7b", "tools": False, "vision": True, "thinking": [], "ctx": 4096,
                             "size": "7B", "family": "llava"}]
        sd._tmp = types.SimpleNamespace(catalog=lambda: tested,
                                        settings=lambda: dict(LOCAL.settings(), catalog=json.dumps(tested)))
        sd._tested(True, "Ollama 0.40.2 · 2 modelo(s) com ferramentas")
        items = [sd.vision.itemData(i) for i in range(sd.vision.count())]
        check("settings_vision_from_tested_server", "llava:7b" in items, str(items))
        sd._tested(False, "O servidor de IA local não respondeu em http://127.0.0.1:11434 "
                          "(Connection refused). Ele está aberto?")
        check("settings_friendly_error", "Connection refused" not in sd.test_out.text()
              and "Connection refused" in sd.test_out.toolTip(), sd.test_out.text())
        sd.close()


def take_shots(dock, panel):
    if not SHOTS:
        return
    # 1. tela inicial (IA local + qwen3:8b), largura padrão
    panel.new_chat()
    if LOCAL is not None:
        panel._choose_model(LOCAL, "qwen3:8b")
    MW.resizeDocks([dock], [380], QtCore.Qt.Horizontal)
    Gui.Selection.items = []
    pump(500)
    shot(dock, "01_inicio")
    # 2. menu de modelos aberto
    m = panel.model_pill.menu()
    panel._build_model_menu()
    m.popup(panel.model_pill.mapToGlobal(QtCore.QPoint(0, 0)))
    pump(80)
    shot(m, "02_menu_modelos")
    m.hide()
    qm = panel.quick.menu()
    panel._build_quick_menu(qm)
    qm.popup(panel.quick.mapToGlobal(QtCore.QPoint(0, 0)))
    pump(60)
    shot(qm, "02b_menu_acoes")
    qm.hide()
    # 3. conversa: um grupo recolhido, um aberto com ENTRADA
    st = simulate_turn(panel)
    Gui.Selection.items = [_SelObj("Box", "Caixa", ["Face6"])]
    panel._refresh_selection()
    panel.input.setPlainText("Faça uma caixa 60 x 40 x 20 mm com tampa de encaixe")
    pth = os.path.join(_home, "croqui.png")
    QtGui.QImage.fromData(png_bytes(300, 200, "#f2f2f2")).save(pth)
    panel._attach_files([pth])
    panel.send()
    r = panel.runner
    r.toolStarted.emit("l1", "thinking", {})
    r.toolFinished.emit("l1", False, [], "")
    r.toolStarted.emit("c1", "run_python", {"code": "# Cria a planilha Parametros\nsheet = doc.addObject("
                                                   "'Spreadsheet::Sheet', 'Parametros')\nsheet.set('B1', '60')"})
    r.toolFinished.emit("c1", False, [], "Planilha criada com 4 parâmetros.")
    r.toolStarted.emit("c2", "run_python", {"code": "# Faz a caixa oca com paredes de 2 mm\nbox = 1"})
    r.toolFinished.emit("c2", False, [], "")
    r.turnBreak.emit()
    r.textDelta.emit("Criei a base. Agora vou fazer a tampa com encaixe de 0,2 mm de folga.")
    r.toolStarted.emit("c3", "run_python", {"code": "# Faz a tampa com encaixe\nlid = 2\nlid.Placement = p",
                                            "resumo": "faz a tampa"})
    r.toolFinished.emit("c3", False, [], "Tampa criada: 60 x 40 x 3 mm.")
    r.toolStarted.emit("c4", "set_parameter", {"nome": "folga", "valor": "0.2"})
    r.toolFinished.emit("c4", True, [], "ERRO: o parâmetro 'folga' não existe na planilha.")
    r.toolStarted.emit("c5", "view_screenshot", {})
    r.toolFinished.emit("c5", False, [png_bytes(480, 300, "#3b4250")], "")
    r.turnBreak.emit()
    r.textDelta.emit("## Pronto\n\nA caixa tem **60 × 40 × 20 mm** e a tampa encaixa por cima.\n\n"
                     "- Para mudar o tamanho, edite `largura` e `altura` na planilha.\n"
                     "- A folga da tampa está em `folga_tampa`.\n\n"
                     "| Parâmetro | Valor |\n|---|---|\n| largura | 60 mm |\n| altura | 20 mm |\n")
    st["busy"] = False
    r.finished.emit({"is_error": False, "result": ""})
    pump(200)
    groups = panel.findChildren(W.StepsGroup)
    if len(groups) >= 2:
        groups[-1].set_expanded(True)
        row = groups[-1].rows.get("c3")
        if row is not None:
            row.toggle()
    pump(200)
    panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
    MW.resize(1280, 1000)
    pump(200)
    panel.scroll.verticalScrollBar().setValue(0)
    pump(60)
    shot(dock, "03_conversa_topo")
    panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
    pump(60)
    shot(dock, "03b_conversa_fim")
    MW.resize(1280, 820)
    # 4. aviso do Ollama desligado
    AUTH.update(ok=False, msg="O servidor de IA local não respondeu em http://127.0.0.1:11434 "
                              "(Connection refused). Ele está aberto?")
    panel._recheck()
    panel.new_chat()
    pump(80)
    shot(dock, "04_aviso_ollama")
    AUTH.update(ok=True, msg="")
    panel._recheck()
    pump(40)
    # 7. painel a 300 px, com conversa
    MW.resizeDocks([dock], [300], QtCore.Qt.Horizontal)
    pump(200)
    shot(dock, "07_painel_300px_inicio")
    panel.input.setPlainText("Faça um furo de 5 mm no centro da face selecionada")
    panel.send()
    r.toolStarted.emit("d1", "run_python", {"code": "# Faz o furo de 5 mm\nhole = 1"})
    r.toolFinished.emit("d1", False, [], "")
    r.toolStarted.emit("d2", "run_python", {"code": "# Confere o sólido"})
    pump(400)
    shot(dock, "07b_painel_300px_trabalhando")


def main():
    try:
        test_theme()
        test_icons()
        test_set_prop()
        test_describe_tool()
        test_markdown()
        test_fit_widgets()
        test_steps_group()
        test_backend()
        dock, panel = make_dock()
        test_panel(dock, panel)
        test_conversation(dock, panel)
        test_local_turn(panel)
        test_cli_process(dock, panel)
        test_settings_during_turn(panel)
        test_panel_dialogs(panel)
        test_review_fixes(panel)
        test_sketch_and_settings()
        take_shots(dock, panel)
    except Exception:
        RESULTS.append(("crash", False, traceback.format_exc()))
    pump(50)
    check("no_exceptions_in_slots", not ERRORS, "\n".join(ERRORS)[:3000])
    for name, ok, info in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok or not info else f"\n    {info}"))
    n_ok = sum(ok for _, ok, _ in RESULTS)
    print(f"\n{n_ok}/{len(RESULTS)} ok")
    return 0 if n_ok == len(RESULTS) else 1


def hard_exit(code):
    """Sai sem destrutores do Qt/FreeCAD. No Windows, os._exit ainda roda o DLL_PROCESS_DETACH das
    DLLs do Qt com a QApplication viva, e às vezes isso dá access violation depois de os resultados
    já estarem impressos; TerminateProcess sai direto com o código certo."""
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)


if __name__ == "__main__":
    hard_exit(main())
