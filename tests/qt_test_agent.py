"""Testes do laço do agente (Qt + HTTP de verdade) contra um servidor falso que imita o Ollama.
Rodar com o Python do FreeCAD (tem PySide6), sem abrir o FreeCAD:
  set QT_QPA_PLATFORM=offscreen
  "<FreeCAD>/bin/python.exe" -I tests/qt_test_agent.py
"""
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui  # noqa: E402

import chatcad_llm as L  # noqa: E402
import chatcad_local as LC  # noqa: E402

APP = QtGui.QGuiApplication(sys.argv)
RESULTS = []


def check(name, cond, info=""):
    RESULTS.append((name, bool(cond), info))


# ---------------------------------------------------------------------------
# servidor falso
# ---------------------------------------------------------------------------

class Script:
    responses = []      # [(status, [linhas json], atraso_por_linha)]
    requests = []
    disconnects = 0


def line(msg=None, done=False, **extra):
    ev = {"model": "m", "message": {"role": "assistant", "content": "", **(msg or {})}, "done": done}
    if done:
        ev.update(done_reason="stop", prompt_eval_count=10, eval_count=5)
    ev.update(extra)
    return ev


def text_reply(text):
    words = text.split(" ")
    pieces = [w + " " for w in words[:-1]] + words[-1:]
    return (200, [line({"content": w}) for w in pieces] + [line(done=True)], 0.01)


def tool_reply(name, args, cid="call_1"):
    return (200, [line({"tool_calls": [{"id": cid, "function": {"name": name, "arguments": args}}]}),
                  line(done=True)], 0.01)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json({"version": "0.40.2"})
        if self.path == "/api/tags":
            return self._json({"models": [{"name": "qwen3:8b"}, {"name": "gemma3:4b"}, {"name": "qwen2.5:7b"}]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/api/show":
            caps = {"qwen3:8b": (["completion", "tools", "thinking"], {"values": [False, True]}),
                    "gemma3:4b": (["completion", "vision"], None),
                    "qwen2.5:7b": (["completion", "tools"], None)}[body["model"]]
            return self._json({"capabilities": caps[0], "thinking": caps[1], "details": {"parameter_size": "8B"},
                               "model_info": {"x.context_length": 40960}})
        Script.requests.append(body)
        if not Script.responses:
            return self._json({"error": "sem resposta roteirizada"}, 500)
        status, lines, delay = Script.responses.pop(0)
        if body.get("stream") is False:
            time.sleep(delay)
            try:
                return self._json(lines[0], status)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                Script.disconnects += 1
                return
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for ev in lines:
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()
                time.sleep(delay)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            Script.disconnects += 1


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"


# ---------------------------------------------------------------------------
# ferramentas falsas (o FreeCAD de verdade é testado em fc_test_tools.py)
# ---------------------------------------------------------------------------

class FakeTools:
    def __init__(self):
        self.calls = []
        self.turn = None

    def reset(self):
        self.calls = []

    def begin_turn(self, text, backup=True, selection=True):
        self.turn = {"changed": False, "selection": selection}

    def end_turn(self, save=True):
        return "Documento salvo."

    selected = ""         # seleção que o "usuário" tem no FreeCAD (vai no contexto se não for ignorada)

    def context_text(self):
        ctx = "Documento ativo: Teste (não salvo)\nDocumento vazio."
        if FakeTools.selected and (self.turn or {}).get("selection", True):
            ctx += "\nSeleção do usuário:\n- " + FakeTools.selected
        return ctx

    problems = ""         # problemas que o "documento" tem agora (mesmo formato do final_warnings)
    effects = {}          # código do run_python -> novo valor de problems
    during = None         # chamado dentro da ferramenta (simula Gui.updateGui bombeando eventos)

    def final_warnings(self):
        if not (self.turn and self.turn.get("changed")):
            return ""
        return FakeTools.problems

    def screenshot(self, vista="isometrica"):
        return b"\x89PNG fake", ""

    def dispatch(self, name, args):
        self.calls.append((name, args))
        if name == "run_python" and args.get("code") == "reentra" and FakeTools.during:
            hook, FakeTools.during = FakeTools.during, None
            hook()
        if name == "run_python" and args.get("code") == "falha":
            return False, "ERRO: NameError: 'x' não é um parâmetro", []
        if name == "run_python" and args.get("code") == "falha2":
            return False, "ERRO: TypeError: outro erro", []
        if name in ("run_python", "set_parameter"):
            self.turn["changed"] = True
            if args.get("code") in FakeTools.effects:
                FakeTools.problems = FakeTools.effects[args["code"]]
            return True, "OK (0.1 s).\nVerificação: Peca: sólido único válido", []
        return True, f"resultado de {name}", []


def make_agent(**cfg):
    prov = LC.LocalProvider(LC.Settings(store=dict(base_url=BASE, **cfg)))
    cat = LC.Catalog(prov)
    got = []
    cat.ready.connect(lambda ok, msg: got.append((ok, msg)))
    cat.refresh()
    wait(lambda: got)
    agent = LC.LocalAgent(prov, tools=FakeTools())
    log = []
    agent.textDelta.connect(lambda s: log.append(("text", s)))
    agent.toolStarted.connect(lambda i, n, a: log.append(("start", i, n, a)))
    agent.toolFinished.connect(lambda i, e, imgs: log.append(("end", i, e, len(imgs))))
    agent.turnBreak.connect(lambda: log.append(("break",)))
    agent.finished.connect(lambda r: log.append(("finished", r)))
    agent.failed.connect(lambda m: log.append(("failed", m)))
    return prov, agent, log, got


def wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)
    return cond()


def ended(log):
    return lambda: any(e[0] in ("finished", "failed") for e in log)


def text_of(log):
    return "".join(e[1] for e in log if e[0] == "text")


def turn_nudges(agent):
    """Empurrões só do último turno (depois do último pedido de verdade do usuário)."""
    start = max(i for i, m in enumerate(agent.messages) if m["role"] == "user" and not m.get("_nudge"))
    return [m["content"] for m in agent.messages[start:] if m.get("_nudge")]


def history_ok(messages):
    """tool só logo depois do assistant com tool_calls (ou de outro tool) e toda chamada respondida."""
    for prev, m in zip(messages, messages[1:]):
        if m["role"] == "tool" and not (prev["role"] == "tool" or prev.get("tool_calls")):
            return False
    called = {c["id"] for m in messages for c in m.get("tool_calls") or []}
    answered = {m.get("tool_call_id") for m in messages if m["role"] == "tool"}
    return called <= answered


def run_all():
    # catálogo: versão, modelos com ferramentas, visão marcada
    prov, agent, log, got = make_agent()
    check("catalog_ok", got and got[0][0] and "Ollama 0.40.2" in got[0][1], got)
    check("catalog_rank", [v for _, v in prov.models()] == ["qwen3:8b", "qwen2.5:7b"], prov.models())
    check("vision_auto", prov.vision_model("qwen3:8b") == "gemma3:4b", prov.vision_model("qwen3:8b"))

    # 1. ferramenta -> resposta final; sinais na ordem e histórico válido
    Script.requests.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "x = 1", "resumo": "teste"}),
                           text_reply("Pronto: criei a peça.")]
    agent.send("crie um cubo", model="qwen3:8b")
    check("basic_ended", wait(ended(log)), log)
    kinds = [e[0] if e[0] != "start" else ("start:" + e[2]) for e in log]
    check("basic_sequence",
          kinds[:2] == ["start:run_python", "end"] and "break" in kinds and kinds[-3:] == ["start:view_screenshot", "end", "finished"],
          kinds)
    check("basic_text", text_of(log) == "Pronto: criei a peça.", text_of(log))
    roles = [m["role"] for m in agent.messages]
    check("basic_history", roles == ["system", "user", "assistant", "tool", "assistant"], roles)
    second = Script.requests[1]["messages"]
    check("tool_msg_sent", second[3]["role"] == "tool" and second[3]["tool_name"] == "run_python", second[3])
    check("context_injected", L.CONTEXT_MARK in Script.requests[0]["messages"][1]["content"],
          Script.requests[0]["messages"][1]["content"])
    check("options_fixed", Script.requests[0]["options"] == Script.requests[1]["options"]
          and Script.requests[0]["think"] is False, Script.requests[0].get("options"))
    check("prefix_stable", Script.requests[0]["messages"][:2] == Script.requests[1]["messages"][:2])

    # 2. JSON quebrado na chamada (erro do servidor) -> pede de novo e segue
    log.clear()
    Script.responses[:] = [(500, [{"error": "error parsing tool call: raw='{\"code\": \"x'"}], 0),
                           tool_reply("run_python", {"code": "y = 2"}, "call_2"),
                           text_reply("Feito.")]
    agent.send("mais um", model="qwen3:8b")
    wait(ended(log))
    check("badjson_retry", log[-1][0] == "finished" and agent.tools().calls[-1] == ("run_python", {"code": "y = 2"}), log)
    check("badjson_nudge", any(m.get("_nudge") for m in agent.messages))

    # 3. chamada escrita como texto: executa e não aparece no chat
    log.clear()
    Script.responses[:] = [text_reply('<tool_call> {"name": "get_document", "arguments": {}} </tool_call>'),
                           text_reply("O documento está vazio.")]
    agent.send("o que tem aqui?", model="qwen3:8b")
    wait(ended(log))
    check("textcall_exec", agent.tools().calls[-1][0] == "get_document", agent.tools().calls[-1])
    check("textcall_hidden", "tool_call" not in text_of(log), text_of(log))

    # 4. modelo mostra código em vez de chamar (caso real do qwen2.5:7b) -> roda como run_python,
    #    e o bloco de código não aparece no chat, mesmo chegando aos pedaços
    log.clear()
    shown = "Vou criar:\n```python\ncaixa = doc.addObject('Part::Box', 'Caixa')\n```\nPronto."
    Script.responses[:] = [(200, [line({"content": shown[i:i + 4]}) for i in range(0, len(shown), 4)]
                            + [line(done=True)], 0),
                           text_reply("Criei a caixa.")]
    agent.send("uma caixa", model="qwen3:8b")
    wait(ended(log))
    last = agent.tools().calls[-1]
    check("code_block_autorun", last[0] == "run_python" and last[1]["code"] == "caixa = doc.addObject('Part::Box', 'Caixa')",
          last)
    check("code_block_hidden", "addObject" not in text_of(log) and "```" not in text_of(log)
          and "Vou criar:" in text_of(log), text_of(log))

    # 5. modo Planejar bloqueia ferramenta que altera
    log.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "z = 3"}, "call_5"), text_reply("Proposta: ...")]
    n_calls = len(agent.tools().calls)
    agent.send("planeje", model="qwen3:8b", plan=True)
    wait(ended(log))
    ends = [e for e in log if e[0] == "end"]
    check("plan_blocked", len(agent.tools().calls) == n_calls and ends and ends[0][2] is True, log)
    check("plan_prompt", Script.requests[-2]["messages"][-1]["content"].startswith("[Modo Planejar]")
          or any(m["content"].startswith("[Modo Planejar]") for m in Script.requests[-2]["messages"] if m["role"] == "user"))

    # 6. limite de passos
    prov2, agent2, log2, _ = make_agent(max_steps=2)
    Script.responses[:] = [tool_reply("get_document", {}, f"c{i}") for i in range(2)]
    agent2.send("loop", model="qwen3:8b")
    wait(ended(log2))
    check("max_steps", "Parei depois de 2 passos" in text_of(log2) and log2[-1][0] == "finished", log2)

    # 7. pensamento vira uma linha "Pensando" antes do texto
    log.clear()
    Script.responses[:] = [(200, [line({"thinking": "hmm "}), line({"thinking": "ok"}), line({"content": "Oi"}),
                                  line(done=True)], 0.01)]
    agent.send("oi", model="qwen3:8b")
    wait(ended(log))
    names = [e[2] for e in log if e[0] == "start"]
    check("thinking_row", names[:1] == ["thinking"] and agent.messages[-1].get("thinking") is None, log)

    # 8. parar no meio do stream: sem "finished", servidor vê a desconexão, histórico fechado
    log.clear()
    Script.disconnects = 0
    Script.responses[:] = [(200, [line({"content": "palavra "})] * 200 + [line(done=True)], 0.05)]
    agent.send("texto longo", model="qwen3:8b")
    wait(lambda: any(e[0] == "text" for e in log))
    agent.stop()
    wait(lambda: Script.disconnects > 0, 5)
    APP.processEvents()
    check("stop_no_finished", not any(e[0] in ("finished", "failed") for e in log) and not agent.busy(), log[-3:])
    check("stop_server_disconnect", Script.disconnects > 0, Script.disconnects)
    check("stop_history", agent.messages[-1]["role"] == "assistant" and "interrompido" in agent.messages[-1]["content"],
          agent.messages[-1])

    # 9. parar com tool_calls pendentes: chamadas ganham resultado "não executado"
    log.clear()
    Script.responses[:] = [(200, [line({"tool_calls": [
        {"id": "a", "function": {"name": "get_document", "arguments": {}}},
        {"id": "b", "function": {"name": "get_document", "arguments": {}}}]}), line(done=True)], 0)]
    agent.send("duas", model="qwen3:8b")
    wait(lambda: any(e[0] == "start" for e in log))
    agent.stop()
    called = {c["id"] for m in agent.messages for c in m.get("tool_calls") or []}
    answered = {m["tool_call_id"] for m in agent.messages if m["role"] == "tool"}
    check("stop_pairs", called <= answered, (called, answered))

    # 10. ponte de visão: gemma descreve, a descrição entra no pedido do modelo de ferramentas
    log.clear()
    img_path = os.path.join(HERE, "_tmp_img.png")
    img = QtGui.QImage(64, 48, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor("red"))
    img.save(img_path)
    Script.requests.clear()
    Script.responses[:] = [(200, [{"message": {"role": "assistant", "content": "Um retângulo vermelho, 60 mm."}}], 0),
                           text_reply("Entendi a imagem.")]
    agent.send("veja", model="qwen3:8b", images=[img_path])
    wait(ended(log))
    os.remove(img_path)
    check("vision_first_call", Script.requests[0]["model"] == "gemma3:4b" and Script.requests[0]["messages"][0]["images"],
          Script.requests[0].get("model"))
    check("vision_injected", "retângulo vermelho" in Script.requests[1]["messages"][-1]["content"],
          Script.requests[1]["messages"][-1]["content"][-200:])
    check("vision_row", any(e[0] == "start" and e[2] == "describe_image" for e in log), log)

    # 11. servidor fora do ar
    prov3 = LC.LocalProvider(LC.Settings(store=dict(base_url="http://127.0.0.1:9", catalog=prov.settings()["catalog"])))
    agent3 = LC.LocalAgent(prov3, tools=FakeTools())
    fails = []
    agent3.failed.connect(fails.append)
    agent3.send("oi", model="qwen3:8b")
    wait(lambda: fails)
    check("server_down", fails and "não respondeu" in fails[0] and not agent3.busy(), fails)
    cat3 = LC.Catalog(prov3)
    got3 = []
    cat3.ready.connect(lambda ok, msg: got3.append((ok, msg)))
    cat3.refresh()
    wait(lambda: got3)
    check("catalog_down", got3 and got3[0][0] is False, got3)

    # 12. o mesmo código que falhou, repetido: roda só 1 vez, avisa na 2a e para na 3a
    log.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "falha"}, f"r{i}") for i in range(3)]
    before = len(agent.tools().calls)
    agent.send("repete", model="qwen3:8b")
    wait(ended(log))
    executed = [c for c in agent.tools().calls[before:] if c[1].get("code") == "falha"]
    check("repeat_guard", len(executed) == 1 and log[-1][0] == "failed" and "repetiu" in log[-1][1], log[-2:])
    sent_tool_msgs = [m["content"] for m in agent.messages if m["role"] == "tool"][-3:]
    check("repeat_warning", "exatamente o mesmo código" in sent_tool_msgs[1], sent_tool_msgs)

    # 13. portão final: "pronto" com a peça quebrada -> um empurrão, o modelo corrige e aí termina
    log.clear()
    FakeTools.problems = ""
    FakeTools.effects = {"a = 1": "2 peças visíveis separadas (Cilindro, Furo)", "corte = 1": ""}
    Script.responses[:] = [tool_reply("run_python", {"code": "a = 1"}, "g1"), text_reply("Pronto."),
                           tool_reply("run_python", {"code": "corte = 1"}, "g2"), text_reply("Agora sim.")]
    agent.send("cilindro com furo", model="qwen3:8b")
    wait(ended(log))
    nudges = [m["content"] for m in agent.messages if m.get("_nudge")]
    check("final_gate", log[-1][0] == "finished" and agent.tools().calls[-1][1] == {"code": "corte = 1"}
          and any("2 peças visíveis" in n for n in nudges) and "Pronto." not in text_of(log), text_of(log))

    # 14. modelo diz "corrigi" sem executar (caso real do qwen2.5:7b): 2o aviso mais duro e,
    #     se continuar quebrado, o painel avisa o usuário de verdade
    log.clear()
    FakeTools.problems = ""
    FakeTools.effects = {"b = 1": "2 peças visíveis separadas (Cilindro, Furo)"}
    Script.responses[:] = [tool_reply("run_python", {"code": "b = 1"}, "h1"), text_reply("Pronto."),
                           text_reply("Corrigi."), text_reply("Corrigi de novo.")]
    agent.send("cilindro com furo", model="qwen3:8b")
    wait(ended(log))
    nudges = turn_nudges(agent)
    check("gate_again", any(n.startswith("Nada mudou") for n in nudges), nudges[-2:])
    check("honest_note", "verificação automática" in text_of(log) and "2 peças visíveis" in text_of(log)
          and log[-1][0] == "finished", text_of(log))

    # 14b. C1: o modelo explica que as peças separadas são de propósito: aceita, sem ordem de "corrigir"
    log.clear()
    FakeTools.problems = ""
    FakeTools.effects = {"tampa = 1": "2 peças visíveis separadas (Caixa, Tampa)"}
    Script.requests.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "tampa = 1"}, "t1"), text_reply("Pronto."),
                           text_reply("A caixa e a tampa ficam separadas de propósito, para imprimir.")]
    agent.send("uma caixa e uma tampa separadas para imprimir", model="qwen3:8b")
    wait(ended(log))
    nudges = turn_nudges(agent)
    check("gate_accepts_intentional", log[-1][0] == "finished" and len(Script.requests) == 3
          and len(nudges) == 1 and not any(n.startswith("Nada mudou") for n in nudges)
          and "de propósito" in text_of(log), (nudges, len(Script.requests), text_of(log)))

    # 14c. C1: problema que já existia antes do turno não é cobrado do modelo
    log.clear()
    FakeTools.problems = "Velho: forma inválida"
    FakeTools.effects = {}
    Script.requests.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "furo = 1"}, "v1"), text_reply("Furo feito.")]
    agent.send("faça um furo", model="qwen3:8b")
    wait(ended(log))
    check("gate_ignores_old_problems", log[-1][0] == "finished" and not turn_nudges(agent)
          and len(Script.requests) == 2 and "Furo feito." in text_of(log), (turn_nudges(agent), text_of(log)))
    # ...mas um problema novo no mesmo documento ainda é barrado
    log.clear()
    FakeTools.effects = {"quebra = 1": "Velho: forma inválida; Novo: forma inválida"}
    Script.responses[:] = [tool_reply("run_python", {"code": "quebra = 1"}, "v2"), text_reply("Pronto."),
                           tool_reply("run_python", {"code": "conserta = 1"}, "v3"), text_reply("Consertei.")]
    FakeTools.effects["conserta = 1"] = "Velho: forma inválida"
    agent.send("mude o furo", model="qwen3:8b")
    wait(ended(log))
    nudges = turn_nudges(agent)
    check("gate_new_problem_only", log[-1][0] == "finished" and len(nudges) == 1 and "Novo:" in nudges[0]
          and "Velho:" not in nudges[0], nudges)
    FakeTools.problems, FakeTools.effects = "", {}

    # 15. resposta cortada no limite de tamanho no meio da chamada (caso real do qwen3:8b com uma
    #     peça grande): nada executado -> pede para fazer em etapas, o modelo obedece e termina
    log.clear()
    cut_reply = (200, [line({"content": ""}), line(done=True, done_reason="length")], 0.01)
    Script.requests.clear()
    Script.responses[:] = [cut_reply, tool_reply("run_python", {"code": "etapa1 = 1"}, "k1"),
                           text_reply("Pronto, em etapas.")]
    agent.send("suporte grande com muitas partes", model="qwen3:8b")
    wait(ended(log))
    nudges = [m["content"] for m in agent.messages if m.get("_nudge")]
    check("too_long_nudge", log[-1][0] == "finished" and nudges and nudges[-1] == L.NUDGE_TOO_LONG
          and agent.tools().calls[-1][1] == {"code": "etapa1 = 1"} and "etapas menores" in text_of(log),
          (log[-3:], nudges[-1:]))
    check("too_long_sent", any(m.get("content") == L.NUDGE_TOO_LONG for m in Script.requests[1]["messages"]),
          [m["role"] for m in Script.requests[1]["messages"]])

    # 15b. chamada vazada como texto no formato XML do qwen3.5, depois de um texto já mostrado:
    #      executa e o XML nunca aparece no chat
    log.clear()
    xml = ("Vou criar a placa.\n<tool_call>\n<function=run_python>\n<parameter=code>\nxml_placa = 2\n"
           "</parameter>\n</function>\n</tool_call>")
    pieces = [xml[i:i + 7] for i in range(0, len(xml), 7)]
    Script.responses[:] = [(200, [line({"content": c}) for c in pieces] + [line(done=True)], 0.005),
                           text_reply("Placa criada.")]
    agent.send("placa", model="qwen3:8b")
    wait(ended(log))
    check("xml_text_call_runs", log[-1][0] == "finished" and agent.tools().calls[-1][1].get("code") == "xml_placa = 2",
          agent.tools().calls[-1:])
    check("xml_text_hidden", "<function" not in text_of(log) and "<tool_call>" not in text_of(log)
          and "Vou criar a placa." in text_of(log), text_of(log))

    # 16. cortada 3 vezes seguidas: desiste com aviso (não fica em laço)
    log.clear()
    Script.responses[:] = [cut_reply, cut_reply, cut_reply]
    agent.send("outra peça enorme", model="qwen3:8b")
    wait(ended(log))
    check("too_long_gives_up", log[-1][0] == "finished" and "cortada" in text_of(log), text_of(log))

    # 18. C3: a chamada que falhou volta a ser aceita depois que o documento mudou (ex.: criou o
    #     parâmetro que faltava); e o aviso de repetição cita o erro DAQUELA chamada
    log.clear()
    before = len(agent.tools().calls)
    Script.responses[:] = [tool_reply("run_python", {"code": "falha"}, "f1"),
                           tool_reply("set_parameter", {"nome": "furo_d", "valor": "4"}, "f2"),
                           tool_reply("run_python", {"code": "falha"}, "f3"),
                           text_reply("Desisti.")]
    agent.send("furo", model="qwen3:8b")
    wait(ended(log))
    executed = [c for c in agent.tools().calls[before:] if c[1].get("code") == "falha"]
    check("retry_after_change", len(executed) == 2 and log[-1][0] == "finished", (executed, log[-2:]))
    log.clear()
    Script.responses[:] = [tool_reply("run_python", {"code": "falha"}, "e1"),
                           tool_reply("run_python", {"code": "falha2"}, "e2"),
                           tool_reply("run_python", {"code": "falha"}, "e3"),
                           text_reply("Fim.")]
    agent.send("erros", model="qwen3:8b")
    wait(ended(log))
    tool_msgs = [m["content"] for m in agent.messages if m["role"] == "tool"][-3:]
    check("repeat_quotes_own_error", "exatamente o mesmo código" in tool_msgs[2] and "NameError" in tool_msgs[2]
          and "TypeError" not in tool_msgs[2], tool_msgs)

    # 19. D1: corte no meio de um texto normal não vira pedido de "etapas" (termina com o aviso)
    log.clear()
    Script.requests.clear()
    prose = (200, [line({"content": "A peça tem uma base "}), line({"content": "larga e"}),
                   line(done=True, done_reason="length")], 0.01)
    Script.responses[:] = [prose]
    agent.send("explique a peça", model="qwen3:8b")
    wait(ended(log))
    check("cut_prose_no_nudge", log[-1][0] == "finished" and len(Script.requests) == 1 and not turn_nudges(agent)
          and "cortada" in text_of(log), (turn_nudges(agent), text_of(log)))

    # 20. D1: cortado só pensando -> pede para pensar menos e desliga o raciocínio no resto do turno
    log.clear()
    Script.requests.clear()
    prov.store.save(think="on")
    thinking_cut = (200, [line({"thinking": "hmm " * 50}), line(done=True, done_reason="length")], 0.01)
    Script.responses[:] = [thinking_cut, text_reply("Resposta direta.")]
    agent.send("pense bem", model="qwen3:8b")
    wait(ended(log))
    prov.store.save(think="off")
    nudges = turn_nudges(agent)
    check("cut_thinking_think_less", log[-1][0] == "finished" and nudges == [L.NUDGE_THINK_LESS]
          and Script.requests[0].get("think") is True and Script.requests[1].get("think") is False,
          (nudges, [r.get("think") for r in Script.requests]))

    # 21. D5: o usuário pediu para VER o código: o bloco não roda e aparece no chat
    log.clear()
    before = len(agent.tools().calls)
    code = "Aqui está:\n```python\nfor o in doc.Objects:\n    doc.removeObject(o.Name)\n```"
    Script.requests.clear()
    Script.responses[:] = [text_reply(code)]
    agent.send("mostre o código Python para apagar todos os objetos", model="qwen3:8b")
    wait(ended(log))
    check("see_code_not_run", log[-1][0] == "finished" and len(agent.tools().calls) == before
          and "removeObject" in text_of(log) and len(Script.requests) == 1, (agent.tools().calls[before:], text_of(log)))

    # 22. C22: chamada no formato do Llama 3.x (<function=nome>{json}</function>) executa com os argumentos
    log.clear()
    Script.responses[:] = [text_reply('<function=run_python>{"code": "llama = 1"}</function>'),
                           text_reply("Feito com llama.")]
    agent.send("placa llama", model="qwen3:8b")
    wait(ended(log))
    check("llama_text_call", agent.tools().calls[-1] == ("run_python", {"code": "llama = 1"})
          and "<function" not in text_of(log), agent.tools().calls[-1:])

    # 23. C21: parar durante a ferramenta e mandar outro pedido antes de ela voltar: o pedido espera,
    #     o resto da fila do turno parado não roda e o histórico continua válido
    log.clear()
    Script.requests.clear()
    before = len(agent.tools().calls)
    seen = {}

    def reentrant():
        agent.stop()
        seen["busy_after_stop"] = agent.busy()
        agent.send("novo pedido", model="qwen3:8b")
        seen["busy_after_send"] = agent.busy()
        seen["requests_during_tool"] = len(Script.requests)
    FakeTools.during = reentrant
    Script.responses[:] = [(200, [line({"tool_calls": [
        {"id": "q1", "function": {"name": "run_python", "arguments": {"code": "reentra"}}},
        {"id": "q2", "function": {"name": "run_python", "arguments": {"code": "nao_roda = 1"}}}]}),
        line(done=True)], 0.01), text_reply("Novo turno ok.")]
    agent.send("turno que vai parar", model="qwen3:8b")
    wait(lambda: any(e[0] == "finished" for e in log))
    wait(lambda: False, 0.3)
    codes = [c[1].get("code") for c in agent.tools().calls[before:]]
    users = [m["content"].split(L.CONTEXT_MARK)[-1] for m in agent.messages
             if m["role"] == "user" and not m.get("_nudge")]
    check("stop_in_tool_defers_send", seen.get("busy_after_send") is True and seen.get("requests_during_tool") == 1
          and codes == ["reentra"] and users[-1] == "novo pedido" and len(Script.requests) == 2
          and sum(e[0] == "finished" for e in log) == 1 and text_of(log).endswith("Novo turno ok."),
          (seen, codes, len(Script.requests), text_of(log)))
    check("stop_in_tool_history", history_ok(agent.messages)
          and agent.messages[-1] == {"role": "assistant", "content": "Novo turno ok."},
          [(m["role"], (m.get("content") or "")[:30]) for m in agent.messages[-6:]])

    # 23b. C17: chip "Ignorando": a seleção não vai no contexto da IA local (nem num envio adiado)
    FakeTools.selected = 'Pad "Pad" Face6'
    log.clear()
    Script.requests.clear()
    Script.responses[:] = [text_reply("Com seleção."), text_reply("Sem seleção.")]
    agent.send("use a face", model="qwen3:8b")
    wait(ended(log))
    with_sel = Script.requests[-1]["messages"][-1]["content"]
    log.clear()
    agent.send("ignore a face", model="qwen3:8b", use_selection=False)
    wait(ended(log))
    without = Script.requests[-1]["messages"][-1]["content"]
    check("selection_kept_by_default", "Face6" in with_sel, with_sel)
    check("selection_ignored_local", "Face6" not in without and "ignore a face" in without
          and agent.tools().turn["selection"] is False, without)
    log.clear()
    Script.requests.clear()

    def reentrant_ignored():
        agent.stop()
        agent.send("pedido adiado", model="qwen3:8b", use_selection=False)
    FakeTools.during = reentrant_ignored
    Script.responses[:] = [tool_reply("run_python", {"code": "reentra"}, "q3"), text_reply("Adiado ok.")]
    agent.send("turno com seleção", model="qwen3:8b")
    wait(lambda: any(e[0] == "finished" for e in log))
    wait(lambda: False, 0.3)
    last = Script.requests[-1]["messages"][-1]["content"] if Script.requests else ""
    check("selection_ignored_deferred", "pedido adiado" in last and "Face6" not in last
          and "Face6" in Script.requests[0]["messages"][-1]["content"], last)
    FakeTools.selected = ""

    # 24. C24: com "Liberar a memória" ligado o pré-aquecimento não recarrega o modelo
    prov5, agent5, log5, _ = make_agent(unload_after=True)
    Script.requests.clear()
    agent5.prewarm("qwen3:8b")
    check("prewarm_respects_unload", not agent5.warm.busy() and not Script.requests, Script.requests)

    # 25. C23: pré-aquecimento de OUTRO modelo não segura o envio (é abortado)
    prov6, agent6, log6, _ = make_agent()
    Script.requests.clear()
    Script.responses[:] = [(200, [{"message": {"role": "assistant", "content": ""}, "done": True}], 3.0),
                           text_reply("Oi do qwen2.5.")]
    agent6.prewarm("qwen3:8b")
    wait(lambda: len(Script.requests) == 1, 3)
    t0 = time.monotonic()
    agent6.send("oi", model="qwen2.5:7b")
    wait(ended(log6))
    check("other_model_prewarm_aborted", log6[-1][0] == "finished" and time.monotonic() - t0 < 2.0
          and Script.requests[-1]["model"] == "qwen2.5:7b" and not agent6.warm.busy(),
          (time.monotonic() - t0, [r["model"] for r in Script.requests]))

    # 26. C23: parar enquanto espera o pré-aquecimento do mesmo modelo aborta o pré-aquecimento
    wait(lambda: False, 3.2)                       # o servidor termina o atraso do teste anterior
    log6.clear()
    Script.requests.clear()
    Script.responses[:] = [(200, [{"message": {"role": "assistant", "content": ""}, "done": True}], 3.0),
                           text_reply("Depois do stop.")]
    agent6.prewarm("qwen3:8b")
    wait(lambda: len(Script.requests) == 1, 3)
    agent6.send("oi", model="qwen3:8b")
    waited = agent6._after_warm is not None
    agent6.stop()
    check("stop_aborts_prewarm", waited and not agent6.warm.busy() and agent6._after_warm is None
          and not agent6.busy(), (waited, agent6.warm.busy()))
    t0 = time.monotonic()
    agent6.send("de novo", model="qwen3:8b")
    wait(ended(log6))
    check("send_after_stop_no_wait", log6 and log6[-1][0] == "finished" and time.monotonic() - t0 < 2.0,
          (time.monotonic() - t0, log6[-2:]))
    wait(lambda: False, 3.2)

    # 16b. "prometeu e não fez" (caso real do qwen3.5:9b): termina com "Agora vou criar..." sem chamar
    #      ferramenta -> um empurrão; o modelo executa e termina
    log.clear()
    Script.responses[:] = [text_reply("Caixa criada. Agora vou criar a abertura superior."),
                           tool_reply("run_python", {"code": "abertura = 1"}, "p1"),
                           text_reply("Abertura feita.")]
    agent.send("cesto oco", model="qwen3:8b")
    wait(ended(log))
    nudges = [m["content"] for m in agent.messages if m.get("_nudge")]
    check("promise_nudge", log[-1][0] == "finished" and L.NUDGE_DO_IT in nudges
          and agent.tools().calls[-1][1].get("code") == "abertura = 1", (log[-2:], nudges[-1:]))
    # promete de novo depois do empurrão: não entra em laço (só 1 empurrão por pedido)
    log.clear()
    Script.responses[:] = [text_reply("Agora vou criar a placa."), text_reply("Agora vou criar a placa mesmo.")]
    agent.send("placa", model="qwen3:8b")
    wait(ended(log))
    nudges = [m["content"] for m in agent.messages if m.get("_nudge")]
    check("promise_once", log[-1][0] == "finished" and nudges.count(L.NUDGE_DO_IT) == 2, nudges)

    # 17. reset limpa o histórico
    agent.reset()
    check("reset", agent.messages == [] and not agent.busy())


try:
    run_all()
except Exception:
    RESULTS.append(("crash", False, traceback.format_exc()))

for name, ok, info in RESULTS:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "\n    " + str(info)[:1500]))
print(f"\n{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} ok")
SERVER.shutdown()
