"""Partes puras (sem Qt e sem FreeCAD) do provedor de IA local: prompt, ferramentas, leitura dos
streams (Ollama NDJSON e OpenAI SSE), conserto de argumentos e controle do histórico.
Testável com qualquer Python 3.9+ (python -m unittest tests.test_llm)."""

import ast
import difflib
import json
import re

# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_LOCAL = """Você é o agente de modelagem do Chat CAD, dentro do FreeCAD. Responda em português do Brasil, curto: o usuário é iniciante em CAD.

Como trabalhar:
1. Para criar ou mudar a peça, chame run_python. Nunca mostre código ao usuário. Peça com várias partes: construa em etapas, uma chamada run_python por etapa com no máximo 40 linhas (os objetos e variáveis continuam existindo entre as chamadas).
2. Leia o resultado. Se vier ERRO, corrija e chame de novo (o passo com erro já foi desfeito).
3. Para só mudar uma medida que já existe, use set_parameter.
4. No fim, diga em 1 a 3 linhas o que mudou e qual parâmetro da planilha Parametros ajustar (não cite nomes de ferramentas).

Regras:
- Sempre paramétrico: crie cada medida com param("nome", valor) e ligue nas propriedades com liga(objeto, Propriedade="expressão"). Em liga, x=, y=, z= posicionam o objeto e angulo= gira em graus (eixo Z).
- Nomes de parâmetros em português, minúsculos, sem acento (largura, furo_d). Medidas em mm.
- Já existem: App, Part, Vector, math, doc (documento ativo), param, liga, sel (seleção do usuário).
- Cada objeto novo com nome próprio; nunca use o mesmo objeto como Base e Tool.
- O sistema recalcula, confere o sólido, esconde objetos auxiliares e faz backup sozinho.
- Impressão 3D: paredes de pelo menos 1.2 mm.

Exemplo: placa 60x40x4 com dois furos de 4 mm a 8 mm das bordas.
param("largura", 60); param("profundidade", 40); param("espessura", 4)
param("furo_d", 4); param("margem", 8)
placa = doc.addObject("Part::Box", "Placa")
liga(placa, Length="largura", Width="profundidade", Height="espessura")
furo1 = doc.addObject("Part::Cylinder", "Furo1")
liga(furo1, Radius="furo_d / 2", Height="espessura", x="margem", y="profundidade / 2")
furo2 = doc.addObject("Part::Cylinder", "Furo2")
liga(furo2, Radius="furo_d / 2", Height="espessura", x="largura - margem", y="profundidade / 2")
furos = doc.addObject("Part::MultiFuse", "Furos")
furos.Shapes = [furo1, furo2]
peca = doc.addObject("Part::Cut", "Peca")
peca.Base = placa
peca.Tool = furos

Referência:
- Part::Box (Length, Width, Height; canto em x, y, z), Part::Cylinder (Radius, Height; centro da base em x, y, z), Part::Sphere (Radius), Part::Cone (Radius1, Radius2, Height)
- Part::Cut, Part::Fuse, Part::Common (Base, Tool). Um furo só: peca.Tool = furo. Part::MultiFuse só com 2 ou mais (Shapes = [a, b])
- Arredondar: arredonda(obj, 10, arestas(obj, eixo="y", z=95)) cria o arredondamento (raio 10) nas arestas paralelas a Y que estão em z=95; arestas(obj, eixo=..., x=/y=/z=...) acha os números das arestas pela posição. Depois use o objeto devolvido como Base do próximo passo.
- Furo no centro de um cilindro: os dois cilindros ficam em x=0, y=0 (não passe x, y).
- Girar em X ou Y: obj.Placement.Rotation = App.Rotation(Vector(1, 0, 0), 90)
"""

PLAN_NOTE_LOCAL = ("[Modo Planejar] Não altere a peça. Use só get_document"
                   " para ler, explique e proponha os passos.\n\n")

CONTEXT_MARK = "\n--- pedido ---\n"   # separa o contexto automático do texto do usuário

NUDGE_SHOWED_CODE = ("Você mostrou código em vez de executar. Chame a ferramenta run_python com esse "
                     "código agora. Não mostre código ao usuário.")
NUDGE_VERIFY = ("Antes de terminar: a verificação da peça acusou {problemas}. Corrija com run_python "
                "(sem recriar o que já está certo). Se for de propósito, explique ao usuário em uma linha.")
NUDGE_VERIFY_AGAIN = ("Nada mudou: você não chamou run_python desde o aviso, e a peça continua com {problemas}. "
                      "Chame run_python agora com o código que corrige isso. Não diga que corrigiu sem executar. "
                      "Se for de propósito, explique ao usuário em uma linha, sem dizer que corrigiu.")
NUDGE_DO_IT = ("Você disse o que ia fazer, mas não executou nada. Chame run_python agora para fazer isso "
               "(não anuncie de novo: execute). Se já terminou tudo o que o usuário pediu, responda só com o "
               "resumo final.")

_PROMISE = re.compile(
    r"\b(vou|irei|vamos)\s+(agora\s+|então\s+|primeiro\s+|em seguida\s+)?"
    r"(criar|fazer|adicionar|cortar|arredondar|furar|unir|juntar|posicionar|ajustar|aplicar|remover|abrir|"
    r"montar|gerar|construir|colocar|recortar|subtrair|desenhar|modelar|corrigir|refazer|executar|rodar)\b"
    r"|\b(now i will|now i'll|next,? i will|next,? i'll|let me now)\b", re.I)
_OFFER = re.compile(r"\b(se quiser|se preferir|posso|quer que|deseja|gostaria)\b", re.I)
_DONE = re.compile(r"\b(pronto|conclu[ií]d|finaliz|terminei|feito)\b", re.I)


def announces_action(text):
    """O texto final termina prometendo uma ação ("Agora vou criar a abertura...") sem ter executado?
    Modelos pequenos fazem muito isso e encerram a vez. Ofertas ("se quiser, posso...") não contam."""
    tail = (text or "").strip()[-260:]
    if not tail or _OFFER.search(tail):
        return False
    last = re.split(r"(?<=[.!?])\s+", tail)[-1] if re.search(r"[.!?]\s+\S", tail) else tail
    m = _PROMISE.search(last) or _PROMISE.search(tail[-120:])
    return bool(m) and not _DONE.search(last)


NUDGE_TOO_LONG = ("Sua resposta passou do limite de tamanho e foi cortada, então nada foi executado. "
                  "Divida o trabalho em etapas: chame run_python agora só com a PRÓXIMA etapa que ainda falta "
                  "(no máximo 40 linhas; não refaça o que já foi criado). Depois faça as próximas etapas em "
                  "chamadas separadas; os objetos e as variáveis continuam existindo entre as chamadas.")
NUDGE_THINK_LESS = ("Seu raciocínio passou do limite de tamanho e foi cortado antes de você responder, então "
                    "nada foi executado. Pense menos: responda direto ou chame run_python agora com a próxima "
                    "etapa que ainda falta (no máximo 40 linhas).")
NUDGE_BAD_CALL = ("Sua última chamada de ferramenta veio errada ({erro}). Chame de novo com argumentos "
                  "JSON válidos, por exemplo {exemplo}.")

# ---------------------------------------------------------------------------
# ferramentas
# ---------------------------------------------------------------------------

_RUN_PYTHON = {"type": "function", "function": {
    "name": "run_python",
    "description": ("Executa código Python dentro do FreeCAD (documento ativo em doc). Use para criar "
                    "ou alterar a peça. Devolve a saída de print, erros e a verificação da peça."),
    "parameters": {"type": "object", "properties": {
        "code": {"type": "string", "description": "Código Python completo. Use param() e liga() para as medidas."},
        "resumo": {"type": "string", "description": "O que este passo faz, em até 6 palavras."}},
        "required": ["code"]}}}

_GET_DOCUMENT = {"type": "function", "function": {
    "name": "get_document",
    "description": ("Mostra o documento: parâmetros da planilha, objetos (tipo, visível, medidas, erros) e a "
                    "seleção. Com objeto, lista as faces e arestas numeradas desse objeto."),
    "parameters": {"type": "object", "properties": {
        "objeto": {"type": "string", "description": "Nome de um objeto para ver em detalhe (opcional)."}},
        "required": []}}}

_SET_PARAMETER = {"type": "function", "function": {
    "name": "set_parameter",
    "description": "Muda (ou cria) uma medida da planilha Parametros e recalcula a peça.",
    "parameters": {"type": "object", "properties": {
        "nome": {"type": "string", "description": "Alias do parâmetro, ex.: largura"},
        "valor": {"type": "string", "description": "Novo valor, ex.: '45 mm', '30 deg' ou '6'"},
        "descricao": {"type": "string", "description": "Descrição curta (só ao criar)."}},
        "required": ["nome", "valor"]}}}

_VIEW_SCREENSHOT = {"type": "function", "function": {
    "name": "view_screenshot",
    "description": "Tira uma foto da vista 3D enquadrada, para conferir a peça visualmente.",
    "parameters": {"type": "object", "properties": {
        "vista": {"type": "string", "enum": ["isometrica", "frente", "topo", "direita"],
                  "description": "Direção da vista (padrão: isometrica)."}},
        "required": []}}}

MUTATING = {"run_python", "set_parameter"}
REQUIRED = {"run_python": ("code",), "get_document": (), "set_parameter": ("nome", "valor"),
            "view_screenshot": ()}
EXAMPLES = {"run_python": '{"code": "caixa = doc.addObject(\\"Part::Box\\", \\"Caixa\\")"}',
            "get_document": '{}', "set_parameter": '{"nome": "largura", "valor": "45 mm"}',
            "view_screenshot": '{"vista": "isometrica"}'}


def tool_schemas(vision=False):
    """Lista fixa por conversa (mudar a lista invalida o cache de prompt do Ollama)."""
    tools = [_RUN_PYTHON, _GET_DOCUMENT, _SET_PARAMETER]
    if vision:
        tools.append(_VIEW_SCREENSHOT)
    return tools


TOOL_ALIASES = {
    "execute_python": "run_python", "execute_code": "run_python", "executar_python": "run_python",
    "python": "run_python", "run_code": "run_python", "exec": "run_python", "code": "run_python",
    "get_document_info": "get_document", "get_objects": "get_document", "ver_documento": "get_document",
    "document": "get_document", "get_selection": "get_document", "list_objects": "get_document",
    "set_param": "set_parameter", "definir_parametro": "set_parameter", "update_parameter": "set_parameter",
    "get_view": "view_screenshot", "screenshot": "view_screenshot",
}

ARG_ALIASES = {
    "run_python": {"code": ("code", "codigo", "código", "python", "script", "source", "command", "cmd"),
                   "resumo": ("resumo", "summary", "descricao", "description")},
    "get_document": {"objeto": ("objeto", "object", "obj", "name", "nome")},
    "set_parameter": {"nome": ("nome", "name", "alias", "parametro", "parâmetro", "parameter"),
                      "valor": ("valor", "value", "novo_valor", "new_value"),
                      "descricao": ("descricao", "descrição", "description")},
    "view_screenshot": {"vista": ("vista", "view", "view_name")},
}


def resolve_tool(name, allowed):
    name = (name or "").strip().split(".")[-1].split("__")[-1]
    if name in allowed:
        return name
    alias = TOOL_ALIASES.get(name.lower())
    if alias in allowed:
        return alias
    close = difflib.get_close_matches(name, sorted(allowed), n=1, cutoff=0.75)
    return close[0] if close else None


def parse_args(raw):
    """Argumentos de uma chamada -> (dict, "") ou (None, motivo). Tolera os erros típicos de modelos
    pequenos: cercas ```json, quebras de linha cruas dentro de strings, vírgula sobrando, JSON
    duplamente codificado e dict no formato Python."""
    if isinstance(raw, dict):
        return raw, ""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, ""
    if not isinstance(raw, str):
        return None, f"argumentos do tipo {type(raw).__name__}"
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    if m:
        s = m.group(1)
    for cand in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            value = json.loads(cand, strict=False)   # strict=False aceita \n cru dentro de strings
        except ValueError:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value, strict=False)
            except ValueError:
                pass
        if isinstance(value, dict):
            return value, ""
    try:
        value = ast.literal_eval(s)
        if isinstance(value, dict):
            return value, ""
    except (ValueError, SyntaxError):
        pass
    return None, "JSON inválido"


def _looks_like_python(s):
    return bool(re.search(r"\b(doc|App|Part|FreeCAD)\.|addObject|param\(|liga\(", s or ""))


def _clean_code(code):
    code = str(code)
    m = re.match(r"^\s*```(?:python|py)?\s*\n(.*?)\n?```\s*$", code, re.S)
    if m:
        code = m.group(1)
    if "\n" not in code and "\\n" in code:          # modelo escapou duas vezes
        code = code.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "    ")
    return code


def normalize_call(name, raw_args, allowed):
    """-> (ferramenta | None, args, erro). erro != "" quer dizer: não executar, devolver o erro."""
    tool = resolve_tool(name, allowed)
    if not tool:
        return None, {}, f"a ferramenta '{name}' não existe; use uma destas: {', '.join(sorted(allowed))}"
    args, err = parse_args(raw_args)
    if args is None:
        if tool == "run_python" and isinstance(raw_args, str) and _looks_like_python(raw_args):
            args = {"code": raw_args}
        else:
            return tool, {}, f"argumentos inválidos ({err})"
    for wrapper in ("arguments", "parameters", "args", "input"):
        if set(args) == {wrapper}:
            inner, _ = parse_args(args[wrapper])
            if inner is not None:
                args = inner
    canon = {}
    aliases = ARG_ALIASES.get(tool, {})
    for key, names in aliases.items():
        for n in names:
            if n in args and args[n] not in (None, ""):
                canon[key] = args[n]
                break
    if tool == "run_python" and "code" not in canon:
        strings = [v for v in args.values() if isinstance(v, str) and _looks_like_python(v)]
        if len(strings) == 1:
            canon["code"] = strings[0]
    if "code" in canon:
        canon["code"] = _clean_code(canon["code"])
    for key in ("valor", "nome", "descricao", "resumo", "objeto", "vista"):
        if key in canon and not isinstance(canon[key], str):
            canon[key] = json.dumps(canon[key]) if isinstance(canon[key], (list, dict)) else str(canon[key])
    missing = [k for k in REQUIRED[tool] if not str(canon.get(k, "")).strip()]
    if missing:
        return tool, canon, f"faltou o campo {missing[0]}"
    return tool, canon, ""


_TEXT_CALL_PATTERNS = (
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S),
    re.compile(r"```(?:json)?\s*(\{\s*\"name\".*?\})\s*```", re.S),
)


_XML_CALL = re.compile(r"(?:<tool_call>\s*)?<function=([\w.\-]+)>(.*?)</function>(?:\s*</tool_call>)?", re.S)
_XML_PARAM = re.compile(r"<parameter=([\w\-]+)>\n?(.*?)\n?</parameter>", re.S)


def _xml_calls(text):
    """Formato estilo Qwen3-Coder/Qwen3.5 que às vezes vaza como texto:
    <function=run_python><parameter=code>...</parameter></function>
    e o do Llama 3.x (ferramenta própria): <function=run_python>{"code": "..."}</function>"""
    out = []
    for m in _XML_CALL.finditer(text or ""):
        params = _XML_PARAM.findall(m.group(2))
        if params:
            args = {k: v for k, v in params}
        else:                                         # corpo em JSON (Llama 3.x)
            obj, _ = parse_args(m.group(2).strip())
            args = obj if isinstance(obj, dict) else {}
            inner = args.get("arguments", args.get("parameters")) if "name" in args else None
            if inner is not None:                     # {"name": ..., "arguments": {...}}
                inner, _ = parse_args(inner)
                args = inner if isinstance(inner, dict) else {}
        out.append((m.group(0), m.group(1), args))
    return out


def extract_text_calls(text, allowed):
    """Chamadas que o modelo escreveu como texto (o servidor não reconheceu). -> (calls, texto_limpo)"""
    calls = []
    cleaned = text or ""
    for whole, name, args in _xml_calls(cleaned):
        if resolve_tool(name, allowed):
            calls.append({"id": "", "name": name, "arguments": args})
            cleaned = cleaned.replace(whole, "")
    if calls:
        return calls, cleaned.strip()
    candidates = []
    for pat in _TEXT_CALL_PATTERNS:
        for m in pat.finditer(cleaned):
            candidates.append((m.group(0), m.group(1)))
    stripped = cleaned.strip()
    if not candidates and stripped.startswith("{") and stripped.endswith("}"):
        candidates.append((stripped, stripped))
    for whole, body in candidates:
        obj, _ = parse_args(body)
        if not isinstance(obj, dict) or not obj.get("name"):
            continue
        if not resolve_tool(obj["name"], allowed):
            continue
        calls.append({"id": "", "name": obj["name"],
                      "arguments": obj.get("arguments", obj.get("parameters", {}))})
        cleaned = cleaned.replace(whole, "")
    return calls, cleaned.strip()


_CODE_WORD = re.compile(r"c[óo]digo|\bscript|\bmacro|\bpython\b|snippet", re.I)
_SEE_VERB = re.compile(r"\b(mostr|exib|escrev|ensin|explic|imprim)\w*|\bme (d[êáa]|pass[ae]|mand[ae])\b|"
                       r"\bver\b|\bveja\b|\bcomo (seria|fica|ficaria|[ée]|fa[çz]o)\b", re.I)
_RUN_VERB = re.compile(r"\b(execut|rod[ae]\b|rodar|aplic)\w*", re.I)


def asks_to_see_code(prompt):
    """O usuário pediu para VER código ("mostre o código para..."): aí o bloco ``` da resposta é a
    resposta, não uma chamada esquecida, e não pode ser executado sozinho."""
    p = (prompt or "").split(CONTEXT_MARK)[-1]
    return bool(_CODE_WORD.search(p) and _SEE_VERB.search(p) and not _RUN_VERB.search(p))


_INTENT = re.compile(r"prop[óo]sit|intencional|separad|como (voc[êe] )?pediu|j[áa] (existia|estava)|"
                     r"mantive|mantenho|deixei", re.I)
_CLAIM = re.compile(r"corrig|consert|arrum|resolv|ajustei|pronto|feito", re.I)


def explains_warning(text):
    """Resposta (sem chamar ferramenta) ao aviso da verificação: explicação honesta ("é de propósito",
    "ficam separadas") é aceita; "corrigi"/"pronto" sem executar nada, ou resposta vazia, não."""
    t = (text or "").strip()
    return bool(t) and (bool(_INTENT.search(t)) or not _CLAIM.search(t))


def warning_items(warn):
    """"a; b; N peças...; se o pedido..." -> itens (a dica "se ..." fica junto do item anterior)."""
    out = []
    for part in (warn or "").split("; "):
        part = part.strip()
        if not part:
            continue
        if out and part.startswith("se "):
            out[-1] += "; " + part
        else:
            out.append(part)
    return out


def new_warnings(warn, baseline):
    """Só os problemas que não existiam no começo do turno ("nenhuma peça visível" sempre conta)."""
    old = set(baseline or ())
    return "; ".join(p for p in warning_items(warn) if p not in old or p == "nenhuma peça visível")


def showed_code_instead_of_calling(text):
    return bool(code_from_text(text)[0])


def code_from_text(text):
    """Bloco ```python com código do FreeCAD que o modelo "mostrou" em vez de executar.
    -> (código, texto_sem_os_blocos). Modelos pequenos fazem isso muito (qwen2.5:7b)."""
    every = list(re.finditer(r"```(?:python|py)?[ \t]*\n(.*?)```", text or "", re.S))
    blocks = [m for m in every if _looks_like_python(m.group(1))]
    if not blocks:
        return "", text or ""
    code = blocks[-1].group(1).strip()          # o último costuma ser a versão corrigida
    cleaned = text
    for m in every:
        cleaned = cleaned.replace(m.group(0), "")
    return code, re.sub(r"\n{3,}", "\n\n", cleaned).strip()


class HideTags:
    """Esconde chamadas de ferramenta que vazam como texto (<tool_call>…</tool_call>,
    <function=…>…</function>) enquanto o texto chega aos pedaços; elas são executadas no fim do passo."""

    PAIRS = (("<tool_call>", "</tool_call>"), ("<function=", "</function>"))

    def __init__(self):
        self.close = None          # fechamento esperado quando está dentro de um bloco
        self.pending = ""

    def feed(self, s):
        s = self.pending + s
        self.pending = ""
        out = []
        while s:
            if self.close is None:
                hits = [(s.find(o), o, c) for o, c in self.PAIRS if s.find(o) >= 0]
                if hits:
                    i, o, c = min(hits)
                    out.append(s[:i])
                    s = s[i + len(o):]
                    self.close = c
                    continue
                keep = max((n for o, _ in self.PAIRS for n in range(1, len(o))
                            if s.endswith(o[:n])), default=0)
                out.append(s[:len(s) - keep])
                self.pending = s[len(s) - keep:]
                break
            i = s.find(self.close)
            if i >= 0:
                s = s[i + len(self.close):]
                self.close = None
                continue
            keep = max((n for n in range(1, len(self.close)) if s.endswith(self.close[:n])), default=0)
            self.pending = s[len(s) - keep:]
            break
        return "".join(out)


class FenceFilter:
    """Esconde blocos ``` enquanto o texto chega aos pedaços (no modo Agente o usuário não vê código)."""

    def __init__(self):
        self.inside = False
        self.pending = ""

    def feed(self, s):
        s = self.pending + s
        self.pending = ""
        out = []
        while s:
            i = s.find("```")
            if i < 0:
                keep = 2 if s.endswith("``") else (1 if s.endswith("`") else 0)
                if not self.inside:
                    out.append(s[:len(s) - keep])
                self.pending = s[len(s) - keep:]
                break
            if not self.inside:
                out.append(s[:i])
            self.inside = not self.inside
            s = s[i + 3:]
        return "".join(out)

    def flush(self):
        rest, self.pending = self.pending, ""
        return "" if self.inside else rest


# ---------------------------------------------------------------------------
# streams
# ---------------------------------------------------------------------------

class NdjsonStream:
    """Ollama /api/chat com stream=true: uma linha JSON por pedaço.
    Eventos: ("think", s) ("text", s) ("tool", {id,name,arguments}) ("done", info) ("error", msg)."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        out = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            out += self._line(line)
        return out

    def flush(self):
        line, self.buf = self.buf, b""
        return self._line(line)

    def _line(self, line):
        line = line.strip()
        if not line:
            return []
        try:
            ev = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return [("error", "resposta inválida do servidor: " + line[:200].decode("utf-8", "replace"))]
        if not isinstance(ev, dict):
            return []
        if ev.get("error"):
            return [("error", str(ev["error"]))]
        out = []
        msg = ev.get("message") or {}
        if msg.get("thinking"):
            out.append(("think", msg["thinking"]))
        if msg.get("content"):
            out.append(("text", msg["content"]))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            out.append(("tool", {"id": tc.get("id") or "", "name": fn.get("name") or "",
                                 "arguments": fn.get("arguments")}))
        if ev.get("done"):
            out.append(("done", {
                "reason": ev.get("done_reason") or "stop",
                "prompt_tokens": ev.get("prompt_eval_count") or 0,
                "cached_tokens": ev.get("prompt_eval_cached_count") or 0,
                "output_tokens": ev.get("eval_count") or 0,
                "prompt_s": (ev.get("prompt_eval_duration") or 0) / 1e9,
                "output_s": (ev.get("eval_duration") or 0) / 1e9,
                "load_s": (ev.get("load_duration") or 0) / 1e9}))
        return out


class SseStream:
    """OpenAI /v1/chat/completions com stream=true (LM Studio, llama.cpp, vLLM, Ollama /v1).
    Os argumentos das ferramentas chegam em pedaços de string por índice; só são emitidos no fim."""

    def __init__(self):
        self.buf = b""
        self.calls = {}
        self.reason = None
        self.usage = {}
        self.finished = False

    def feed(self, data):
        self.buf += data
        out = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            out += self._line(line)
        return out

    def flush(self):
        out = self._line(self.buf)
        self.buf = b""
        if not self.finished:
            out += self._finish()
        return out

    def _line(self, line):
        line = line.strip()
        if not line.startswith(b"data:") or self.finished:
            return []
        payload = line[5:].strip()
        if payload == b"[DONE]":
            return self._finish()
        try:
            ev = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return []
        if ev.get("error"):
            err = ev["error"]
            return [("error", err.get("message", str(err)) if isinstance(err, dict) else str(err))]
        if ev.get("usage"):
            self.usage = ev["usage"]
        out = []
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or ch.get("message") or {}
            reasoning = d.get("reasoning_content") or d.get("reasoning")
            if reasoning:
                out.append(("think", reasoning))
            if d.get("content"):
                out.append(("text", d["content"]))
            for tc in d.get("tool_calls") or []:
                idx = tc.get("index", len(self.calls))
                slot = self.calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                slot["id"] = slot["id"] or tc.get("id") or ""
                fn = tc.get("function") or {}
                slot["name"] = slot["name"] or fn.get("name") or ""
                args = fn.get("arguments")
                if isinstance(args, str):
                    slot["arguments"] += args
                elif isinstance(args, dict):
                    slot["arguments"] = json.dumps(args, ensure_ascii=False)
            if ch.get("finish_reason"):
                self.reason = ch["finish_reason"]
        return out

    def _finish(self):
        self.finished = True
        out = [("tool", dict(self.calls[i])) for i in sorted(self.calls)]
        out.append(("done", {"reason": {"tool_calls": "stop"}.get(self.reason, self.reason or "stop"),
                             "prompt_tokens": self.usage.get("prompt_tokens", 0),
                             "cached_tokens": 0,
                             "output_tokens": self.usage.get("completion_tokens", 0)}))
        return out


class ThinkSplitter:
    """Separa <think>...</think> que vem misturado no conteúdo (servidores que não separam o
    raciocínio). Aguenta a tag partida entre dois pedaços."""

    def __init__(self, inside=False):
        self.inside = inside
        self.pending = ""

    def feed(self, s):
        s = self.pending + s
        self.pending = ""
        out = []
        while s:
            tag = "</think>" if self.inside else "<think>"
            i = s.find(tag)
            if i >= 0:
                if i:
                    out.append(("think" if self.inside else "text", s[:i]))
                s = s[i + len(tag):]
                self.inside = not self.inside
                continue
            keep = 0
            for n in range(min(len(tag) - 1, len(s)), 0, -1):
                if tag.startswith(s[-n:]):
                    keep = n
                    break
            body = s[:len(s) - keep]
            if body:
                out.append(("think" if self.inside else "text", body))
            self.pending = s[len(s) - keep:]
            break
        return out

    def flush(self):
        rest, self.pending = self.pending, ""
        return [("think" if self.inside else "text", rest)] if rest else []


# ---------------------------------------------------------------------------
# histórico
# ---------------------------------------------------------------------------

def truncate(text, limit=1800):
    """Corta no meio (o fim de um traceback é o que importa)."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n…[{cut} caracteres cortados]…\n{text[-tail:]}"


def estimate_tokens(messages):
    total = 0
    for m in messages:
        # o raciocínio volta ao servidor (to_ollama) e os modelos com thinking o renderizam
        total += 6 + (len(m.get("content") or "") + len(m.get("thinking") or "")) // 3
        for c in m.get("tool_calls") or []:
            args = c.get("arguments")
            total += 10 + len(args if isinstance(args, str) else json.dumps(args or {})) // 3
        total += 260 * len(m.get("images") or [])
    return total


def compact(messages, budget):
    """Encolhe o histórico até caber em `budget` tokens, sem deixar tool_calls sem resposta.
    0) tira o raciocínio ("thinking") de todas as respostas, menos da última;
    1) turnos antigos viram só (pedido do usuário, resposta final), sem imagens nem ferramentas;
    2) apaga os turnos antigos mais velhos; 3) no turno atual, esvazia resultados antigos."""
    if estimate_tokens(messages) <= budget or len(messages) < 3:
        return messages
    last = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    if any(m.get("thinking") for i, m in enumerate(messages) if i != last):
        messages = [{k: v for k, v in m.items() if k != "thinking"} if (i != last and m.get("thinking")) else m
                    for i, m in enumerate(messages)]
        if estimate_tokens(messages) <= budget:
            return messages
    system, rest = messages[0], messages[1:]
    starts = [i for i, m in enumerate(rest) if m["role"] == "user" and not m.get("_nudge")]
    if not starts:
        return messages
    current = rest[starts[-1]:]
    old_turns = [rest[a:b] for a, b in zip(starts[:-1], starts[1:])]
    slim = []
    for turn in old_turns:
        user = dict(turn[0])
        user.pop("images", None)
        user["content"] = (user.get("content") or "").split(CONTEXT_MARK)[-1]
        finals = [m for m in turn if m["role"] == "assistant" and not m.get("tool_calls")
                  and (m.get("content") or "").strip()]
        pair = [user]
        if finals:
            pair.append({"role": "assistant", "content": finals[-1]["content"]})
        slim.append(pair)
    out = [system] + [m for pair in slim for m in pair] + current
    while slim and estimate_tokens(out) > budget:
        slim.pop(0)
        out = [system] + [m for pair in slim for m in pair] + current
    if estimate_tokens(out) > budget:
        tool_idx = [i for i, m in enumerate(out) if m["role"] == "tool"]
        for i in tool_idx[:-2]:
            out[i] = dict(out[i], content="[resultado antigo omitido para caber na memória do modelo]")
    return out


# ---------------------------------------------------------------------------
# formatos de mensagem por servidor
# ---------------------------------------------------------------------------
# formato interno:
#   {"role": "user", "content": str, "images": [b64...]}
#   {"role": "assistant", "content": str, "thinking": str, "tool_calls": [{"id","name","arguments": dict}]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}

def to_ollama(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("thinking"):
                msg["thinking"] = m["thinking"]
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}}
                                     for c in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", ""),
                        "tool_call_id": m.get("tool_call_id", "")})
        else:
            msg = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("images"):
                msg["images"] = list(m["images"])
            out.append(msg)
    return out


def to_openai(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                    "name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                    for c in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m["content"]})
        elif m.get("images"):
            parts = [{"type": "text", "text": m.get("content") or ""}]
            parts += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}}
                      for b in m["images"]]
            out.append({"role": m["role"], "content": parts})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def ollama_body(model, messages, tools, think, cfg):
    body = {"model": model, "messages": to_ollama(messages), "stream": True,
            "keep_alive": cfg.get("keep_alive", "30m"),
            "options": {"num_ctx": int(cfg.get("num_ctx", 8192)),
                        "num_predict": int(cfg.get("num_predict", 1536)),
                        "temperature": float(cfg.get("temperature", 0.2))}}
    if tools:
        body["tools"] = tools
    if think is not None:
        body["think"] = think
    return body


def openai_body(model, messages, tools, think, cfg):
    msgs = to_openai(messages)
    body = {"model": model, "messages": msgs, "stream": True,
            "temperature": float(cfg.get("temperature", 0.2)),
            "max_tokens": int(cfg.get("num_predict", 1536)),
            "stream_options": {"include_usage": True}}
    if think is False and "qwen3" in model.lower():
        # soft switch do Qwen3 no prompt de sistema (fixo: vale em todos os passos e não quebra o
        # cache); sem sistema, no último pedido do usuário. llama.cpp/vLLM também leem o kwarg.
        idx = next((i for i in range(len(msgs) - 1, -1, -1)
                    if msgs[i]["role"] == "user" and isinstance(msgs[i]["content"], str)), None)
        if msgs and msgs[0]["role"] == "system" and isinstance(msgs[0]["content"], str):
            idx = 0
        if idx is not None:
            msgs[idx] = dict(msgs[idx], content=msgs[idx]["content"] + " /no_think")
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if tools:
        body["tools"] = tools
    return body


# ---------------------------------------------------------------------------
# modelos
# ---------------------------------------------------------------------------

def model_info(name, show):
    """Resumo do /api/show do Ollama."""
    caps = set(show.get("capabilities") or [])
    think = show.get("thinking")
    values = think.get("values") if isinstance(think, dict) else None
    if values is None:
        values = [False, True] if "thinking" in caps else []
    ctx = next((v for k, v in (show.get("model_info") or {}).items() if k.endswith(".context_length")), 0)
    det = show.get("details") or {}
    return {"name": name, "tools": "tools" in caps, "vision": "vision" in caps,
            "thinking": list(values), "ctx": int(ctx or 0),
            "size": det.get("parameter_size", ""), "family": det.get("family", "")}


def think_param(info, mode, plan):
    """Valor do campo "think" (None = não mandar). mode: off | on | auto (auto = só no Planejar)."""
    values = info.get("thinking") or []
    if not values:
        return None
    levels = [v for v in values if isinstance(v, str)]
    if levels:                                           # níveis: gpt-oss (low/medium/high), qwen3.8 (False/low/…/xhigh)
        wants = mode == "on" or (mode == "auto" and plan)
        if not wants and False in values:                # dá para desligar de verdade (qwen3.8)
            return False
        if not wants:
            return "low" if "low" in levels else levels[0]
        if mode == "on":
            return "high" if "high" in levels else ("xhigh" if "xhigh" in levels else levels[-1])
        return "medium" if "medium" in levels else levels[len(levels) // 2]
    if all(v is True for v in values):                   # só sabe pensar (ex.: qwen3:4b 2507)
        return True
    return mode == "on" or (mode == "auto" and plan)


def openai_think(mode, plan):
    """Servidor OpenAI-compatível não diz se o modelo pensa: decide só pela opção Raciocínio."""
    return mode == "on" or (mode == "auto" and plan)


PREFERRED = ("qwen3:8b", "qwen2.5:7b", "qwen3:4b-instruct", "qwen2.5:3b", "llama3.1:8b", "qwen3:4b")


def rank_models(infos):
    """Modelos com ferramentas, do mais recomendado para este tipo de máquina ao menos."""
    def score(i):
        pref = next((n for n, p in enumerate(PREFERRED) if i["name"].startswith(p)), len(PREFERRED))
        only_thinks = bool(i["thinking"]) and all(v is True for v in i["thinking"])
        return (only_thinks, pref, i["name"])
    return sorted((i for i in infos if i["tools"]), key=score)


def model_label(info):
    bits = [info.get("size") or ""]
    if info["thinking"] and all(v is True for v in info["thinking"]):
        bits.append("sempre pensa, lento")
    if info["vision"]:
        bits.append("vê imagens")
    bits = [b for b in bits if b]
    return info["name"] + (f"  ({', '.join(bits)})" if bits else "")
