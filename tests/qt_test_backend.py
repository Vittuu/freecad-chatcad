"""Testes do chatcad_backend (AuthCheck, ClaudeRunner, montagem de argumentos dos CLIs) com
executáveis falsos (um script Python que imprime linhas JSON), sem nenhum CLI de verdade.
Rodar com o Python do FreeCAD (tem PySide6), sem abrir o FreeCAD:
  set QT_QPA_PLATFORM=offscreen
  "<FreeCAD>/bin/python.exe" -I tests/qt_test_backend.py
"""
import os
import shutil
import sys
import tempfile
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore  # noqa: E402

import chatcad_backend as B  # noqa: E402

APP = QtCore.QCoreApplication(sys.argv)
RESULTS = []
TMP = tempfile.mkdtemp(prefix="chatcad_backend_test_")
MISSING_EXE = os.path.join(TMP, "nao_existe", "cli_inexistente.exe")

# fora do FreeCAD: nada de import FreeCAD
B.clean_path = lambda path: path
B.mcp_server_cmd = lambda: ("uvx", ["freecad-mcp"])
B.attachments_dir = lambda: os.path.join(TMP, "anexos")
os.makedirs(os.path.join(TMP, "anexos"), exist_ok=True)

FAKE = os.path.join(TMP, "fake_cli.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(r'''
import json, sys, time
mode = sys.argv[1]
if mode.startswith("auth"):                 # auth <atraso> <logado 0/1>
    time.sleep(float(sys.argv[2]))
    print(json.dumps({"loggedIn": sys.argv[3] == "1"}))
elif mode == "turn":                        # turno que responde com sessão
    print(json.dumps({"type": "init", "session": "sess-1"}))
    print(json.dumps({"type": "result", "text": "ok"}))
elif mode == "fail":                        # sai com erro antes de ter sessão
    sys.stderr.write("nao logado\n")
    sys.exit(1)
''')


def check(name, cond, info=""):
    RESULTS.append((name, bool(cond), info))


def wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)
    return cond()


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)


class FakeCli(B.Provider):
    """Provedor de CLI falso: o "executável" é o Python rodando o fake_cli.py."""

    def __init__(self, key, delay=0.0, logged=True, exe=None):
        self.key = self.name = key
        self.delay, self.logged = delay, logged
        self._exe = exe or sys.executable
        self.auth_calls = 0
        self.mode = "turn"
        self.first_turns = []

    def exe(self):
        return self._exe

    def auth_args(self):
        return ["-I", FAKE, "auth", str(self.delay), "1" if self.logged else "0"]

    def auth_ok(self, out):
        self.auth_calls += 1
        return B.ClaudeProvider().auth_ok(out)

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        self.first_turns.append(first_turn)
        return ["-I", FAKE, self.mode], {}, prompt

    def handle(self, ev, sink):
        if ev.get("type") == "init":
            sink.session(ev.get("session"))
        elif ev.get("type") == "result":
            sink.done(False, ev.get("text", ""))


class NoAuth:
    """Provedor sem auth_ok (como a IA local): nunca pode receber resultado de outro."""
    key = name = "semauth"

    def exe(self):
        return sys.executable

    def auth_args(self):
        return None


def auth_log():
    auth = B.AuthCheck()
    log = []
    auth.finished.connect(lambda ok, msg: log.append((ok, msg)))
    return auth, log


# ---------------------------------------------------------------------------

def test_auth_overlap():
    # C13/C18: um check lento substituído por um rápido; o lento não pode emitir nada
    slow = FakeCli("lento", delay=1.5, logged=False)
    fast = FakeCli("rapido", delay=0.0, logged=True)
    auth, log = auth_log()
    auth.run(slow)
    first_proc = auth.proc
    auth.run(fast)
    check("auth: só o check atual responde", wait(lambda: log) and log == [(True, "")], log)
    pump(2.5)
    check("auth: o check velho não emite depois", log == [(True, "")], log)
    check("auth: o provedor velho nunca é consultado", slow.auth_calls == 0, slow.auth_calls)
    check("auth: processo velho foi largado", auth.proc is None and first_proc is not auth.proc)


def test_auth_switch_to_provider_without_auth_ok():
    # C18 (a): check de CLI em andamento e troca para um provedor sem auth_ok
    slow = FakeCli("lento", delay=1.0)
    auth, log = auth_log()
    auth.run(slow)
    auth.run(NoAuth())
    pump(2.0)
    check("auth: troca para provedor sem auth_ok não quebra nem mistura", log == [(True, "")], log)
    check("auth: auth_ok do provedor trocado não roda", slow.auth_calls == 0, slow.auth_calls)


def test_auth_stale_catalog():
    # C13/C18 (b): o catálogo da IA local termina depois de trocar para um CLI
    class FakeCatalog(QtCore.QObject):
        ready = QtCore.Signal(bool, str)

        def __init__(self, prov, parent=None):
            super().__init__(parent)
            self.prov = prov

        def refresh(self):
            QtCore.QTimer.singleShot(400, self, lambda: self.ready.emit(True, "Ollama falso"))

    class FakeLocal:
        key, name, in_process = "local", "IA local", True

    real = sys.modules.get("chatcad_local")
    sys.modules["chatcad_local"] = types.SimpleNamespace(Catalog=FakeCatalog)
    try:
        local = FakeLocal()
        auth, log = auth_log()
        auth.run(local)
        check("auth: catálogo local responde normalmente", wait(lambda: log) and log == [(True, "Ollama falso")], log)
        log.clear()
        auth.run(local)
        auth.run(FakeCli("claude", delay=0.0, logged=False))
        wait(lambda: log)
        pump(1.0)
        check("auth: catálogo velho não vira resultado do CLI",
              log == [(False, "claude não está conectado.")], log)
        log.clear()
        auth.run(local)                       # voltou para a IA local: o mesmo catálogo vale de novo
        check("auth: catálogo volta a valer ao voltar para a local",
              wait(lambda: log) and log == [(True, "Ollama falso")], log)
    finally:
        if real is not None:
            sys.modules["chatcad_local"] = real
        else:
            sys.modules.pop("chatcad_local", None)


def test_auth_failed_to_start():
    # C15: FailedToStart no check não pode deixar "verificando…" para sempre
    auth, log = auth_log()
    auth.run(FakeCli("quebrado", exe=MISSING_EXE))
    ok = wait(lambda: log, 5.0)
    check("auth: falha ao iniciar vira finished(False, ...)",
          ok and log[0][0] is False and log[0][1].startswith("Não consegui iniciar o quebrado"), log)
    check("auth: processo que não iniciou é largado", auth.proc is None)


def runner_log(prov):
    runner = B.ClaudeRunner()
    runner.prov = prov
    log = []
    runner.finished.connect(lambda d: log.append(("finished", d)))
    runner.failed.connect(lambda m: log.append(("failed", m)))
    runner.idle.connect(lambda: log.append(("idle",)))
    return runner, log


def test_runner_failed_to_start():
    # C15: processo do turno que não inicia => failed() em português, nada pendurado
    prov = FakeCli("quebrado", exe=MISSING_EXE)
    runner, log = runner_log(prov)
    runner.send("oi")
    ok = wait(lambda: log, 5.0)
    check("runner: falha ao iniciar emite failed",
          ok and log[0][0] == "failed" and log[0][1].startswith("Não consegui iniciar o quebrado"), log)
    check("runner: não fica ocupado", not runner.busy())
    check("runner: turno que não iniciou não conta", runner.turns == 0, runner.turns)
    pump(0.5)
    check("runner: nenhum evento extra depois da falha", len(log) == 1, log)


def test_runner_normal_turn_and_first_turn():
    # sanidade do send() + C14: first_turn segue a falta de sessão, não o contador de turnos
    prov = FakeCli("falso")
    runner, log = runner_log(prov)
    prov.mode = "fail"
    runner.send("oi")
    wait(lambda: any(e[0] == "failed" for e in log))
    runner.send("de novo")
    wait(lambda: sum(e[0] == "failed" for e in log) >= 2)
    check("runner: turno que falhou sem sessão => o próximo ainda é o primeiro",
          prov.first_turns == [True, True], prov.first_turns)
    prov.mode = "turn"
    log.clear()
    runner.send("agora vai")
    ok = wait(lambda: any(e[0] == "idle" for e in log))
    check("runner: turno normal termina com finished + idle",
          ok and log[0] == ("finished", {"is_error": False, "result": "ok"}), log)
    check("runner: sessão capturada", runner.session_id == "sess-1", runner.session_id)
    runner.send("continua")
    wait(lambda: sum(e[0] == "idle" for e in log) >= 2)
    check("runner: com sessão não é mais o primeiro turno",
          prov.first_turns == [True, True, True, False], prov.first_turns)


def test_agy_session_from_step_update():
    # C14: o id da conversa também vem no step_update
    runner = B.ClaudeRunner()
    B.AntigravityProvider().handle({"event": "step_update", "step_update": {
        "conversation_id": "conv-9", "step_index": 0, "step_type": "agent_response", "text_delta": "Oi"}}, runner)
    check("agy: step_update guarda o conversation_id", runner.session_id == "conv-9", runner.session_id)
    args, _, _ = B.AntigravityProvider().build("faça", "", False, None, True, None)
    check("agy: primeiro turno leva o SYSTEM_PROMPT", args[1].startswith(B.SYSTEM_PROMPT))
    args, _, _ = B.AntigravityProvider().build("faça", "", False, "conv-9", False, None)
    check("agy: turno retomado não repete o SYSTEM_PROMPT", not args[1].startswith(B.SYSTEM_PROMPT))


def test_codex_sandbox_every_turn():
    # C32: sandbox só leitura também no "exec resume"
    codex = B.CodexProvider()
    first, _, _ = codex.build("oi", "", False, None, True, TMP)
    resumed, _, _ = codex.build("oi", "", False, "thread-1", False, TMP)
    for label, args in (("1º turno", first), ("retomado", resumed)):
        pairs = list(zip(args, args[1:]))
        check(f"codex: sandbox read-only no {label}", ("-c", 'sandbox_mode="read-only"') in pairs, args)
        check(f"codex: sem -s no {label}", "-s" not in args, args)
    check("codex: retomado termina com <sessão> -", resumed[-2:] == ["thread-1", "-"], resumed[-3:])
    check("codex: -C só no 1º turno", "-C" in first and "-C" not in resumed)


def test_codex_images_with_commas():
    # C33: cada imagem num par -i próprio; vírgula no caminho vira cópia sem vírgula
    folder = os.path.join(TMP, "fotos, junho")
    os.makedirs(folder, exist_ok=True)
    a = os.path.join(folder, "vista frontal, v2.png")
    b = os.path.join(TMP, "simples.png")
    for path, data in ((a, b"PNG-A"), (b, b"PNG-B")):
        with open(path, "wb") as f:
            f.write(data)
    args, _, _ = B.CodexProvider().build("oi", "", False, None, True, None, [a, b])
    values = [args[i + 1] for i, v in enumerate(args) if v == "-i"]
    check("codex: um -i por imagem", len(values) == 2, values)
    check("codex: nenhum valor de -i com vírgula", all("," not in v for v in values), values)
    check("codex: caminho sem vírgula passa igual", values[1] == b, values)
    try:
        with open(values[0], "rb") as f:
            same = f.read() == b"PNG-A"
    except OSError:
        same = False
    check("codex: a cópia tem o mesmo conteúdo", same, values[0])
    check("codex: cópia vai para a pasta de anexos",
          os.path.dirname(values[0]) == B.attachments_dir(), values[0])
    again = B._comma_free(a)
    check("codex: duas cópias no mesmo segundo não se sobrescrevem", again != values[0], (again, values[0]))
    check("codex: -i logo depois do exec", args[:2] == ["exec", "-i"], args[:3])


def test_local_selection_flag():
    # C17: o runner repassa ao agente local a escolha do chip "Ignorando" (a IA local monta o contexto)
    class FakeLocal:
        key, name, in_process = "local", "IA local", True

    calls = []

    class FakeAgent:
        def send(self, prompt, **kw):
            calls.append((prompt, kw))

    runner = B.ClaudeRunner()
    runner.prov = FakeLocal()
    runner._local = lambda: FakeAgent()
    runner.send("oi", model="m", use_selection=False)
    runner.send("de novo", model="m")
    check("runner: use_selection=False chega ao agente local",
          [kw.get("use_selection") for _, kw in calls] == [False, True], calls)


def run_all():
    for test in (test_auth_overlap, test_auth_switch_to_provider_without_auth_ok, test_auth_stale_catalog,
                 test_auth_failed_to_start, test_runner_failed_to_start,
                 test_runner_normal_turn_and_first_turn, test_agy_session_from_step_update,
                 test_codex_sandbox_every_turn, test_codex_images_with_commas, test_local_selection_flag):
        try:
            test()
        except Exception:
            RESULTS.append((test.__name__ + " (crash)", False, traceback.format_exc()))


try:
    run_all()
finally:
    pump(0.2)
    shutil.rmtree(TMP, ignore_errors=True)

for name, ok, info in RESULTS:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "\n    " + str(info)[:1500]))
print(f"\n{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} ok")
sys.exit(0 if all(ok for _, ok, _ in RESULTS) else 1)
