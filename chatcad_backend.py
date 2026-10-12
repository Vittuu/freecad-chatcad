"""Roda agentes de terminal (Claude Code, Codex, Antigravity) em modo headless e traduz os
eventos de cada um em sinais Qt comuns para o painel."""

import base64
import glob
import json
import os
import shutil

try:
    from PySide import QtCore                       # shim do FreeCAD
except ImportError:                                 # testes offscreen fora do FreeCAD
    from PySide6 import QtCore

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
WINGET = r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"

SYSTEM_PROMPT = """Você é o agente de modelagem do Chat CAD, um painel dentro do FreeCAD.
Fale sempre em português do Brasil, de forma curta e direta: o usuário é iniciante em CAD.

Ferramentas: use apenas as ferramentas do servidor MCP "freecad" (execute_code, get_view, get_objects...).
Não use terminal nem edite arquivos por fora. O FreeCAD já está aberto; o documento ativo é o
que o usuário está vendo.

Documento: trabalhe SÓ no documento ativo (App.ActiveDocument, o "Documento ativo" do contexto).
- Nunca leia, altere, feche nem salve outros documentos abertos, mesmo que apareçam em
  list_documents; não troque o documento ativo.
- Não crie documento novo se já houver um ativo, a não ser que o usuário peça.
- Não salve o documento num caminho novo (saveAs, outra pasta, Documentos...) sem o usuário pedir.
  Se o documento ainda não tem arquivo, deixe-o sem salvar e diga que ele pode salvar quando quiser.

Regras de modelagem:
- Modele SEMPRE de forma paramétrica. Toda medida importante vai numa planilha chamada "Parametros"
  (colunas Parametro / Valor / Descricao, com alias na coluna B) e os objetos usam expressões
  (setExpression) apontando para Parametros.<alias>. Nomes de parâmetros em português, sem acento.
- Antes de alterar um documento já salvo, salve uma cópia de backup ao lado:
  <nome>.antes-<acao>.FCStd (use shutil.copy do arquivo salvo).
- Depois de cada alteração: recompute, confira que não há objetos com erro, que o sólido final
  é válido e único (Shape.isValid(), len(Solids)==1), salve o documento (só se ele já tem
  arquivo; nunca num caminho novo) e confira o resultado com get_view.
- Código exploratório (só para ler/inspecionar) deve ser à prova de erro: envolva em try/except
  e imprima o erro, em vez de deixar a exceção estourar (cada exceção abre uma janela de erro na
  frente do usuário). Na planilha, leia com sheet.getContents("B5") / sheet.getAlias("B5"), que
  devolvem vazio; NUNCA use sheet.get() numa célula que pode estar vazia.
- Esconda objetos auxiliares (ferramentas de corte etc.); só a peça final fica visível.
- Pense em impressão 3D: paredes >= 1.2 mm, evite lascas finas, avise sobre balanços grandes.
- Se a mensagem trouxer "Contexto da seleção", é o que o usuário clicou no FreeCAD: aplique a
  ação exatamente ali.
- Comece TODO execute_code com uma linha de comentário curta em português dizendo o que o passo
  faz (ex.: "# Cria a planilha Parametros"). O painel mostra essa linha como título do passo.

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


def _comma_free(path):
    """Caminho da imagem sem vírgula (o -i do codex divide nas vírgulas): copia para a pasta de
    anexos (ou a temporária) com um nome limpo. Se não der, devolve o original."""
    if "," not in path:
        return path
    import tempfile
    import time
    base = os.path.basename(path).replace(",", "_")
    for folder in (attachments_dir, tempfile.gettempdir):
        try:
            folder = folder()
            if "," in folder:
                continue
            stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
            dest = os.path.join(folder, f"{stamp}-{base}")
            while os.path.exists(dest):          # duas imagens com o mesmo nome no mesmo segundo
                n += 1
                dest = os.path.join(folder, f"{stamp}-{n}-{base}")
            shutil.copyfile(path, dest)
            return dest
        except Exception:
            continue
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


def _text_of(content, limit=4000):
    """Texto de um resultado de ferramenta (string, lista MCP de blocos ou dict), para o detalhe
    do passo no painel."""
    if content is None:
        return ""
    if isinstance(content, str):
        out = content
    elif isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "image":
                    parts.append("[imagem]")
                elif c.get("text") is not None:
                    parts.append(str(c.get("text")))
            elif isinstance(c, str):
                parts.append(c)
        out = "\n".join(parts)
    elif isinstance(content, dict):
        if "content" in content:
            return _text_of(content.get("content"), limit)
        out = json.dumps(content, ensure_ascii=False, indent=1)
    else:
        out = str(content)
    out = out.strip()
    return out if len(out) <= limit else out[:limit - 1] + "…"


def _args_of(value):
    """Entrada de uma ferramenta como dict (alguns CLIs mandam string JSON)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            j = json.loads(value)
            return j if isinstance(j, dict) else {"entrada": j}
        except ValueError:
            return {"entrada": value}
    return {}


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

    def cached_models(self):
        """Como models(), mas sem nada lento (menus montados na hora de abrir)."""
        return self.models()

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
                    sink.tool_start(block.get("id", ""), block.get("name", ""), _args_of(block.get("input")))
                elif block.get("type") == "text" and not sink.streamed:
                    sink.text_break()
                    sink.text(block.get("text", ""))
        elif kind == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    sink.tool_end(block.get("tool_use_id", ""),
                                  bool(block.get("is_error")) or _failed(block.get("content")),
                                  _images(block.get("content")), _text_of(block.get("content")))
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
        # -i logo após o subcomando: ele aceita vários valores e engoliria o "-" do stdin.
        # Um "-i <caminho>" por imagem; o codex ainda divide cada valor nas vírgulas
        # (value_delimiter=','), então caminho com vírgula vira uma cópia sem vírgula.
        for path in images:
            args += ["-i", _comma_free(path)]
        args += ["--json", "--skip-git-repo-check",
                 # MCP do FreeCAD: sempre carregado, ferramentas visíveis e liberadas
                 "--disable", "tool_search_always_defer_mcp_tools",
                 "-c", "mcp_servers.freecad.command=" + json.dumps(cmd),
                 "-c", "mcp_servers.freecad.args=" + json.dumps(mcp_args),
                 "-c", "mcp_servers.freecad.startup_timeout_sec=60",
                 "-c", "mcp_servers.freecad.tool_timeout_sec=300",
                 "-c", 'mcp_servers.freecad.default_tools_approval_mode="approve"',
                 "-c", "developer_instructions=" + json.dumps(SYSTEM_PROMPT),
                 # sandbox só leitura em TODO turno: o "exec resume" não aceita -s e, sem isto, a
                 # partir do 2º turno valeria o config (pasta "trusted" = workspace-write)
                 "-c", 'sandbox_mode="read-only"']
        if not session and cwd:
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
            inp = _args_of(item.get("arguments")) if itype == "mcp_tool_call" else \
                ({"comando": item["command"]} if item.get("command") else {})
            sink.tool_start(item.get("id", ""), name, inp)
        elif kind == "item.completed":
            if itype == "agent_message":
                sink.text_break()
                sink.text(item.get("text", ""))
            elif itype in ("mcp_tool_call", "command_execution"):
                result = item.get("result") or {}
                content = result.get("content") if isinstance(result, dict) else None
                err = item.get("error")
                if err:
                    output = err.get("message", "") if isinstance(err, dict) else str(err)
                elif itype == "command_execution":
                    output = _text_of(item.get("aggregated_output"))
                else:
                    output = _text_of(content)
                sink.tool_end(item.get("id", ""), bool(err) or _failed(content), _images(content), output)
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

    _fetch = None                              # QProcess de "agy models" em andamento

    @staticmethod
    def _parse_models(out):
        models = [("Padrão", "")]
        for line in out.splitlines():
            if "\t" in line:
                slug, label = line.split("\t", 1)
                models.append((label.strip(), slug.strip()))
        return models

    def models(self):
        """Síncrono (até 20 s na 1ª vez): o painel usa cached_models() + fetch_models()."""
        if AntigravityProvider._models is None:
            AntigravityProvider._models = [("Padrão", "")]
            exe = self.exe()
            if exe:
                import subprocess
                try:
                    out = subprocess.run([exe, "models"], capture_output=True, text=True,
                                         timeout=20, encoding="utf-8",
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                    AntigravityProvider._models = self._parse_models(out)
                except Exception:
                    pass
        return AntigravityProvider._models

    def cached_models(self):
        return AntigravityProvider._models or [("Padrão", "")]

    def fetch_models(self, parent, done):
        """Lista os modelos sem travar a interface; done() roda quando a lista chega.
        Devolve False se não há o que buscar (lista já pronta ou CLI ausente)."""
        if AntigravityProvider._models is not None:
            return False
        if AntigravityProvider._fetch is not None:
            return True
        exe = self.exe()
        if not exe:
            AntigravityProvider._models = [("Padrão", "")]
            return False
        proc = QtCore.QProcess(parent)
        AntigravityProvider._fetch = proc

        def finish(*_):
            if AntigravityProvider._fetch is not proc:
                return
            AntigravityProvider._fetch = None
            out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            AntigravityProvider._models = self._parse_models(out)
            proc.deleteLater()
            done()

        proc.finished.connect(finish)
        proc.errorOccurred.connect(lambda err: finish() if err == QtCore.QProcess.FailedToStart else None)
        QtCore.QTimer.singleShot(20000, proc, lambda: proc.kill()
                                 if proc.state() != QtCore.QProcess.NotRunning else None)
        proc.start(exe, ["models"])
        return True

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
            # o id também vem aqui: um turno interrompido antes do "result" ainda pode ser retomado
            sink.session(step.get("conversation_id"))
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
                    sink.tool_start(idx, name, {k: v for k, v in params.items() if k != "ToolName"})
                if step.get("state") == "DONE":
                    out = _text_of(info.get("output"))
                    sink.tool_end(idx, _failed(out), [], out)
        elif kind == "result":
            res = ev.get("result", {})
            sink.session(res.get("conversation_id"))
            denied = res.get("denied_actions")
            failed = res.get("status") != "SUCCESS"
            msg = res.get("response") or ""
            if denied:
                msg = "O Antigravity bloqueou uma ação por falta de permissão: " + \
                      ", ".join(d.get("display_name", "?") for d in denied)
            elif failed and not msg.strip():
                msg = self.error_message(res)
            sink.done(failed or bool(denied), msg)

    ERRORS_PT = {
        "there was a network issue connecting to the server, please try again.":
            "houve um problema de rede ao conectar ao servidor. Tente de novo.",
    }

    def error_message(self, res):
        """Mensagem em português para um "result" sem resposta (nunca só "Algo deu errado")."""
        err = res.get("error")
        if isinstance(err, dict):
            err = err.get("message") or err.get("detail") or json.dumps(err, ensure_ascii=False)
        err = str(err or "").strip()
        if not err:
            status = res.get("status") or "sem status"
            return f"O Antigravity falhou ({status}) sem explicar o motivo. Veja o log do último pedido."
        return "O Antigravity falhou: " + self.ERRORS_PT.get(err.lower(), err)


PROVIDERS = [ClaudeProvider(), CodexProvider(), AntigravityProvider()]

try:  # IA local (Ollama / servidor compatível com OpenAI), roda dentro do FreeCAD
    from chatcad_local import LocalProvider
    PROVIDERS.append(LocalProvider())
except Exception as _e:  # nunca derrubar os outros provedores por causa dele
    try:
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"Chat CAD: IA local indisponível: {_e}\n")
    except Exception:
        pass


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
        self.prov = None
        self.proc = None
        self._catalog = None
        self._gen = 0                  # cada run() é uma geração; resultado de geração velha é ignorado
        self._catalog_gen = -1

    def run(self, prov):
        self._gen += 1
        gen = self._gen
        self.prov = prov
        self._drop_proc()                        # um processo por vez; o anterior não fala mais
        if getattr(prov, "in_process", False):   # IA local: confere o servidor e lista os modelos
            from chatcad_local import Catalog
            if self._catalog is None or self._catalog.prov is not prov:
                self._catalog = Catalog(prov, self)
                self._catalog.ready.connect(lambda ok, msg, c=self._catalog: self._catalog_done(c, ok, msg))
            self._catalog_gen = gen
            self._catalog.refresh()
            return
        exe = prov.exe()
        if not exe:
            self.finished.emit(False, f"{prov.name} não está instalado neste computador.")
            return
        if prov.auth_args() is None:
            self.finished.emit(True, "")
            return
        proc = self.proc = QtCore.QProcess(self)
        # o provedor e o processo vão presos no slot: nunca ler self.prov/self.proc de outra geração
        proc.finished.connect(lambda *_, p=proc, pv=prov, g=gen: self._done(p, pv, g))
        proc.errorOccurred.connect(lambda err, p=proc, pv=prov, g=gen: self._error(p, pv, g, err))
        proc.start(exe, prov.auth_args())

    def _catalog_done(self, catalog, ok, msg):
        # resultado do catálogo de antes de trocar de provedor: não é deste provedor
        if catalog is self._catalog and self._catalog_gen == self._gen and self.prov is catalog.prov:
            self.finished.emit(ok, msg)

    def _drop_proc(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        for sig in (proc.finished, proc.errorOccurred):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        if proc.state() != QtCore.QProcess.NotRunning:
            proc.finished.connect(proc.deleteLater)
            proc.kill()
        else:
            proc.deleteLater()

    def _release(self, proc):
        if proc is self.proc:
            self.proc = None
        proc.deleteLater()

    def _done(self, proc, prov, gen):
        out = bytes(proc.readAllStandardOutput() + proc.readAllStandardError()).decode(
            "utf-8", "replace")
        self._release(proc)
        if gen != self._gen:
            return
        ok = prov.auth_ok(out)
        self.finished.emit(ok, "" if ok else f"{prov.name} não está conectado.")

    def _error(self, proc, prov, gen, err):
        # FailedToStart não vem seguido de finished: sem isto o status ficaria "verificando…" para sempre
        if err != QtCore.QProcess.FailedToStart:
            return
        msg = f"Não consegui iniciar o {prov.name}: {proc.errorString()}"
        self._release(proc)
        if gen == self._gen:
            self.finished.emit(False, msg)


class ClaudeRunner(QtCore.QObject):
    """Uma conversa; cada envio é um processo headless que retoma a sessão anterior."""

    textDelta = QtCore.Signal(str)
    toolStarted = QtCore.Signal(str, str, dict)   # id, nome, input
    toolFinished = QtCore.Signal(str, bool, list, str)  # id, erro?, imagens, saída (texto, até 4000)
    thinkingDelta = QtCore.Signal(str)             # raciocínio em streaming (se o agente mandar)
    turnBreak = QtCore.Signal()
    finished = QtCore.Signal(dict)                 # {"is_error": bool, "result": str}
    failed = QtCore.Signal(str)
    idle = QtCore.Signal()                         # o processo do CLI saiu de vez (busy() já é False)

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
        self._agent = None

    def _local(self):
        """Agente da IA local (criado na primeira vez), com os mesmos sinais deste runner."""
        if self._agent is None or self._agent.prov is not self.prov:
            from chatcad_local import LocalAgent
            self._agent = LocalAgent(self.prov, parent=self)
            for name in ("textDelta", "toolStarted", "turnBreak", "finished", "failed", "thinkingDelta"):
                if hasattr(self._agent, name):
                    getattr(self._agent, name).connect(getattr(self, name).emit)
            # o agente local manda (id, erro, imagens); a saída em texto é opcional
            self._agent.toolFinished.connect(
                lambda tid, err, imgs, out="": self.toolFinished.emit(tid, bool(err), list(imgs or []), out or ""))
        return self._agent

    def turn_stats(self):
        """Estatísticas do agente local: [(passo, {"output_tokens", "output_s", "prompt_tokens", ...})]."""
        return list(getattr(self._agent, "stats", None) or []) if self._in_process() and self._agent else []

    def _in_process(self):
        return getattr(self.prov, "in_process", False)

    def prewarm(self, model=""):
        if self._in_process():
            self._local().prewarm(model)

    # chamadas usadas pelos provedores ------------------------------------------------
    def session(self, sid):
        if sid:
            self.session_id = sid

    def text_break(self):
        self.turnBreak.emit()

    def text(self, chunk):
        self.textDelta.emit(chunk)

    def tool_start(self, tid, name, inp=None):
        self.toolStarted.emit(str(tid), name, dict(inp) if isinstance(inp, dict) else {})

    def tool_end(self, tid, error, images, output=""):
        self.toolFinished.emit(str(tid), bool(error), list(images or []), str(output or "")[:4000])

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
        if self._in_process():
            return self._local().busy()
        return self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning

    def reset(self):
        if self._agent is not None:
            self._agent.reset()
        self.stop()
        self._detach()
        self.session_id = None
        self.turns = 0

    def stop(self):
        if self._in_process():
            self._local().stop()
        elif self.busy():
            self._done = True               # quem parou já avisou: o fim do processo não vira "Interrompido."
            self._detach()

    def _detach(self):
        """Larga o processo atual: mata se ainda roda e ignora o que ele mandar depois.
        busy() fica False na hora (o kill() do QProcess só termina no próximo giro do loop)."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        for sig in (proc.readyReadStandardOutput, proc.readyReadStandardError, proc.finished):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        if proc.state() != QtCore.QProcess.NotRunning:
            proc.finished.connect(proc.deleteLater)
            proc.kill()
        else:
            proc.deleteLater()

    def send(self, prompt, model="", plan=False, cwd=None, images=(), use_selection=True):
        if self._in_process():
            # a IA local monta o contexto sozinha: precisa saber se o usuário ignorou a seleção
            self._local().send(prompt, model=model, plan=plan, cwd=cwd, images=images,
                               use_selection=use_selection)
            return
        exe = self.prov.exe()
        if not exe:
            self.failed.emit(f"{self.prov.name} não está instalado.")
            return
        # "primeiro turno" = ainda sem conversa para retomar (um turno que falhou antes de ter
        # id não conta: o próximo abre conversa nova e precisa do SYSTEM_PROMPT de novo)
        args, env_extra, stdin_text = self.prov.build(
            prompt, model, plan, self.session_id, not self.session_id, cwd, list(images))
        self.turns += 1
        self._buf = b""
        self._stderr = b""
        self._raw = []
        self._done = False
        self.streamed = False
        self.last_step = None
        self.tools_seen = set()

        self._detach()                      # nunca dois processos na mesma conversa
        proc = self.proc = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PATH", clean_path(env.value("PATH")))
        for k, v in env_extra.items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        if cwd and os.path.isdir(cwd):
            proc.setWorkingDirectory(cwd)
        proc.readyReadStandardOutput.connect(self._read)
        proc.readyReadStandardError.connect(self._read_err)
        proc.finished.connect(self._exited)
        proc.errorOccurred.connect(lambda err, p=proc: self._start_error(p, err))
        proc.start(exe, args)
        if self.proc is not proc:           # não iniciou (o _start_error já avisou e largou o processo)
            return
        if stdin_text is not None:
            proc.write(stdin_text.encode("utf-8"))
        proc.closeWriteChannel()

    def _start_error(self, proc, err):
        """FailedToStart (exe sumiu, linha de comando longa demais...) não emite finished: sem isto
        o turno ficaria pendurado, sem resposta nem erro."""
        if err != QtCore.QProcess.FailedToStart or proc is not self.proc:
            return
        msg = f"Não consegui iniciar o {self.prov.name}: {proc.errorString()}"
        self.turns = max(0, self.turns - 1)
        self._done = True
        for sig in (proc.readyReadStandardOutput, proc.readyReadStandardError, proc.finished,
                    proc.errorOccurred):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.proc = None
        proc.deleteLater()
        self.failed.emit(msg)

    def _read_err(self):
        if self.proc is not None:
            self._stderr = (self._stderr + bytes(self.proc.readAllStandardError()))[-20000:]

    def _read(self):
        if self.proc is None:
            return
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if line:
                self._raw = (self._raw + [line[:400]])[-40:]   # últimas linhas, para o log
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
                f.write("\n\n--- últimas linhas da saída ---\n")
                f.write("\n".join(r.decode("utf-8", "replace") for r in getattr(self, "_raw", [])))
        except Exception:
            pass
        if self._done:                      # o "result" chegou antes de o processo sair
            self.idle.emit()
            return
        if status != QtCore.QProcess.NormalExit:
            self.done(True, "Interrompido.")
            return
        err = self._stderr.decode("utf-8", "replace").strip()
        if code != 0:
            self.failed.emit(err[-600:] or f"{self.prov.name} terminou com erro ({code}).")
            self._done = True
        else:
            # saiu "com sucesso" mas sem o evento final (ex.: sem internet, CLI desatualizado):
            # nunca mostrar "concluído" vazio; diz o que o CLI escreveu por último
            last = next((r.decode("utf-8", "replace") for r in reversed(getattr(self, "_raw", []))
                         if not r.startswith(b"{")), "")
            detail = (err or last)[-400:]
            self.done(True, f"O {self.prov.name} terminou sem responder."
                            + (f" Mensagem: {detail}" if detail else " Confira a internet e o login, e tente de novo."))
