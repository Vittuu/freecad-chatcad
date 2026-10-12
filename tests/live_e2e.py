"""Ponta a ponta: Ollama de verdade + ferramentas de verdade + laço Qt, no freecadcmd (sem janela).
  set FREECAD_USER_HOME=<pasta temporária>  &  set CHATCAD_TESTS=<repo>/tests
  set CHATCAD_MODEL=qwen3:8b  &  set CHATCAD_PROMPT=...
  freecadcmd -c "exec(open(r'<repo>/tests/live_e2e.py', encoding='utf-8').read())"
Lento de propósito (CPU): 3-10 min. Gera tests/live_result_<modelo>.txt para o README."""
import json
import os
import sys
import time
import traceback

HERE = os.environ["CHATCAD_TESTS"]
sys.path.insert(0, os.path.dirname(HERE))

import FreeCAD as App  # noqa: E402
from PySide import QtCore  # noqa: E402

import chatcad_fctools as T  # noqa: E402
import chatcad_local as LC  # noqa: E402

MODEL = os.environ.get("CHATCAD_MODEL", "qwen3:8b")
PROMPT = os.environ.get("CHATCAD_PROMPT",
                        "Crie um cilindro de 40 mm de diâmetro e 15 mm de altura com um furo central de 10 mm.")
OUT = os.path.join(HERE, f"live_result_{MODEL.replace(':', '_')}.txt")
app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
lines = []


def log(*a):
    lines.append(" ".join(str(x) for x in a))


def pump(cond, timeout):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        app.processEvents(QtCore.QEventLoop.AllEvents, 100)
        time.sleep(0.01)
    return cond()


try:
    doc = App.newDocument("Peca")
    doc.UndoMode = 1
    prov = LC.LocalProvider(LC.Settings(store={"model": MODEL, "think": "off"}))
    cat = LC.Catalog(prov)
    got = []
    cat.ready.connect(lambda ok, msg: got.append((ok, msg)))
    cat.refresh()
    pump(lambda: got, 30)
    log("catalog:", got, [v for _, v in prov.models()])
    agent = LC.LocalAgent(prov, tools=T.FreeCADTools())
    t0 = time.monotonic()
    events = []
    agent.textDelta.connect(lambda s: events.append(("text", s)))
    agent.toolStarted.connect(lambda i, n, a: events.append(("start", round(time.monotonic() - t0), n,
                                                             (a.get("code") or "")[:2000])))
    agent.toolFinished.connect(lambda i, e, im: events.append(("end", round(time.monotonic() - t0), i, e)))
    agent.finished.connect(lambda r: events.append(("finished", round(time.monotonic() - t0))))
    agent.failed.connect(lambda m: events.append(("failed", m)))
    agent.prewarm(MODEL)
    pump(lambda: not agent.warm.busy(), 600)
    log(f"prewarm: {time.monotonic() - t0:.0f}s")
    t0 = time.monotonic()
    agent.send(PROMPT, model=MODEL)
    pump(lambda: any(e[0] in ("finished", "failed") for e in events), 1500)
    log(f"turno: {time.monotonic() - t0:.0f}s, passos: {agent._steps}")
    for step, info in agent.stats:
        log(f"  passo {step}: prompt {info['prompt_tokens']} tok (cache {info['cached_tokens']}) "
            f"{info.get('prompt_s', 0):.0f}s, saída {info['output_tokens']} tok {info.get('output_s', 0):.0f}s, "
            f"load {info.get('load_s', 0):.0f}s")
    text = "".join(e[1] for e in events if e[0] == "text")
    for e in events:
        if e[0] != "text":
            log("  ", e)
    log("resposta:", text)
    for m in agent.messages:
        if m["role"] == "tool":
            log("tool result:", m["content"][:700].replace("\n", " | "))
    finals = T.FreeCADTools().final_parts(doc)
    log("peças finais:", [(o.Name, o.Shape.isValid(), len(o.Shape.Solids),
                           round(o.Shape.BoundBox.XLength, 2), round(o.Shape.Volume)) for o in finals])
    log("parâmetros:", T.FreeCADTools().params_text(doc))
    log("undo:", doc.UndoNames)
except Exception:
    log(traceback.format_exc())

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
