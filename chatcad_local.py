"""Provedor "IA local": Ollama (ou servidor compatível com OpenAI: LM Studio, llama.cpp) rodando
dentro do FreeCAD, sem CLI e sem MCP. O HTTP é assíncrono (QNetworkAccessManager, sinais no
thread da interface) e as ferramentas rodam direto no FreeCAD entre um pedido e outro.
Emite os mesmos sinais do ClaudeRunner, então o painel quase não muda."""

import base64
import json
import os
import shutil
import sys
import time

try:
    from PySide import QtCore, QtGui, QtNetwork     # shim do FreeCAD
except ImportError:                                  # testes fora do FreeCAD
    from PySide6 import QtCore, QtGui, QtNetwork

import chatcad_llm as L

PREFS = "User parameter:Plugins/ChatCAD/Local"
DEFAULTS = {
    "backend": "ollama",                 # ollama | openai
    "base_url": "http://127.0.0.1:11434",
    "api_key": "",
    "model": "",
    "vision_model": "auto",              # auto | "" (nenhum) | nome
    "openai_vision": False,              # servidor OpenAI não diz se o modelo vê imagens
    "think": "off",                      # off | on | auto (só no Planejar)
    "num_ctx": 8192,
    "num_predict": 1536,
    "temperature": 0.2,
    "keep_alive": "30m",
    "max_steps": 8,
    "tool_result_chars": 1800,
    "backup": True,
    "autosave": True,
    "unload_after": False,               # descarrega o modelo ao fim de cada resposta (economiza RAM)
    "catalog": "[]",                     # cache do /api/tags + /api/show (para models() ser instantâneo)
}

VISION_PROMPT = ("Descreva esta imagem para um projetista de CAD que não pode vê-la: que peça ou vista "
                 "aparece; formas e proporções; rabiscos, setas e marcas coloridas (vermelho costuma "
                 "marcar onde mudar, azul uma área) e para onde apontam; textos e medidas escritas, "
                 "exatamente como estão. Não invente medidas. No máximo 120 palavras, em português.")


# ---------------------------------------------------------------------------
# configuração
# ---------------------------------------------------------------------------

class Settings:
    """Preferências do FreeCAD (User parameter:Plugins/ChatCAD/Local). Com `store` (dict) fica só
    em memória: usado nos testes, para não tocar no user.cfg."""

    def __init__(self, store=None):
        self._mem = None if store is None else dict(DEFAULTS, **store)

    def load(self):
        if self._mem is not None:
            return dict(self._mem)
        import FreeCAD
        grp = FreeCAD.ParamGet(PREFS)
        cfg = {}
        for k, v in DEFAULTS.items():
            if isinstance(v, bool):
                cfg[k] = grp.GetBool(k, v)
            elif isinstance(v, int):
                cfg[k] = grp.GetInt(k, v)
            elif isinstance(v, float):
                cfg[k] = grp.GetFloat(k, v)
            else:
                cfg[k] = grp.GetString(k, v)
        return cfg

    def save(self, **values):
        if self._mem is not None:
            self._mem.update(values)
            return
        import FreeCAD
        grp = FreeCAD.ParamGet(PREFS)
        for k, v in values.items():
            if isinstance(v, bool):
                grp.SetBool(k, v)
            elif isinstance(v, int):
                grp.SetInt(k, v)
            elif isinstance(v, float):
                grp.SetFloat(k, v)
            else:
                grp.SetString(k, str(v))


def _base(cfg):
    url = (cfg["base_url"] or DEFAULTS["base_url"]).strip().rstrip("/")
    # no Windows "localhost" tenta ::1 primeiro e o Ollama só escuta em 127.0.0.1
    return url.replace("://localhost", "://127.0.0.1")


def _is_loopback(url):
    host = QtCore.QUrl(url).host()
    return host in ("127.0.0.1", "localhost", "::1", "[::1]")


def encode_image(path, max_edge=1024):
    """Reduz (os modelos de visão usam ~900 px) e manda como JPEG base64, sem prefixo data:."""
    img = QtGui.QImage(path)
    if img.isNull():
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    if max(img.width(), img.height()) > max_edge:
        img = img.scaled(max_edge, max_edge, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.convertToFormat(QtGui.QImage.Format_RGB888).save(buf, "JPG", 88)
    return base64.b64encode(bytes(buf.data())).decode()


# ---------------------------------------------------------------------------
# HTTP assíncrono
# ---------------------------------------------------------------------------

class HttpStream(QtCore.QObject):
    """Um pedido por vez. chunk(bytes) chega no thread da interface conforme o servidor manda;
    done(status, corpo_de_erro, erro) sempre fecha o pedido (erro == "canceled" se foi abortado)."""

    chunk = QtCore.Signal(bytes)
    done = QtCore.Signal(int, bytes, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QtNetwork.QNetworkAccessManager(self)
        self.reply = None

    def busy(self):
        return self.reply is not None

    def request(self, method, url, payload=None, headers=None, timeout_ms=0):
        self.abort()
        req = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
        req.setHeader(QtNetwork.QNetworkRequest.ContentTypeHeader, "application/json")
        for k, v in (headers or {}).items():
            req.setRawHeader(k.encode(), v.encode())
        if timeout_ms:
            req.setTransferTimeout(timeout_ms)       # inatividade, não duração total
        # localhost nunca passa por proxy (o FreeCAD/Addon Manager pode ter configurado um)
        self.nam.setProxy(QtNetwork.QNetworkProxy(
            QtNetwork.QNetworkProxy.NoProxy if _is_loopback(url) else QtNetwork.QNetworkProxy.DefaultProxy))
        if method == "GET":
            reply = self.nam.get(req)
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            reply = self.nam.post(req, QtCore.QByteArray(data))
        reply.setProperty("errbody", QtCore.QByteArray())
        reply.readyRead.connect(lambda r=reply: self._ready(r))
        reply.finished.connect(lambda r=reply: self._finished(r))
        self.reply = reply                            # manter referência (senão o GC derruba o pedido)

    def _status(self, r):
        return int(r.attribute(QtNetwork.QNetworkRequest.HttpStatusCodeAttribute) or 0)

    def _ready(self, r):
        if r is not self.reply:
            return
        data = bytes(r.readAll())
        if self._status(r) >= 400:
            r.setProperty("errbody", QtCore.QByteArray(bytes(r.property("errbody")) + data))
        elif data:
            self.chunk.emit(data)

    def _finished(self, r):
        current = r is self.reply
        if current:
            self.reply = None
        canceled = bool(r.property("canceled"))
        if current and not canceled:
            rest = bytes(r.readAll())
            status = self._status(r)
            body = bytes(r.property("errbody"))
            if status >= 400:
                body += rest
            elif rest:
                self.chunk.emit(rest)
            err = ""
            if r.error() != QtNetwork.QNetworkReply.NoError and status < 400:
                err = r.errorString() or "erro de rede"
            self.done.emit(status, body, err)
        elif canceled:
            self.done.emit(0, b"", "canceled")
        r.deleteLater()

    def abort(self):
        r, self.reply = self.reply, None
        if r is not None:
            r.setProperty("canceled", True)
            r.abort()          # fecha a conexão: o Ollama para de gerar (libera a CPU)


# ---------------------------------------------------------------------------
# provedor (mesma "cara" dos provedores de CLI, por duck typing)
# ---------------------------------------------------------------------------

class LocalProvider:
    key = "local"
    name = "IA local"
    login_hint = ""
    in_process = True

    def __init__(self, settings=None):
        self.store = settings or Settings()

    # interface usada pelo painel ---------------------------------------------------
    def settings(self):
        return self.store.load()

    def exe(self):
        """Só para o botão do aviso: o app do Ollama (bandeja) ou o binário."""
        return (shutil.which("ollama")
                or next((p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"),
                                     os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"))
                         if os.path.exists(p)), None))

    @property
    def connect_label(self):
        return "Iniciar Ollama" if self.settings()["backend"] == "ollama" and self.exe() else "Configurar"

    def start_server(self):
        exe = self.exe()
        if not exe:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl("https://ollama.com/download"))
            return False
        if exe.lower().endswith("ollama app.exe"):
            return QtCore.QProcess.startDetached(exe, [])
        return QtCore.QProcess.startDetached(exe, ["serve"])

    def auth_args(self):
        return None

    def login_cmd(self):
        return []

    def catalog(self):
        try:
            return json.loads(self.settings()["catalog"])
        except ValueError:
            return []

    def models(self):
        ranked = L.rank_models(self.catalog())
        if not ranked:
            return [("Nenhum modelo com ferramentas", "")]
        return [(L.model_label(i), i["name"]) for i in ranked]

    def info(self, name):
        cat = self.catalog()
        hit = next((i for i in cat if i["name"] == name), None)
        if hit is None:
            ranked = L.rank_models(cat)
            hit = ranked[0] if ranked else None
        if hit and self.settings()["backend"] == "openai":
            hit = dict(hit, vision=self.settings()["openai_vision"])
        return hit

    def vision_model(self, tool_model):
        cfg = self.settings()
        if cfg["vision_model"] == "":
            return None
        if cfg["vision_model"] != "auto":
            return cfg["vision_model"]
        vis = [i for i in self.catalog() if i["vision"] and i["name"] != tool_model]
        return vis[0]["name"] if vis else None

    # pedidos ------------------------------------------------------------------------
    def chat_request(self, model, messages, tools, think, cfg, stream=True):
        headers = {"Authorization": "Bearer " + cfg["api_key"]} if cfg["api_key"] else {}
        if cfg["backend"] == "openai":
            url = _base(cfg) + ("/chat/completions" if _base(cfg).endswith("/v1") else "/v1/chat/completions")
            body = L.openai_body(model, messages, tools, think, cfg)
        else:
            url = _base(cfg) + "/api/chat"
            body = L.ollama_body(model, messages, tools, think, cfg)
        body["stream"] = stream
        return url, body, headers

    def parser(self, cfg):
        return L.SseStream() if cfg["backend"] == "openai" else L.NdjsonStream()

    def explain(self, status, body, err, cfg):
        """Mensagem para o usuário a partir de um erro HTTP/rede."""
        detail = ""
        try:
            j = json.loads(body.decode("utf-8", "replace")) if body else {}
            detail = j.get("error") if isinstance(j.get("error"), str) else (j.get("error") or {}).get("message", "")
        except (ValueError, AttributeError):
            detail = body[:300].decode("utf-8", "replace")
        if err and not status:
            return f"O servidor de IA local não respondeu em {_base(cfg)} ({err}). Ele está aberto?"
        if "does not support tools" in detail:
            return "Este modelo não usa ferramentas. Escolha outro (ex.: qwen3:8b)."
        if status == 404:
            return f"Modelo não encontrado no servidor: {detail or status}. Clique em ↻ para atualizar a lista."
        if "memory" in detail.lower():
            return "Faltou memória para o modelo. Feche programas ou escolha um modelo menor."
        return f"O servidor de IA local respondeu erro {status}: {detail or err}"


class Catalog(QtCore.QObject):
    """Confere a conexão e monta a lista de modelos (com capacidades), sem travar a interface."""

    ready = QtCore.Signal(bool, str)

    def __init__(self, prov, parent=None):
        super().__init__(parent)
        self.prov = prov
        self.http = HttpStream(self)
        self.http.chunk.connect(lambda b: self._buf.append(b))
        self.http.done.connect(self._step)
        self._buf = []
        self._queue = []
        self._phase = None

    def refresh(self):
        self.cfg = self.prov.settings()
        self._infos, self._buf = [], []
        if self.cfg["backend"] == "openai":
            self._phase = "models"
            self.http.request("GET", _base(self.cfg).removesuffix("/v1") + "/v1/models",
                              headers={"Authorization": "Bearer " + self.cfg["api_key"]} if self.cfg["api_key"] else {},
                              timeout_ms=5000)
        else:
            self._phase = "version"
            self.http.request("GET", _base(self.cfg) + "/api/version", timeout_ms=3000)

    def _json(self):
        data, self._buf = b"".join(self._buf), []
        try:
            return json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return {}

    def _step(self, status, body, err):
        if err == "canceled":
            return
        j = self._json()
        if err or status >= 400:
            self.ready.emit(False, self.prov.explain(status, body, err or "erro", self.cfg))
            return
        if self._phase == "models":                     # OpenAI-compatível
            infos = [{"name": m.get("id", ""), "tools": True, "vision": False, "thinking": [], "ctx": 0,
                      "size": "", "family": ""} for m in j.get("data", []) if m.get("id")]
            return self._finish(infos, "Servidor OpenAI-compatível")
        if self._phase == "version":
            self.version = j.get("version", "?")
            self._phase = "tags"
            self.http.request("GET", _base(self.cfg) + "/api/tags", timeout_ms=10000)
        elif self._phase == "tags":
            self._queue = [m["name"] for m in j.get("models", []) if m.get("name")]
            self._phase = "show"
            self._show_next()
        elif self._phase == "show":
            self._infos.append(L.model_info(self._current, j))
            self._show_next()

    def _show_next(self):
        if not self._queue:
            return self._finish(self._infos, f"Ollama {self.version}")
        self._current = self._queue.pop(0)
        self.http.request("POST", _base(self.cfg) + "/api/show", {"model": self._current}, timeout_ms=10000)

    def _finish(self, infos, server):
        self.prov.store.save(catalog=json.dumps(infos))
        ranked = L.rank_models(infos)
        if not ranked:
            self.ready.emit(False, f"{server} está rodando, mas nenhum modelo usa ferramentas. "
                                   "Instale um num terminal: ollama pull qwen3:8b")
            return
        self.ready.emit(True, f"{server} · {len(ranked)} modelo(s) com ferramentas")


# ---------------------------------------------------------------------------
# agente
# ---------------------------------------------------------------------------

class LocalAgent(QtCore.QObject):
    """Mesmos sinais e métodos (send/stop/busy/reset) do ClaudeRunner."""

    textDelta = QtCore.Signal(str)
    toolStarted = QtCore.Signal(str, str, dict)
    toolFinished = QtCore.Signal(str, bool, list)
    turnBreak = QtCore.Signal()
    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, prov, tools=None, parent=None):
        super().__init__(parent)
        self.prov = prov
        self._tools_obj = tools
        self.http = HttpStream(self)
        self.http.chunk.connect(self._on_chunk)
        self.http.done.connect(self._on_done)
        self.warm = HttpStream(self)
        self.warm.done.connect(self._on_warm_done)
        self.waiting = QtCore.QTimer(self)
        self.waiting.setSingleShot(True)
        self.waiting.timeout.connect(self._show_waiting)
        self.messages = []
        self._busy = False
        self._in_tool = False
        self._deferred = None                          # envio feito enquanto a ferramenta parada termina
        self._after_warm = None
        self._warm_model = None                        # modelo do pré-aquecimento em andamento
        self._show_code = False                        # o usuário pediu para ver código neste turno
        self._baseline = []                            # problemas que o documento já tinha no começo do turno
        self._row_seq = 0
        self._gen = 0                                  # muda a cada envio/parada: callbacks velhos se calam
        self.stats = []                                # (passo, info do "done") para o README/log

    # API do runner -------------------------------------------------------------------
    def busy(self):
        return self._busy

    def tools(self):
        if self._tools_obj is None:
            import chatcad_fctools
            self._tools_obj = chatcad_fctools.FreeCADTools()
        return self._tools_obj

    def reset(self):
        self.stop()
        self.messages = []
        self.tools().reset()

    def stop(self):
        if not self._busy:
            return
        self._gen += 1
        self._stopped = True
        self._busy = False
        self._deferred = None
        if self._after_warm is not None:  # esperava o pré-aquecimento: ele pode estar travado
            self._after_warm = None
            self.warm.abort()
        self.waiting.stop()
        self.http.abort()
        if self._in_tool:                 # a ferramenta termina sozinha e fecha o histórico
            return
        self._close_rows()
        self._seal("interrompido pelo usuário")

    def prewarm(self, model=""):
        """Carrega o modelo e o prefixo fixo (prompt + ferramentas) no cache do Ollama enquanto o
        usuário digita: no CPU isso tira ~1-2 min do primeiro pedido."""
        cfg = self.prov.settings()
        info = self.prov.info(model or cfg["model"])
        # "Liberar a memória ao terminar": não recarrega o modelo sem um pedido (ex.: depois de um erro)
        if self._busy or not info or cfg["backend"] != "ollama" or cfg.get("unload_after"):
            return
        if self.warm.busy():
            if self._warm_model is None or self._warm_model == info["name"]:
                return                    # descarregando, ou já aquecendo este mesmo modelo
            self.warm.abort()             # aquecimento de outro modelo: obsoleto
        url, body, headers = self.prov.chat_request(
            info["name"], [{"role": "system", "content": L.SYSTEM_PROMPT_LOCAL}],
            L.tool_schemas(info["vision"]), L.think_param(info, cfg["think"], False), cfg, stream=False)
        body["options"]["num_predict"] = 1
        self.warm.request("POST", url, body, headers, timeout_ms=240000)
        self._warm_model = info["name"]

    def _on_warm_done(self, *_):
        self._warm_model = None
        start, self._after_warm = self._after_warm, None
        if start and self._busy:
            start()

    def send(self, prompt, model="", plan=False, cwd=None, images=(), use_selection=True):
        if self._busy:
            return
        if self._in_tool:
            # parou durante uma ferramenta que ainda roda (ex.: Gui.updateGui no código): o novo
            # pedido espera ela voltar e o turno parado ser fechado; até lá o agente fica ocupado
            self._deferred = (prompt, model, plan, cwd, images, use_selection)
            self._busy = True
            return
        cfg = self.prov.settings()
        info = self.prov.info(model or cfg["model"])
        if not info:
            self.failed.emit("Nenhum modelo local com ferramentas. Clique em ↻ ou instale um "
                             "(ollama pull qwen3:8b).")
            return
        blocker = _task_dialog_open()
        if blocker:
            self.failed.emit(blocker)
            return
        self.cfg, self.info, self.model, self.plan = cfg, info, info["name"], plan
        self._gen += 1
        self._busy, self._stopped = True, False
        self._steps = self._bad = 0
        self._cuts = 0                                                 # respostas cortadas pelo limite
        self._promises = 0                                             # "agora vou..." sem executar
        self._nudged = False
        self._gates = 0                                                # portão da verificação final (até 2x)
        self._ran_since_gate = 0
        self._failed_calls = {}                                        # chamada idêntica que já falhou
        self._last_error = ""
        self._schemas = L.tool_schemas(vision=info["vision"])          # fixo: não quebra o cache
        self._allowed = {"get_document"} | ({"view_screenshot"} if info["vision"] else set())
        if not plan:
            self._allowed |= L.MUTATING
        if cfg["backend"] == "openai":                                 # o servidor não diz se o modelo pensa
            self._think = L.openai_think(cfg["think"], plan)
        else:
            self._think = L.think_param(info, cfg["think"], plan)
        self._show_code = L.asks_to_see_code(prompt)                   # pediu para VER código: não executa
        if not self.messages:
            self.messages = [{"role": "system", "content": L.SYSTEM_PROMPT_LOCAL}]
        tools = self.tools()
        tools.begin_turn(prompt, backup=cfg["backup"], selection=use_selection)
        self._baseline = self._warnings_now(tools) if not plan else []
        context = tools.context_text() if hasattr(tools, "context_text") else ""
        text = (L.PLAN_NOTE_LOCAL if plan else "") + (context + L.CONTEXT_MARK if context else "") + prompt
        self._pending_user = {"role": "user", "content": text}
        imgs = [encode_image(p) for p in images]
        vision = self.prov.vision_model(self.model)
        if imgs and info["vision"]:
            self._pending_user["images"] = imgs
            self._go(self._start_step)
        elif imgs and vision:
            self._vision_queue = list(enumerate(imgs, 1))
            self._vision_model = vision
            self._vision_prompt = prompt
            self._go(self._describe_next)
        else:
            if imgs:
                self._pending_user["content"] += ("\n\n(O usuário anexou uma imagem, mas nenhum modelo local "
                                                  "consegue vê-la. Se precisar, peça para ele descrever.)")
            self._go(self._start_step)

    @staticmethod
    def _warnings_now(tools):
        """Problemas que o documento já tem no começo do turno (final_warnings só olha depois de uma
        mudança; aqui pergunta como se o turno já tivesse mudado algo)."""
        turn = getattr(tools, "turn", None)
        if not hasattr(tools, "final_warnings") or not isinstance(turn, dict):
            return []
        was = turn.get("changed")
        turn["changed"] = True
        try:
            return L.warning_items(tools.final_warnings())
        except Exception:
            return []
        finally:
            turn["changed"] = was

    def _later(self, ms, fn):
        gen = self._gen
        QtCore.QTimer.singleShot(ms, lambda: fn() if gen == self._gen else None)

    def _go(self, fn):
        if self.warm.busy() and self._warm_model not in (None, self.model):
            self.warm.abort()           # aquecendo outro modelo: só atrasaria (e tiraria este da RAM)
        if self.warm.busy() and self._warm_model == self.model:
            # pré-aquecimento deste modelo ainda rodando: espera (cache + CPU)
            gen = self._gen
            self._after_warm = lambda: fn() if gen == self._gen else None
        else:
            self._later(0, fn)

    # ponte de visão --------------------------------------------------------------------
    def _describe_next(self):
        if self._stopped:
            return
        if not self._vision_queue:
            self._start_step()
            return
        idx, b64 = self._vision_queue.pop(0)
        self._phase = "vision"
        self._vision_row = self._row("describe_image", {"modelo": self._vision_model, "imagem": idx})
        self._vision_idx, self._vbuf = idx, []
        msgs = [{"role": "user", "content": VISION_PROMPT + "\nPedido do usuário: " + self._vision_prompt,
                 "images": [b64]}]
        # keep_alive 0: o modelo de visão sai da memória logo depois (nunca dois modelos grandes juntos)
        cfg = dict(self.cfg, num_predict=320, temperature=0.1, num_ctx=4096, keep_alive=0)
        url, body, headers = self.prov.chat_request(self._vision_model, msgs, None, None, cfg, stream=False)
        self.http.request("POST", url, body, headers)

    def _vision_done(self, status, body, err):
        data = b"".join(self._vbuf)
        desc = ""
        try:
            j = json.loads(data.decode("utf-8", "replace"))
            desc = (j.get("message") or {}).get("content") or \
                   ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except ValueError:
            pass
        ok = bool(desc.strip()) and not err and status < 400
        self.toolFinished.emit(self._vision_row, not ok, [])
        if ok:
            self._pending_user["content"] += (f"\n\n[Imagem {self._vision_idx} anexada, descrita automaticamente "
                                              f"por {self._vision_model}; pode ter erros]: {desc.strip()}")
        else:
            self._pending_user["content"] += f"\n\n(Não consegui ler a imagem {self._vision_idx}.)"
        self._describe_next()

    # um passo = um pedido ao modelo ----------------------------------------------------
    def _start_step(self):
        if self._stopped:
            return
        if self._pending_user is not None:
            self.messages.append(self._pending_user)
            self._pending_user = None
        if self._steps >= int(self.cfg["max_steps"]):
            last = f" Último erro: {self._last_error[:200]}" if self._last_error else ""
            self._say(f"\n\nParei depois de {self._steps} passos.{last} Diga **continue** para eu seguir.")
            self._finish()
            return
        self._steps += 1
        budget = int(self.cfg["num_ctx"]) - int(self.cfg["num_predict"]) - 700
        self.messages = L.compact(self.messages, budget)
        self._phase = "chat"
        self._st = {"text": "", "held": "", "shown": "", "released": False, "thinking": "", "calls": [],
                    "done": None, "error": "", "think_row": None, "wait_row": None, "any": False,
                    "split": L.ThinkSplitter(), "fence": L.FenceFilter(), "tags": L.HideTags(), "warn": ""}
        tools = self.tools()
        if not self.plan and self._gates < 2 and hasattr(tools, "final_warnings"):
            # a peça está com problema: se este passo for a resposta final, ela vai ser barrada pelo
            # portão, então o texto fica guardado até saber (não mostra um "Pronto!" falso).
            # Só contam problemas que este turno criou (o que já existia antes não é cobrado do modelo).
            self._st["warn"] = L.new_warnings(tools.final_warnings(), getattr(self, "_baseline", ()))
        self._parser = self.prov.parser(self.cfg)
        url, body, headers = self.prov.chat_request(self.model, self.messages, self._schemas, self._think, self.cfg)
        self.http.request("POST", url, body, headers)
        self.waiting.start(1500)

    def _show_waiting(self):
        st = getattr(self, "_st", None)
        if self._busy and st is not None and not st["any"] and not st["wait_row"]:
            st["wait_row"] = self._row("reading", {})

    def _on_chunk(self, data):
        if self._stopped:
            return
        if self._phase == "vision":
            self._vbuf.append(data)
            return
        for kind, value in self._parser.feed(data):
            self._event(kind, value)

    def _event(self, kind, value):
        st = self._st
        if not st["any"]:
            st["any"] = True
            self.waiting.stop()
            self._end_row("wait_row")
        if kind == "think":
            self._thinking(value)
        elif kind == "text":
            for k, s in st["split"].feed(value):
                self._thinking(s) if k == "think" else self._text(s)
        elif kind == "tool":
            self._end_row("think_row")
            st["calls"].append(value)
        elif kind == "done":
            st["done"] = value
        elif kind == "error":
            st["error"] = value

    def _thinking(self, s):
        st = self._st
        if not st["think_row"]:
            st["think_row"] = self._row("thinking", {})
        st["thinking"] += s

    def _text(self, s):
        st = self._st
        st["text"] += s
        self._end_row("think_row")
        if st["released"]:
            self._show(s)
            return
        st["held"] += s
        head = st["held"].lstrip()
        if not head or st["warn"]:
            return
        # pode ser uma chamada escrita como texto, ou código que não devia aparecer: segura até o fim
        if head.startswith(("<tool_call>", "{", "```", "<function")) or \
                (len(head) < 11 and "<tool_call>".startswith(head)):
            return
        self._release()

    def _release(self):
        st = self._st
        txt, st["held"], st["released"] = st["held"].lstrip(), "", True
        self._show(txt)

    def _show(self, s):
        """Texto para o chat. No modo Agente blocos ``` não aparecem (o código vai para o run_python),
        a não ser que o usuário tenha pedido para ver o código."""
        st = self._st
        vis = st["tags"].feed(s)                      # chamada vazada como texto nunca aparece
        vis = vis if (self.plan or self._show_code) else st["fence"].feed(vis)
        if not vis:
            return
        if not st["shown"]:
            self.turnBreak.emit()
            vis = vis.lstrip()
        st["shown"] += vis
        self.textDelta.emit(vis)

    def _on_done(self, status, body, err):
        if self._stopped or err == "canceled":
            return
        if self._phase == "vision":
            self._vision_done(status, body, err)
            return
        for kind, value in self._parser.flush():
            self._event(kind, value)
        self.waiting.stop()
        self._end_row("wait_row")
        self._end_row("think_row")
        for k, s in self._st["split"].flush():
            self._thinking(s) if k == "think" else self._text(s)
        problem = self._st["error"] or ("" if status < 400 and not err else self.prov.explain(status, body, err, self.cfg))
        if problem:
            if "tool" in problem.lower() and "pars" in problem.lower():     # JSON da chamada quebrado
                self._bad_call(problem)
            else:
                self._fail(problem)
            return
        if self._st["done"]:
            self.stats.append((self._steps, self._st["done"]))
        self._step_finished()

    def _bad_call(self, why):
        self._bad += 1
        if self._bad >= 3:
            self._fail("O modelo errou o formato das ferramentas 3 vezes. Tente de novo ou use outro modelo.")
            return
        self.messages.append({"role": "user", "_nudge": True, "content": L.NUDGE_BAD_CALL.format(
            erro=why[:200], exemplo=L.EXAMPLES["run_python"])})
        self._start_step()

    def _step_finished(self):
        st = self._st
        calls = list(st["calls"])
        text = st["text"]
        everything = set(L.REQUIRED)
        if not calls and (st["held"] or text):
            # chamada que o servidor não reconheceu e veio no texto (ex.: qwen3.5 com <function=...>);
            # pode estar no trecho segurado ou depois de um texto que já foi mostrado
            calls, cleaned = L.extract_text_calls(st["held"] or text, everything)
            if calls:
                text = (st["shown"] + "\n" + cleaned).strip() if st["held"] else cleaned
                st["held"] = ""
        if not calls and not self.plan and not self._show_code:
            # o modelo "mostrou" código do FreeCAD em vez de chamar a ferramenta: executa como run_python
            # (mesma confiança de uma chamada normal; economiza um passo de ~1 min no CPU). Se o
            # usuário pediu para VER o código, o bloco é a resposta e não roda.
            code, cleaned = L.code_from_text(text)
            if code:
                calls = [{"id": "", "name": "run_python",
                          "arguments": {"code": code, "resumo": "código escrito na resposta"}}]
                text = cleaned
        cut = bool(st["done"] and st["done"].get("reason") == "length")
        raw = (st["held"] or text).strip()
        in_code = (not raw or raw.startswith("{")
                   or any(t in raw for t in ("<tool_call>", "<function", "```")))
        thought = not raw and st["thinking"].strip()
        if cut and not calls and not self.plan and ((in_code and not self._show_code) or thought):
            # passou do limite de tamanho no meio do código/da chamada (ou só pensou): nada foi
            # executado. Em vez de terminar em silêncio, pede para refazer em etapas menores (até 2
            # vezes). Corte no meio de um texto normal não é pedido de novo (termina com o aviso).
            self._cuts += 1
            if self._cuts <= 2:
                st["held"] = ""
                if st["shown"].strip():
                    self.messages.append({"role": "assistant", "content": st["shown"].strip()})
                if thought:                         # gastou o limite pensando: pensa menos agora
                    self._think_less()
                    nudge = L.NUDGE_THINK_LESS
                    note = "_(o modelo pensou demais; pedi uma resposta mais direta)_"
                else:
                    nudge = L.NUDGE_TOO_LONG
                    note = "_(a resposta ficou longa demais; pedi para o modelo fazer em etapas menores)_"
                self.messages.append({"role": "user", "_nudge": True, "content": nudge})
                self._say(note)
                self._start_step()
                return
        if not calls and not self.plan and self._promises < 1 and L.announces_action(text):
            # terminou prometendo ("Agora vou criar a abertura...") sem executar: pede para fazer agora
            # (uma vez por pedido; modelos pequenos encerram a vez assim e a peça fica pela metade)
            self._promises += 1
            st["held"] = ""
            self.messages.append({"role": "assistant", "content": text.strip()})
            self.messages.append({"role": "user", "_nudge": True, "content": L.NUDGE_DO_IT})
            self._start_step()
            return
        if not calls and st["warn"] and self._gates and not self._ran_since_gate \
                and L.explains_warning(text):
            # respondeu ao aviso sem chamar ferramenta, explicando (ex.: "são peças separadas de
            # propósito"): aceita; o painel ainda mostra o aviso da verificação ao usuário
            st["warn"] = ""
        if not calls and st["warn"]:                    # portão: não aceita "pronto" com a peça quebrada
            warn = st["warn"]
            if warn:
                template = L.NUDGE_VERIFY_AGAIN if self._gates and not self._ran_since_gate else L.NUDGE_VERIFY
                self._gates += 1
                self._ran_since_gate = 0
                st["held"] = ""
                self.messages.append({"role": "assistant", "content": text.strip()})
                self.messages.append({"role": "user", "_nudge": True, "content": template.format(problemas=warn)})
                self._start_step()
                return
        if st["held"]:
            self._release()
        msg = {"role": "assistant", "content": (st["shown"] if calls else text).strip()}
        if st["thinking"] and self._think:
            msg["thinking"] = st["thinking"]
        if calls:
            msg["tool_calls"] = []
            for n, c in enumerate(calls):
                args, _ = L.parse_args(c.get("arguments"))
                msg["tool_calls"].append({"id": c.get("id") or f"call_{self._steps}_{n}", "name": c.get("name") or "",
                                          "arguments": args if isinstance(args, dict) else
                                          {"_bruto": str(c.get("arguments"))[:2000]}})
        self.messages.append(msg)
        if st["done"] and st["done"].get("reason") == "length" and not calls:
            self._say("\n\n_(a resposta foi cortada no limite de tamanho)_")
        if not calls:
            self._finish()
            return
        self._queue = [(tc, c) for tc, c in zip(msg["tool_calls"], calls)]
        self._extra = []                                       # imagens: só depois de todos os resultados
        self._later(0, self._next_tool)

    def _think_less(self):
        """Depois de um corte só com raciocínio: o resto do turno pensa o mínimo que o modelo deixa."""
        if isinstance(self._think, str):                       # gpt-oss: low / medium / high
            self._think = "low"
        elif self._think and False in (self.info.get("thinking") or []):
            self._think = False
        elif self._think and self.cfg["backend"] == "openai":
            self._think = False

    # ferramentas ----------------------------------------------------------------------
    def _next_tool(self):
        if self._stopped:
            return
        if not self._queue:
            self.messages += self._extra
            self._start_step()
            return
        tc, raw = self._queue.pop(0)
        tool, args, err = L.normalize_call(raw.get("name"), raw.get("arguments"), self._allowed)
        if tool is None and L.resolve_tool(raw.get("name"), set(L.REQUIRED)) in L.MUTATING:
            err = "modo Planejar: não altere a peça; só leia (get_document) e proponha os passos"
        self.toolStarted.emit(tc["id"], tool or raw.get("name") or "ferramenta", dict(args or {}))
        # dá tempo de o painel desenhar "◌ Executando…" antes de ocupar o thread da interface
        self._later(40, lambda: self._run_tool(tc["id"], tool, args, err))

    def _run_tool(self, cid, tool, args, err):
        if self._stopped and not self._in_tool:
            return
        images = []
        key = (tool, json.dumps(args or {}, sort_keys=True, ensure_ascii=False))
        prev = self._failed_calls.get(key)                     # (vezes, erro daquela chamada)
        repeated = not err and prev is not None
        if repeated and prev[0] >= 2:                          # 3a vez igual: o modelo entrou em laço
            self._failed_calls[key] = (prev[0] + 1, prev[1])
            self.toolFinished.emit(cid, True, [])
            self._fail("O modelo local repetiu o mesmo erro e eu parei. Último erro: " + prev[1][:300]
                       + "\nTente explicar o pedido de outro jeito ou use um modelo maior.")
            return
        if err or repeated:
            ok = False
            if err and not err.startswith("modo Planejar"):
                self._bad += 1
            if repeated:      # não roda de novo: devolve o erro anterior com um aviso mais forte
                text = ("ERRO: você mandou exatamente o mesmo código que já falhou com: " + prev[1][:400]
                        + "\nMude o código para corrigir esse erro antes de chamar de novo.")
            else:
                text = f"ERRO: {err}. Exemplo de chamada certa: {L.EXAMPLES.get(tool or 'run_python')}"
        else:
            self._in_tool = True
            if tool in L.MUTATING:
                self._ran_since_gate += 1
            try:
                ok, text, images = self.tools().dispatch(tool, args)
            except Exception as e:                            # nunca derruba a conversa
                ok, text = False, f"ERRO interno da ferramenta: {type(e).__name__}: {e}"
            finally:
                self._in_tool = False
            if not ok:
                self._last_error = text.split("\n")[0]
            elif tool in L.MUTATING:
                # o documento mudou: uma chamada que falhou antes pode funcionar agora (ex.: criou o
                # parâmetro que faltava), então ela pode ser tentada de novo
                self._failed_calls.clear()
                self._last_error = ""
        if not ok and not err:
            count, why = self._failed_calls.get(key, (0, ""))
            self._failed_calls[key] = (count + 1, why if repeated else self._last_error)
        self.toolFinished.emit(cid, not ok, images[:1])
        if self.messages:                                     # (reset durante a ferramenta: nada a fechar)
            self.messages.append({"role": "tool", "tool_call_id": cid, "name": tool or "",
                                  "content": L.truncate(text, int(self.cfg["tool_result_chars"]))})
        if images and tool == "view_screenshot":
            self._extra.append({"role": "user", "_nudge": True, "content": "Foto da vista 3D pedida.",
                                "images": [base64.b64encode(images[0]).decode()]})
        if self._stopped:                                     # parou durante a ferramenta
            self._seal("interrompido pelo usuário")
            self._extra = []
            if self._deferred:                                # pedido feito enquanto ela terminava
                pending, self._deferred = self._deferred, None
                self._busy = False
                self.send(*pending)
            return
        if self._bad >= 3:
            self._fail("O modelo errou o formato das ferramentas 3 vezes. Tente de novo ou use outro modelo.")
            return
        self._later(0, self._next_tool)

    # fim --------------------------------------------------------------------------------
    def _finish(self):
        tools = self.tools()
        turn = getattr(tools, "turn", None) or {}
        warn = tools.final_warnings() if (not self.plan and hasattr(tools, "final_warnings")) else ""
        if warn:   # o modelo pode dizer "pronto" mesmo assim: o painel fala a verdade
            self._say("**Atenção (verificação automática):** " + warn + ".")
        if turn.get("changed"):
            note = tools.end_turn(save=self.cfg["autosave"])
            png, _ = tools.screenshot() if hasattr(tools, "screenshot") else (None, "")
            if png:
                rid = self._row("view_screenshot", {})
                self.toolFinished.emit(rid, False, [png])
            if note.startswith("Não"):
                self._say("\n\n_" + note + "_")
        self._busy = False
        self._unload_if_asked()
        last = next((m["content"] for m in reversed(self.messages)
                     if m["role"] == "assistant" and m.get("content")), "")
        self.finished.emit({"is_error": False, "result": last})

    def _fail(self, msg):
        self._close_rows()
        self._seal("erro: " + msg[:120])
        self._busy = False
        self._unload_if_asked()
        self.failed.emit(msg)

    def _unload_if_asked(self):
        """Opção "Liberar a memória ao terminar": o Ollama descarrega o modelo agora (keep_alive 0).
        O próximo pedido recarrega (10-60 s no CPU), em troca de devolver a RAM entre respostas."""
        cfg = getattr(self, "cfg", None) or self.prov.settings()
        if not cfg.get("unload_after") or cfg["backend"] != "ollama" or self.warm.busy():
            return
        self.unload(getattr(self, "model", "") or cfg["model"])

    def unload(self, model):
        cfg = self.prov.settings()
        if not model or cfg["backend"] != "ollama":
            return
        self.warm.request("POST", _base(cfg) + "/api/chat",
                          {"model": model, "messages": [], "keep_alive": 0})

    def _seal(self, why):
        """Histórico sempre válido: pedido pendente entra, tool_calls sem resposta ganham resultado,
        texto parcial vira mensagem do assistente."""
        if getattr(self, "_pending_user", None) is not None:
            self.messages.append(self._pending_user)
            self._pending_user = None
        st = getattr(self, "_st", None)
        last_user = max((i for i, m in enumerate(self.messages) if m["role"] == "user"), default=-1)
        answered = {m.get("tool_call_id") for m in self.messages[last_user:] if m["role"] == "tool"}
        for m in self.messages[last_user:]:
            for c in m.get("tool_calls") or []:
                if c["id"] not in answered:
                    self.messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                                          "content": f"não executado ({why})"})
        if st and st.get("shown") and self.messages and self.messages[-1]["role"] in ("user", "tool"):
            self.messages.append({"role": "assistant", "content": st["shown"] + f" [{why}]"})
        self._queue = []

    # linhas de status no chat ----------------------------------------------------------
    def _row(self, name, data):
        self._row_seq += 1
        rid = f"local-{self._row_seq}"
        self.toolStarted.emit(rid, name, data)
        return rid

    def _end_row(self, key, error=False):
        st = getattr(self, "_st", None)
        if st and st.get(key):
            self.toolFinished.emit(st[key], error, [])
            st[key] = None

    def _close_rows(self):
        for key in ("wait_row", "think_row"):
            self._end_row(key, error=True)

    def _say(self, text):
        self.turnBreak.emit()
        self.textDelta.emit(text.strip())


def _task_dialog_open():
    gui = sys.modules.get("FreeCADGui")
    try:
        if gui is not None and gui.Control.activeDialog():
            return ("Há uma tarefa aberta no painel Tarefas do FreeCAD (ex.: editando um esboço). "
                    "Clique em OK ou Cancelar nela e envie de novo.")
    except Exception:
        pass
    return ""
