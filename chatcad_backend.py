"""Roda agentes de terminal (Claude Code, Codex, Antigravity) em modo headless e traduz os
eventos de cada um em sinais Qt comuns para o painel."""

import base64
import glob
import json
import os
import shutil

from PySide import QtCore

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
WINGET = r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"

SYSTEM_PROMPT = """Você é o agente de modelagem do Chat CAD, um painel dentro do FreeCAD.
Fale sempre em português do Brasil, de forma curta e direta: o usuário é iniciante em CAD.

Ferramentas: use apenas as ferramentas do servidor MCP "freecad" (execute_code, get_view, get_objects...).
Não use terminal nem edite arquivos por fora. O FreeCAD já está aberto; o documento ativo é o
que o usuário está vendo.

Regras de modelagem:
- Modele SEMPRE de forma paramétrica. Toda medida importante vai numa planilha chamada "Parametros"
  (colunas Parametro / Valor / Descricao, com alias na coluna B) e os objetos usam expressões
  (setExpression) apontando para Parametros.<alias>. Nomes de parâmetros em português, sem acento.
- Antes de alterar um documento já salvo, salve uma cópia de backup ao lado:
  <nome>.antes-<acao>.FCStd (use shutil.copy do arquivo salvo).
- Depois de cada alteração: recompute, confira que não há objetos com erro, que o sólido final
  é válido e único (Shape.isValid(), len(Solids)==1), salve o documento e confira o resultado
  com get_view.
- Código exploratório (só para ler/inspecionar) deve ser à prova de erro: envolva em try/except
  e imprima o erro, em vez de deixar a exceção estourar (cada exceção abre uma janela de erro na
  frente do usuário). Na planilha, leia com sheet.getContents("B5") / sheet.getAlias("B5"), que
  devolvem vazio; NUNCA use sheet.get() numa célula que pode estar vazia.
- Esconda objetos auxiliares (ferramentas de corte etc.); só a peça final fica visível.
- Pense em impressão 3D: paredes >= 1.2 mm, evite lascas finas, avise sobre balanços grandes.
- Se a mensagem trouxer "Contexto da seleção", é o que o usuário clicou no FreeCAD: aplique a
  ação exatamente ali.

Ao terminar, responda em poucas linhas: o que mudou e qual parâmetro ajustar para mexer nisso.
"""

PLAN_NOTE = ("[Modo Planejar] Não altere a peça nem o documento. Só leia o que precisar, "
             "explique e proponha os passos; espere o usuário pedir para executar.\n\n")


def _which(names, patterns):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for pattern in patterns:
        hits = sorted(glob.glob(os.path.expandvars(pattern)))
        if hits:
            return hits[-1]
    return None


def find_uvx():
    return _which(["uvx"], [WINGET + r"\astral-sh.uv_*\uvx.exe"])


def mcp_server_cmd():
    """Comando do servidor MCP do FreeCAD."""
    import FreeCAD
    # o PATH do FreeCAD tem o Python 3.11 dele na frente; o freecad-mcp exige >= 3.12
    args = ["--python", ">=3.12", "freecad-mcp"]
    freecadcmd = os.path.normpath(os.path.join(FreeCAD.getHomePath(), "bin", "freecadcmd.exe"))
    if os.path.exists(freecadcmd):
        args += ["--freecadcmd", freecadcmd]
    return find_uvx() or "uvx", args


def clean_path(path):
    """Tira do PATH as pastas do próprio FreeCAD (Python 3.11 embutido, PySide...)."""
    import FreeCAD
    home = os.path.normcase(os.path.normpath(FreeCAD.getHomePath()))
    keep = [p for p in path.split(os.pathsep)
            if p and not os.path.normcase(os.path.normpath(p)).startswith(home)]
    return os.pathsep.join(keep)


def _mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".webp": "image/webp"}.get(ext, "image/png")


def attachments_dir():
    import FreeCAD
    path = os.path.join(FreeCAD.getUserAppDataDir(), "chatcad_anexos")
    os.makedirs(path, exist_ok=True)
    return path


def _failed(content):
    """O freecad-mcp devolve falhas como texto comum ("Failed to execute code: ...")."""
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
    else:
        return False
    return any(t.lstrip().startswith(("Failed to", "Error executing")) for t in texts)


def _images(content):
    """Extrai imagens (bytes) do conteúdo de um resultado de ferramenta MCP."""
    out = []
    if not isinstance(content, list):
        return out
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        data = item.get("data") or item.get("source", {}).get("data")
        if data:
            try:
                out.append(base64.b64decode(data))
            except Exception:
                pass
    return out


# ---------------------------------------------------------------------------
# provedores
# ---------------------------------------------------------------------------

class Provider:
    key = ""
    name = ""
    login_hint = ""

    def exe(self):
        raise NotImplementedError

    def models(self):
        """[(rótulo, valor)]; valor vazio = padrão do próprio CLI."""
        return [("Padrão", "")]

    def auth_args(self):
        """Argumentos de um comando rápido que diz se está logado (ou None)."""
        return None

    def auth_ok(self, out):
        return True

    def login_cmd(self):
        return [self.exe()]

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        """Devolve (args, env_extra, stdin_text)."""
        raise NotImplementedError

    def handle(self, ev, sink):
        """Traduz um evento JSON em chamadas no sink (ver Runner)."""
        raise NotImplementedError


class ClaudeProvider(Provider):
    key = "claude"
    name = "Claude Code"
    login_hint = "No terminal que abrir, digite /login e entre com a sua conta Claude."

    def exe(self):
        return _which(["claude"], [WINGET + r"\Anthropic.ClaudeCode_*\claude.exe",
                                   r"%USERPROFILE%\.local\bin\claude.exe",
                                   r"%APPDATA%\npm\claude.cmd"])

    def models(self):
        return [("Opus", "opus"), ("Sonnet", "sonnet"), ("Haiku", "haiku")]

    def auth_args(self):
        return ["auth", "status"]

    def auth_ok(self, out):
        try:
            return bool(json.loads(out).get("loggedIn"))
        except ValueError:
            return False

    def _mcp_config(self):
        import FreeCAD
        cmd, args = mcp_server_cmd()
        path = os.path.join(FreeCAD.getUserAppDataDir(), "chatcad_mcp.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"freecad": {"command": cmd, "args": args}}}, f)
        return path

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--strict-mcp-config", "--mcp-config", self._mcp_config(),
                "--allowedTools", "mcp__freecad",
                # (QProcess descarta argumentos vazios, então nada de --tools "")
                "--disallowedTools", "Bash,PowerShell,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task",
                "--append-system-prompt", SYSTEM_PROMPT]
        if model:
            args += ["--model", model]
        if plan:
            args += ["--permission-mode", "plan"]
        if session:
            args += ["--resume", session]
        # sem isso o -p começa antes do MCP conectar e o modelo fica sem as ferramentas do FreeCAD
        stdin = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for path in images:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": _mime(path), "data": data}})
            args += ["--input-format", "stream-json"]
            stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
        return args, {"MCP_CONNECTION_NONBLOCKING": "false"}, stdin

    def handle(self, ev, sink):
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            sink.session(ev.get("session_id"))
        elif kind == "stream_event":
            inner = ev.get("event", {})
            if inner.get("type") == "content_block_start" and \
                    inner.get("content_block", {}).get("type") == "text":
                sink.text_break()
            elif inner.get("type") == "content_block_delta":
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    sink.streamed = True
                    sink.text(delta.get("text", ""))
        elif kind == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    sink.tool_start(block.get("id", ""), block.get("name", ""))
                elif block.get("type") == "text" and not sink.streamed:
                    sink.text_break()
                    sink.text(block.get("text", ""))
        elif kind == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    sink.tool_end(block.get("tool_use_id", ""),
                                  bool(block.get("is_error")) or _failed(block.get("content")),
                                  _images(block.get("content")))
        elif kind == "result":
            sink.session(ev.get("session_id"))
            sink.done(bool(ev.get("is_error")), ev.get("result") or "")


class CodexProvider(Provider):
    key = "codex"
    name = "Codex"
    login_hint = "No terminal que abrir, entre com a sua conta do ChatGPT."

    def exe(self):
        return _which(["codex"], [WINGET + r"\OpenAI.Codex_*\codex-x86_64-pc-windows-msvc.exe",
                                  r"%APPDATA%\npm\codex.cmd"])

    def models(self):
        out = [("Padrão", "")]
        try:
            with open(os.path.expanduser(r"~\.codex\models_cache.json"), encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("models", [])[:6]:
                if m.get("slug") and "review" not in m["slug"]:
                    out.append((m.get("display_name") or m["slug"], m["slug"]))
        except Exception:
            pass
        return out

    def auth_args(self):
        return ["login", "status"]

    def auth_ok(self, out):
        return "logged in" in out.lower()

    def login_cmd(self):
        return [self.exe(), "login"]

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        cmd, mcp_args = mcp_server_cmd()
        args = ["exec"]
        if session:
            args += ["resume"]
        # -i logo após o subcomando: ele aceita vários valores e engoliria o "-" do stdin
        for path in images:
            args += ["-i", path]
        args += ["--json", "--skip-git-repo-check",
                 # MCP do FreeCAD: sempre carregado, ferramentas visíveis e liberadas
                 "--disable", "tool_search_always_defer_mcp_tools",
                 "-c", "mcp_servers.freecad.command=" + json.dumps(cmd),
                 "-c", "mcp_servers.freecad.args=" + json.dumps(mcp_args),
                 "-c", "mcp_servers.freecad.startup_timeout_sec=60",
                 "-c", "mcp_servers.freecad.tool_timeout_sec=300",
                 "-c", 'mcp_servers.freecad.default_tools_approval_mode="approve"',
                 "-c", "developer_instructions=" + json.dumps(SYSTEM_PROMPT)]
        if not session:
            args += ["-s", "read-only"]
            if cwd:
                args += ["-C", cwd]
        if model:
            args += ["-m", model]
        if session:
            args += [session]
        args += ["-"]
        return args, {}, (PLAN_NOTE if plan else "") + prompt

    def handle(self, ev, sink):
        kind = ev.get("type")
        item = ev.get("item") or {}
        itype = item.get("type")
        if kind == "thread.started":
            sink.session(ev.get("thread_id"))
        elif kind == "item.started" and itype in ("mcp_tool_call", "command_execution"):
            name = item.get("tool") or "terminal"
            sink.tool_start(item.get("id", ""), name)
        elif kind == "item.completed":
            if itype == "agent_message":
                sink.text_break()
                sink.text(item.get("text", ""))
            elif itype in ("mcp_tool_call", "command_execution"):
                result = item.get("result") or {}
                content = result.get("content") if isinstance(result, dict) else None
                sink.tool_end(item.get("id", ""), bool(item.get("error")) or _failed(content),
                              _images(result.get("content") if isinstance(result, dict) else None))
        elif kind == "turn.completed":
            sink.done(False, "")
        elif kind in ("turn.failed", "error"):
            err = ev.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            sink.done(True, msg or ev.get("message") or "O Codex falhou.")


class AntigravityProvider(Provider):
    key = "agy"
    name = "Antigravity"
    login_hint = "No terminal que abrir, entre com a sua conta Google."

    _models = None

    def exe(self):
        return _which(["agy"], [WINGET + r"\Google.AntigravityCLI_*\agy.exe",
                                r"%LOCALAPPDATA%\Antigravity\agy.exe"])

    def models(self):
        if AntigravityProvider._models is None:
            AntigravityProvider._models = [("Padrão", "")]
            exe = self.exe()
            if exe:
                import subprocess
                try:
                    out = subprocess.run([exe, "models"], capture_output=True, text=True,
                                         timeout=20, encoding="utf-8",
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                    for line in out.splitlines():
                        if "\t" in line:
                            slug, label = line.split("\t", 1)
                            AntigravityProvider._models.append((label.strip(), slug.strip()))
                except Exception:
                    pass
        return AntigravityProvider._models

    def build(self, prompt, model, plan, session, first_turn, cwd, images=()):
        text = (SYSTEM_PROMPT + "\n---\n\n" if first_turn else "") + (PLAN_NOTE if plan else "") + prompt
        if images:
            text += ("\n\nImagens anexadas pelo usuário (abra cada uma com view_file antes de agir; "
                     "rabiscos coloridos marcam onde ele quer a mudança):\n"
                     + "\n".join(f"- {p}" for p in images))
        args = ["-p", text, "--output-format", "stream-json"]
        for folder in sorted({os.path.dirname(p) for p in images}):
            args += ["--add-dir", folder]
        if model:
            args += ["--model", model]
        if plan:
            args += ["--mode", "plan"]
        if session:
            args += ["--conversation", session]
        return args, {}, None

    def handle(self, ev, sink):
        kind = ev.get("event")
        if kind == "init":
            sink.session(ev.get("conversation_id"))
        elif kind == "step_update":
            step = ev.get("step_update", {})
            idx = str(step.get("step_index"))
            stype = step.get("step_type")
            if stype == "agent_response":
                if sink.last_step != idx:
                    sink.last_step = idx
                    sink.text_break()
                if step.get("text_delta"):
                    sink.text(step["text_delta"])
            elif stype == "tool":
                info = step.get("tool_info") or {}
                params = info.get("parameters") or {}
                name = params.get("ToolName") or step.get("tool_name") or info.get("name") or "ferramenta"
                if idx not in sink.tools_seen:
                    sink.tools_seen.add(idx)
                    sink.tool_start(idx, name)
                if step.get("state") == "DONE":
                    sink.tool_end(idx, _failed(str(info.get("output", ""))), [])
        elif kind == "result":
            res = ev.get("result", {})
            sink.session(res.get("conversation_id"))
            denied = res.get("denied_actions")
            msg = res.get("response") or ""
            if denied:
                msg = "O Antigravity bloqueou uma ação por falta de permissão: " + \
                      ", ".join(d.get("display_name", "?") for d in denied)
            sink.done(res.get("status") != "SUCCESS" or bool(denied), msg)


PROVIDERS = [ClaudeProvider(), CodexProvider(), AntigravityProvider()]


def provider(key):
    return next((p for p in PROVIDERS if p.key == key), PROVIDERS[0])


# ---------------------------------------------------------------------------
# processos
# ---------------------------------------------------------------------------

class AuthCheck(QtCore.QObject):
    """Confere se o CLI escolhido está instalado e logado, sem travar a interface."""

    finished = QtCore.Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None

    def run(self, prov):
        self.prov = prov
        exe = prov.exe()
        if not exe:
            self.finished.emit(False, f"{prov.name} não está instalado neste computador.")
            return
        if prov.auth_args() is None:
            self.finished.emit(True, "")
            return
        self.proc = QtCore.QProcess(self)
        self.proc.finished.connect(self._done)
        self.proc.start(exe, prov.auth_args())

    def _done(self, *_):
        out = bytes(self.proc.readAllStandardOutput() + self.proc.readAllStandardError()).decode(
            "utf-8", "replace")
        ok = self.prov.auth_ok(out)
        self.finished.emit(ok, "" if ok else f"{self.prov.name} não está conectado.")


class ClaudeRunner(QtCore.QObject):
    """Uma conversa; cada envio é um processo headless que retoma a sessão anterior."""

    textDelta = QtCore.Signal(str)
    toolStarted = QtCore.Signal(str, str, dict)   # id, nome, input
    toolFinished = QtCore.Signal(str, bool, list)  # id, erro?, imagens
    turnBreak = QtCore.Signal()
    finished = QtCore.Signal(dict)                 # {"is_error": bool, "result": str}
    failed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prov = PROVIDERS[0]
        self.session_id = None
        self.turns = 0
        self.proc = None
        self._buf = b""
        self._stderr = b""
        self._done = False
        # estado usado pelos provedores durante um turno
        self.streamed = False
        self.last_step = None
        self.tools_seen = set()

    # chamadas usadas pelos provedores ------------------------------------------------
    def session(self, sid):
        if sid:
            self.session_id = sid

    def text_break(self):
        self.turnBreak.emit()

    def text(self, chunk):
        self.textDelta.emit(chunk)

    def tool_start(self, tid, name):
        self.toolStarted.emit(str(tid), name, {})

    def tool_end(self, tid, error, images):
        self.toolFinished.emit(str(tid), error, images)

    def done(self, error, msg):
        if not self._done:
            self._done = True
            self.finished.emit({"is_error": error, "result": msg})

    # controle --------------------------------------------------------------------
    def set_provider(self, prov):
        if prov is not self.prov:
            self.reset()
            self.prov = prov

    def busy(self):
        return self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning

    def reset(self):
        self.stop()
        self.session_id = None
        self.turns = 0

    def stop(self):
        if self.busy():
            self.proc.kill()

    def send(self, prompt, model="", plan=False, cwd=None, images=()):
        exe = self.prov.exe()
        if not exe:
            self.failed.emit(f"{self.prov.name} não está instalado.")
            return
        args, env_extra, stdin_text = self.prov.build(
            prompt, model, plan, self.session_id, self.turns == 0, cwd, list(images))
        self.turns += 1
        self._buf = b""
        self._stderr = b""
        self._done = False
        self.streamed = False
        self.last_step = None
        self.tools_seen = set()

        self.proc = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PATH", clean_path(env.value("PATH")))
        for k, v in env_extra.items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        if cwd and os.path.isdir(cwd):
            self.proc.setWorkingDirectory(cwd)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._exited)
        self.proc.start(exe, args)
        if stdin_text is not None:
            self.proc.write(stdin_text.encode("utf-8"))
        self.proc.closeWriteChannel()

    def _read_err(self):
        self._stderr = (self._stderr + bytes(self.proc.readAllStandardError()))[-20000:]

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"{"):
                continue
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            try:
                self.prov.handle(event, self)
            except Exception as e:  # um evento inesperado não pode derrubar a conversa
                self._stderr += f"\n[chatcad] evento ignorado: {e}".encode()

    def _exited(self, code, status):
        self._read()
        try:  # log do último turno, para depurar
            import FreeCAD
            with open(os.path.join(FreeCAD.getUserAppDataDir(), "chatcad_last.log"), "w",
                      encoding="utf-8") as f:
                f.write(f"{self.prov.name} exit={code}\n{self.proc.arguments()}\n\n")
                f.write(self._stderr.decode("utf-8", "replace"))
        except Exception:
            pass
        if self._done:
            return
        if status != QtCore.QProcess.NormalExit:
            self.done(True, "Interrompido.")
            return
        err = self._stderr.decode("utf-8", "replace").strip()
        if code != 0:
            self.failed.emit(err[-600:] or f"{self.prov.name} terminou com erro ({code}).")
            self._done = True
        else:
            self.done(False, "")
