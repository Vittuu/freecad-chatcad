"""Ferramentas do provedor local, executadas direto no FreeCAD (thread da interface).
Sem MCP: o modelo chama run_python / get_document / set_parameter / view_screenshot e o
painel executa aqui. Funciona também no freecadcmd (sem interface) para os testes."""

import contextlib
import io
import itertools
import linecache
import math
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import unicodedata

import FreeCAD as App

try:
    import FreeCADGui as Gui
    if not App.GuiUp:
        Gui = None
except ImportError:
    Gui = None

import chatcad_llm as L

SHEET = "Parametros"
CODE_TIMEOUT = 120         # s; só vale para laços em Python, não interrompe uma operação OCC longa
MAX_LINES = 3_000_000      # linhas de Python do agente por passo (~2-5 s de laço puro)
DIM_PROPS = ("Length", "Width", "Height", "Radius", "Radius1", "Radius2", "Angle", "Size",
             "Thickness", "Value")
PLACEMENT_KEYS = {"x": "Placement.Base.x", "y": "Placement.Base.y", "z": "Placement.Base.z",
                  "angulo": "Placement.Rotation.Angle"}
EXPR_WORDS = {"mm", "cm", "m", "km", "um", "in", "ft", "deg", "rad", "gon", "pi", "e", "abs", "sin",
              "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "pow", "min", "max", "round",
              "trunc", "floor", "ceil", "mod", "hypot", "exp", "log", "log10", "cath", "sum", "average"}
NO_HIDE_LINKS = {"AttachmentSupport", "Support", "ExpressionEngine", "Group", "Origin"}
PROTECTED = {"App", "FreeCAD", "Gui", "FreeCADGui", "Part", "Vector", "math", "doc", "param", "liga", "sel",
             "arestas", "arredonda"}
AGENT_PREFIX = "<agente-"  # nome de "arquivo" do código de cada passo: <agente-1>, <agente-2>...
ANGLE_WORDS = r"(^|_)(ang|angulo|angle|grau|graus|inclinacao|rotacao)\d*(_|$)"
_runs = itertools.count(1)


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def make_alias(nome):
    s = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s).strip("_") or "p"
    if s[0].isdigit():
        s = "p_" + s
    return s[:40]


def default_unit(alias):
    if re.match(r"^(n|num|numero|qtd|quant|quantidade)(_|$)", alias):
        return ""
    if re.search(ANGLE_WORDS, alias):         # palavra inteira: "espessura_flange" não é ângulo
        return "deg"
    return "mm"


def _num_text(v):
    """Número com precisão total (":g" corta em 6 dígitos: 1250.125 virava 1250.12)."""
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)                           # 60.0 -> "60"
    return repr(v)


def cell_text(valor, alias, unidade=None):
    """Conteúdo de célula para param()/set_parameter: sempre uma expressão ("=40 mm")."""
    if isinstance(valor, bool):
        valor = int(valor)
    if isinstance(valor, (int, float)):
        unit = default_unit(alias) if unidade is None else unidade
        return f"={_num_text(valor)} {unit}".rstrip()
    s = str(valor).strip().replace("°", " deg")
    if not s:
        raise ValueError("valor vazio")
    if s.startswith("="):
        return s
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", s):
        return cell_text(float(s.replace(",", ".")), alias, unidade)
    return "=" + re.sub(r"(\d),(\d)", r"\1.\2", s)    # "45 mm", "30 deg", "largura / 2"


def _num(v):
    return float(getattr(v, "Value", v))


def _fmt_q(v):
    """Sem vírgula decimal do locale ("60,00 mm" confunde o modelo): 60 mm, 30 deg, 6."""
    if hasattr(v, "Value") and hasattr(v, "Unit"):
        try:
            units = App.Units
            suffix = {str(units.Length): " mm", str(units.Angle): " deg"}.get(str(v.Unit), "")
            if suffix or str(v.Unit) == str(units.Unit()):
                return f"{v.Value:.6g}{suffix}"
        except Exception:
            pass
        return v.UserString
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _err_text(e):
    """FreeCADError às vezes traz um dict em args[0]."""
    if e.args and isinstance(e.args[0], dict):
        return e.args[0].get("sErrMsg") or str(e.args[0])
    return str(e) or type(e).__name__


_PLACEMENT_HINT = ("Placement recebe App.Placement(App.Vector(x, y, z), App.Rotation()); para só mover use "
                   "obj.Placement.Base = App.Vector(x, y, z) (números) ou liga(obj, x=\"...\", y=\"...\", z=\"...\") "
                   "para seguir os parâmetros")

ERROR_HINTS = (   # mensagens do OCC/FreeCAD -> dica curta em português para o modelo pequeno
    ("Not enough shape objects linked", "Part::MultiFuse precisa de 2 ou mais formas em Shapes; "
                                        "com uma só, ligue direto em Tool (peca.Tool = furo)"),
    ("graph must be a DAG", "dependência circular: um objeto aponta para ele mesmo (Base/Tool)"),
    ("Base shape is null", "a Base está vazia ou aponta para o próprio objeto"),
    ("Tool shape is null", "a Tool está vazia ou aponta para o próprio objeto"),
    ("command not done", "a booleana falhou: a ferramenta precisa atravessar a peça; deixe-a 1 mm "
                         "mais alta/comprida que a peça e confira a posição"),
    ("Resulting shape is null", "a operação não sobrou nada: confira medidas e posições"),
    ("Linked shape object is empty", "um objeto ligado está vazio; confira Base/Tool/Shapes"),
    # erros de Python que modelos pequenos repetem (vistos com o qwen3:8b montando um suporte)
    ("three floats, tuple or Vector expected", "Vector/Placement só aceitam números, nunca texto. Use os "
                                               "números direto (App.Vector(0, 37, 0)) ou, para seguir os "
                                               "parâmetros, liga(obj, x=\"largura - margem\", y=\"...\", z=\"...\")"),
    ("matrix or placement expected", _PLACEMENT_HINT),          # App.Placement(...) com argumentos errados
    ("must be 'matrix' or 'placement'", _PLACEMENT_HINT),       # obj.Placement = App.Vector(...)
    ("has no attribute 'Radius'", "se for arredondamento: Part::Fillet não tem Radius; o raio vai em Edges: "
                                  "f.Base = obj; f.Edges = [(numero_da_aresta, raio, raio), ...] (números das "
                                  "arestas: get_document com objeto=\"Nome\")"),
)


def error_hint(msg):
    for key, hint in ERROR_HINTS:
        if key.lower() in (msg or "").lower():
            return f" -> Dica: {hint}"
    return ""


def object_error(o):
    try:
        states = [str(s) for s in (o.State or [])]
    except Exception:
        states = []
    bad = [s for s in states if s.lower() in ("invalid", "error")]
    try:
        valid = o.isValid()
    except Exception:
        valid = True
    if valid and not bad:
        return ""
    try:
        reason = o.getStatusString()
    except Exception:
        reason = ""
    return reason or ", ".join(states) or "erro"


class AgentTimeout(BaseException):
    """Limite de linhas/tempo estourado. BaseException de propósito: o `except Exception` do código
    do agente (padrão comum de "tentar de novo" em modelo pequeno) não engole o limite. Depois que o
    rastreador levanta, o Python desliga o rastreamento; um `except:` puro ainda engole, então o
    deadline também marca `fired` para o run_python recusar o passo."""


@contextlib.contextmanager
def deadline(seconds, max_lines=3_000_000, prefix=AGENT_PREFIX):
    """Interrompe laços infinitos no código do agente. Só os frames do agente são rastreados (custo
    ~zero no resto), inclusive funções definidas em passos anteriores (<agente-1> chamada no
    <agente-2>) e execute() de FeaturePython do agente durante o recálculo. Conta linhas executadas
    E tempo: uma operação OCC lenta (ou o primeiro import de um módulo) não dispara o limite de
    linhas; um `while True` dispara em poucos segundos. Devolve {"fired": bool}."""
    end = time.monotonic() + seconds
    count = [0]
    state = {"fired": False}

    def local(frame, event, arg):
        if event == "line":
            count[0] += 1
            if count[0] > max_lines or (count[0] % 1000 == 0 and time.monotonic() > end):
                state["fired"] = True
                raise AgentTimeout(f"o código rodou demais ({count[0]} linhas; laço infinito?)")
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename.startswith(prefix) else None

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        yield state
    finally:
        sys.settrace(old)


def short_traceback(e, filename):
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == filename]
    where = ""
    if frames:
        f = frames[-1]
        where = f" (linha {f.lineno}: {(f.line or '').strip()[:140]})"
    name = "TimeoutError" if isinstance(e, AgentTimeout) else type(e).__name__
    return f"{name}: {_err_text(e)}{where}"


def find_alias(sh, nome):
    """Alias que existe de fato na planilha para `nome` (em qualquer coluna). param() grava
    "<alias>_p" quando o FreeCAD recusa o nome (unidades: h, l, t, s, g, m, min...)."""
    if sh is None:
        return None
    base = make_alias(nome)
    for a in dict.fromkeys((str(nome), base, base + "_p")):
        try:
            if sh.getCellFromAlias(a):
                return a
        except Exception:
            pass
    return None


def _cell_col_row(cell):
    m = re.fullmatch(r"([A-Z]+)(\d+)", cell or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _cell_unit(sh, alias):
    """Unidade do valor atual da célula: "mm", "deg", "" (sem unidade) ou None (outra: usa o padrão)."""
    try:
        v = getattr(sh, alias)
    except Exception:
        return None
    if isinstance(v, (int, float)):
        return ""
    unit = getattr(v, "Unit", None)
    if unit is None:
        return None
    units = App.Units
    return {str(units.Length): "mm", str(units.Angle): "deg", str(units.Unit()): ""}.get(str(unit))


def _object_names(d):
    try:
        return frozenset(o.Name for o in d.Objects)
    except Exception:
        return None


def _undo_state(d):
    try:
        return d.UndoCount, tuple(d.UndoNames)
    except Exception:
        return None


def _mtime(path):
    try:
        return os.path.getmtime(path) if path else None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ferramentas
# ---------------------------------------------------------------------------

class FreeCADTools:
    def __init__(self, timeout=CODE_TIMEOUT, max_lines=MAX_LINES):
        self.timeout = timeout
        self.max_lines = max_lines
        self.reset()

    def _fixed_ns(self):
        import Part
        return {"App": App, "FreeCAD": App, "Gui": Gui, "FreeCADGui": Gui, "Part": Part, "Vector": App.Vector,
                "Placement": App.Placement, "Rotation": App.Rotation, "math": math, "param": self.param,
                "liga": self.liga, "arestas": self.arestas, "arredonda": self.arredonda}

    def reset(self):
        """Nova conversa: namespace limpo (variáveis do agente persistem entre passos de uma conversa)."""
        self.ns = {"__name__": "__main__", **self._fixed_ns()}
        self.turn = None

    def _restore_ns(self, snapshot):
        """Passo desfeito: o namespace volta junto (sem isso `peca`/`furo` ficam apontando para objetos
        que a transação apagou, e um param() recusado deixa a variável com o valor recusado)."""
        self.ns.clear()
        self.ns.update(snapshot)

    # --- turno -----------------------------------------------------------------
    def begin_turn(self, user_text, backup=True, selection=True):
        words = [w for w in re.findall(r"[a-zA-ZÀ-ú0-9]+", user_text.split(L.CONTEXT_MARK)[-1])
                 if len(w) > 2][:4]
        active = App.ActiveDocument
        self.turn = {"slug": make_alias("-".join(words)).replace("_", "-")[:30] or "mudanca",
                     "backup": backup, "backed_up": False, "steps": [], "changed": False,
                     "doc": active.Name if active else None,     # documento do turno (fixo)
                     "docs": set(),                              # documentos que o turno mudou
                     "selection": selection}                     # False: o usuário mandou ignorar a seleção

    def end_turn(self, save=True):
        """Salva os documentos (que já tinham arquivo) que o turno mudou de fato. Não é o documento
        ativo: o usuário pode ter trocado de aba enquanto o modelo pensava."""
        t = self.turn
        if not (t and t["changed"] and save):
            return ""
        docs = App.listDocuments()
        saved, errors = [], []
        for name in sorted(t.get("docs") or ()):
            d = docs.get(name)
            if d is None or not d.FileName:
                continue
            try:
                d.save()
                saved.append(os.path.basename(d.FileName))
            except Exception as e:
                errors.append(f"{os.path.basename(d.FileName)}: {_err_text(e)}")
        if errors:
            return "Não consegui salvar o documento: " + "; ".join(errors)
        return f"Documento salvo ({', '.join(saved)})." if saved else ""

    def _ensure_doc(self):
        doc = App.ActiveDocument
        if doc is None:
            doc = App.newDocument("Peca")
            if Gui is not None:
                Gui.SendMsgToActiveView("ViewFit")
        return doc

    def _turn_doc(self):
        """Documento em que o agente trabalha neste turno: o que estava ativo no começo do turno."""
        t = self.turn
        name = t.get("doc") if t else None
        doc = App.listDocuments().get(name) if name else None
        if doc is None:
            doc = self._ensure_doc()
            if t is not None:
                t["doc"] = doc.Name
        return doc

    def _current_doc(self):
        """Para leitura: o documento do turno, se ainda estiver aberto; senão o ativo."""
        t = self.turn
        name = t.get("doc") if t else None
        return (App.listDocuments().get(name) if name else None) or App.ActiveDocument

    def _backup(self, doc):
        """Cópia do arquivo antes da primeira mudança do turno -> caminho ("" se não fez). Uma cópia que
        falha (pasta só de leitura...) nunca derruba o passo: vira um aviso no resultado."""
        t = self.turn
        if not t or not t["backup"] or t["backed_up"]:
            return ""
        t["backed_up"] = True
        path = doc.FileName
        if not path or not os.path.exists(path):
            return ""
        base, ext = os.path.splitext(path)
        dst = f"{base}.antes-{t['slug']}{ext}"
        n = 2
        while os.path.exists(dst):
            dst = f"{base}.antes-{t['slug']}-{n}{ext}"
            n += 1
        try:
            shutil.copy2(path, dst)
        except OSError as e:
            t["backup_note"] = f"(backup não foi possível: {e.strerror or e})"
            return ""
        return dst

    def _drop_backup(self, dst, path, mtime):
        """O passo não mudou nada: apaga o backup que acabou de fazer (o próximo passo que mudar algo
        faz outro). Se o arquivo foi salvo no meio do passo, o backup fica."""
        if not dst or _mtime(path) != mtime:
            return
        try:
            os.remove(dst)
        except OSError:
            return
        if self.turn:
            self.turn["backed_up"] = False

    def _mark_changed(self, names):
        t = self.turn
        if t is None or not names:
            return ""
        t["changed"] = True
        t.setdefault("docs", set()).update(names)
        return t.pop("backup_note", "")

    # --- transação do passo --------------------------------------------------------
    def _open_step(self, doc, name):
        """Transação da aplicação: abrange todo documento que o passo mexer (não só `doc`). Documento
        com undo desligado ganha undo só durante o passo; sem isso um erro não seria desfeito."""
        docs = App.listDocuments()
        step = {"doc": doc, "tid": 0, "undo_off": [], "docs": set(docs),
                "undo": {n: _undo_state(d) for n, d in docs.items()},
                "objs": {n: _object_names(d) for n, d in docs.items()}}
        for d in docs.values():
            try:
                if not d.UndoMode:
                    d.UndoMode = 1
                    step["undo_off"].append(d)
            except Exception:
                pass
        try:
            step["tid"] = App.setActiveTransaction(name[:70]) or 0
        except Exception:
            step["tid"] = 0
        if not step["tid"]:
            try:
                doc.openTransaction(name[:70])       # sem transação da aplicação: só o documento do turno
            except Exception:
                pass
        return step

    def _close_step(self, step, abort):
        """Fecha (desfaz ou confirma) a transação do passo -> nomes dos documentos que ele mudou."""
        docs = App.listDocuments()
        pending = {n for n, d in docs.items() if getattr(d, "HasPendingTransaction", False)}
        try:
            if step["tid"]:
                App.closeActiveTransaction(abort, step["tid"])
            elif abort:
                step["doc"].abortTransaction()
            else:
                step["doc"].commitTransaction()
        except Exception:
            pass
        changed = set()
        for n, d in App.listDocuments().items():
            if n not in step["docs"] or _object_names(d) != step["objs"].get(n):
                changed.add(n)
            elif n in pending or _undo_state(d) != step["undo"].get(n):
                # o FreeCAD abre uma transação vazia "-> nome" no documento ATIVO quando outro documento
                # muda (para o Desfazer dele funcionar): isso não é mudança do documento ativo
                top = (d.UndoNames or [""])[0]
                if not str(top).startswith("-> "):
                    changed.add(n)
        for d in step["undo_off"]:
            try:
                d.UndoMode = 0
            except Exception:
                pass
        if abort:
            for n in set(App.listDocuments()) - step["docs"]:     # documentos que o passo criou
                try:
                    App.closeDocument(n)
                except Exception:
                    pass
            return set()
        return changed

    def dispatch(self, name, args):
        """-> (ok, texto_para_o_modelo, imagens_png)."""
        if name == "run_python":
            return self.run_python(args["code"], args.get("resumo", ""))
        if name == "get_document":
            return True, self.document_text(args.get("objeto", "")), []
        if name == "set_parameter":
            return self.set_parameter(args["nome"], args["valor"], args.get("descricao", ""))
        if name == "view_screenshot":
            png, err = self.screenshot(args.get("vista", "isometrica"))
            return (True, "Imagem da vista anexada.", [png]) if png else (False, err, [])
        return False, f"ferramenta desconhecida: {name}", []

    # --- run_python --------------------------------------------------------------
    def run_python(self, code, resumo=""):
        doc = self._turn_doc()                    # fixo no turno: o usuário pode trocar de aba no meio
        mtime = _mtime(doc.FileName)
        backup = self._backup(doc)
        ns_before = dict(self.ns)
        self.ns.update(self._fixed_ns())          # o passo anterior pode ter sobrescrito math, Part, param...
        self.ns["doc"] = doc
        self.ns["sel"] = self.selection()
        before = {o.Name for o in doc.Objects}
        broken_before = {o.Name for o in doc.Objects if object_error(o)}
        docs_before = set(App.listDocuments())
        fname = f"{AGENT_PREFIX}{next(_runs)}>"
        linecache.cache[fname] = (len(code), None, code.splitlines(True), fname)   # linhas no traceback
        out = io.StringIO()
        step = self._open_step(doc, "Chat CAD: " + (resumo or "passo do agente"))
        t0 = time.monotonic()
        error = ""
        try:
            compiled = compile(code, fname, "exec")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                    deadline(self.timeout, self.max_lines) as dl:
                exec(compiled, self.ns)
            if dl["fired"]:                       # o código engoliu o TimeoutError com um except puro
                error = "TimeoutError: o código rodou demais (laço infinito?) e um except engoliu o limite"
        except SyntaxError as e:
            error = f"SyntaxError na linha {e.lineno}: {e.msg}: {(e.text or '').strip()[:140]}"
        except (Exception, SystemExit, AgentTimeout) as e:   # nunca deixa a exceção chegar no FreeCAD
            error = short_traceback(e, fname)
            error += error_hint(error)
        printed = L.truncate(out.getvalue().strip(), 900)
        if not error:
            # o código rodou, mas o passo só vale se o recálculo não quebrou nada novo; senão desfaz
            # (sem isso o modelo repete o código e empilha Cilindro001, Cilindro002... com erro)
            try:
                with deadline(self.timeout, self.max_lines):   # execute() de FeaturePython do agente
                    doc.recompute()
                broken = [o for o in doc.Objects if o.Name not in broken_before and object_error(o)]
            except (Exception, AgentTimeout) as e:
                broken, error = [], f"o recálculo falhou: {_err_text(e)}"
            if broken:
                error = "o código rodou, mas o recálculo falhou: " + "; ".join(
                    f"{o.Name} ({o.TypeId}): {object_error(o)}{error_hint(object_error(o))}" for o in broken[:3])
        if error:
            self._abort_step(step, doc, before, ns_before, backup, mtime)
            text = f"ERRO: {error}\n"
            if printed:
                text += f"Saída antes do erro:\n{printed}\n"
            text += "Nada foi alterado (o passo foi desfeito). Corrija o código e chame run_python de novo."
            return False, text, []
        try:
            new = [o for o in doc.Objects if o.Name not in before]
            hidden = self._auto_hide(doc, new)
        except Exception as e:
            self._abort_step(step, doc, before, ns_before, backup, mtime)
            return False, f"ERRO ao recalcular: {_err_text(e)}. Nada foi alterado.", []
        changed = self._close_step(step, abort=False)
        removed = before - {o.Name for o in doc.Objects}
        if new or removed:
            changed.add(doc.Name)
        backup_note = self._mark_changed(changed)
        if not changed:                           # só leitura (print do volume...): sem backup nem autosave
            self._drop_backup(backup, doc.FileName, mtime)
            backup = ""
        lines = [f"OK ({time.monotonic() - t0:.1f} s)."]
        if printed:
            lines.append("Saída:\n" + printed)
        if new or removed:
            parts = [f"+{o.Name} ({o.TypeId})" for o in new[:12]] + [f"-{n}" for n in sorted(removed)[:6]]
            lines.append("Mudanças: " + ", ".join(parts))
        if hidden:
            lines.append("Ocultei (auxiliares): " + ", ".join(hidden))
        created_docs = set(App.listDocuments()) - docs_before
        if created_docs:
            lines.append(f"Atenção: você criou o documento {', '.join(created_docs)}. Use doc em vez de App.newDocument().")
        lines.append(self.verification(doc))
        lines += self.cut_hints(doc, {o.Name for o in new})
        params = self.params_text(doc)
        if params:
            lines.append(params)
        if backup:
            lines.append(f"(backup salvo: {os.path.basename(backup)})")
        if backup_note:
            lines.append(backup_note)
        return True, "\n".join(lines), []

    def _abort_step(self, step, doc, before, ns_before, backup, mtime):
        """Desfaz o passo inteiro: transação (todos os documentos), documentos criados e namespace."""
        self._close_step(step, abort=True)
        try:
            self._remove_new(doc, before)         # garantia, se nem a transação abriu (normalmente não sobra nada)
            doc.recompute()
        except Exception:
            pass
        self._restore_ns(ns_before)
        self._drop_backup(backup, doc.FileName, mtime)

    def _remove_new(self, doc, before):
        """Sem undo no documento: apaga o que o passo criou (dependentes primeiro)."""
        for o in reversed([o for o in doc.Objects if o.Name not in before]):
            try:
                doc.removeObject(o.Name)
            except Exception:
                pass

    def _auto_hide(self, doc, new):
        """Esconde o que foi consumido por objetos novos (Base/Tool/Shapes...), como a interface faz."""
        hidden = []
        for parent in new:
            if parent.TypeId.startswith(("Spreadsheet::", "App::", "Sketcher::")):
                continue
            for prop in parent.PropertiesList:
                if prop in NO_HIDE_LINKS:
                    continue
                try:
                    if not parent.getTypeIdOfProperty(prop).startswith("App::PropertyLink"):
                        continue
                    value = getattr(parent, prop)
                except Exception:
                    continue
                for child in _linked_objects(value):
                    if child is parent or not hasattr(child, "Shape") or not child.Visibility:
                        continue
                    if _in_body(child):
                        continue
                    child.Visibility = False
                    hidden.append(child.Name)
        return sorted(set(hidden))

    # --- verificação e leitura ---------------------------------------------------
    def final_parts(self, doc):
        out = []
        for o in doc.Objects:
            if not getattr(o, "Visibility", False) or o.TypeId.startswith(("Spreadsheet::", "App::", "Sketcher::")):
                continue
            if _in_body(o):
                continue
            shape = getattr(o, "Shape", None)
            if shape is None or shape.isNull():
                continue
            out.append(o)
        return out

    def verification(self, doc):
        errors = [f"{o.Name}: {object_error(o)}" for o in doc.Objects if object_error(o)]
        lines = []
        if errors:
            lines.append("OBJETOS COM ERRO: " + "; ".join(errors[:5]))
        finals = self.final_parts(doc)
        if not finals:
            lines.append("Verificação: nenhuma peça visível.")
        for o in finals[:4]:
            s = o.Shape
            bb = s.BoundBox
            if not s.isValid():
                state = "FORMA INVÁLIDA"
            elif len(s.Solids) == 1:
                state = "sólido único válido"
            elif not s.Solids:
                state = "SEM SÓLIDO (só faces/arestas)"
            else:
                state = f"{len(s.Solids)} SÓLIDOS SEPARADOS (deveria ser 1)"
            lines.append(f"Verificação: {o.Name} \"{o.Label}\": {state}, {bb.XLength:.1f} x {bb.YLength:.1f} x "
                         f"{bb.ZLength:.1f} mm, volume {s.Volume:.0f} mm3")
        if len(finals) > 1:
            lines.append(f"Atenção: {len(finals)} peças visíveis ({', '.join(o.Name for o in finals[:6])}); "
                         "se era para ser uma peça só, junte com Part::Fuse ou corte com Part::Cut.")
        return "\n".join(lines)

    def cut_hints(self, doc, names):
        """Corte (criado neste passo) cuja ferramenta sai para fora da peça no plano XY: quase sempre
        furo fora de posição. Compara a caixa XY da ferramenta com a da parte que ela realmente
        corta (common); ferramenta mais alta em Z (corte "limpo") não conta."""
        out = []
        for o in doc.Objects:
            if o.Name not in names or o.TypeId != "Part::Cut" or object_error(o):
                continue
            base, tool = getattr(o, "Base", None), getattr(o, "Tool", None)
            try:
                if len(tool.Shape.Faces) > 200:          # padrões grandes: pula (custo)
                    continue
                t = tool.Shape.BoundBox
                c = base.Shape.common(tool.Shape).BoundBox
            except Exception:
                continue
            eps = 1e-3
            inside = (c.XMin <= t.XMin + eps and c.XMax >= t.XMax - eps and
                      c.YMin <= t.YMin + eps and c.YMax >= t.YMax - eps)
            if not inside:
                out.append(f"Atenção: {tool.Name} sai para fora de {base.Name} em X/Y (corta só em parte). "
                           "Se era um furo interno, confira x e y (Part::Cylinder: x, y = centro da base; "
                           "Part::Box: x, y = canto).")
        return out[:3]

    def final_warnings(self):
        """Problemas que o modelo não pode ignorar antes de dizer "pronto" (portão da resposta final)."""
        doc = self._current_doc()
        if doc is None or not (self.turn and self.turn.get("changed")):
            return ""
        problems = [f"{o.Name}: {object_error(o)}" for o in doc.Objects if object_error(o)]
        finals = self.final_parts(doc)
        if not finals:
            problems.append("nenhuma peça visível")
        elif len(finals) > 1:
            problems.append(f"{len(finals)} peças visíveis separadas ({', '.join(o.Name for o in finals[:5])}); "
                            "se o pedido era uma peça só, corte com Part::Cut ou junte com Part::Fuse")
        for o in finals[:4]:
            if not o.Shape.isValid() or len(o.Shape.Solids) != 1:
                problems.append(f"{o.Name} não é um sólido único válido")
        return "; ".join(problems)

    def document_text(self, objeto=""):
        doc = self._current_doc()
        if doc is None:
            return "Nenhum documento aberto. run_python cria um documento novo automaticamente."
        if objeto:
            return self.object_detail(doc, objeto)
        lines = [f"Documento: {doc.Name}" + (f" ({doc.FileName})" if doc.FileName else " (não salvo)")]
        params = self.params_text(doc)
        lines.append(params or "Sem planilha Parametros.")
        objs = [o for o in doc.Objects if not o.TypeId.startswith(("App::Origin", "App::Line", "App::Plane"))]
        lines.append(f"Objetos ({len(objs)}):")
        for o in objs[:30]:
            lines.append("- " + self.object_line(o))
        if len(objs) > 30:
            lines.append(f"... e mais {len(objs) - 30} objetos")
        sel = self.selection_text()
        if sel:
            lines.append(sel)
        return L.truncate("\n".join(lines), 2500)

    def context_text(self):
        """Contexto curto que vai em cada pedido (economiza um passo de get_document; ~100 tokens)."""
        doc = self._current_doc()
        if doc is None:
            return "Nenhum documento aberto (run_python cria um)."
        lines = [f"Documento ativo: {doc.Name}" + (" (salvo)" if doc.FileName else " (não salvo)")]
        params = self.params_text(doc)
        if params:
            lines.append(params)
        finals = self.final_parts(doc)
        if finals:
            lines.append("Peças visíveis: " + "; ".join(
                f'{o.Name} "{o.Label}" {o.Shape.BoundBox.XLength:.1f} x {o.Shape.BoundBox.YLength:.1f} x '
                f'{o.Shape.BoundBox.ZLength:.1f} mm' for o in finals[:4]))
        if doc.Objects:
            lines.append(f"{len(doc.Objects)} objetos no total (detalhes: get_document).")
        else:
            lines.append("Documento vazio.")
        sel = self.selection_text()
        if sel:
            lines.append(sel)
        return "\n".join(lines)

    def object_line(self, o):
        bits = [f'{o.Name} "{o.Label}" {o.TypeId}', "visível" if getattr(o, "Visibility", False) else "oculto"]
        exprs = dict(getattr(o, "ExpressionEngine", []) or [])
        dims = []
        for p in DIM_PROPS:
            if p in o.PropertiesList:
                try:
                    v = _fmt_q(getattr(o, p))
                except Exception:
                    continue
                dims.append(f"{p}={v}" + (f" [{exprs[p]}]" if p in exprs else ""))
        if dims:
            bits.append(", ".join(dims))
        others = [f"{k}=[{v}]" for k, v in exprs.items() if k not in DIM_PROPS][:4]
        if others:
            bits.append(", ".join(others))
        shape = getattr(o, "Shape", None)
        if shape is not None and not o.TypeId.startswith("Spreadsheet") and not shape.isNull():
            bb = shape.BoundBox
            bits.append(f"caixa {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm")
        err = object_error(o)
        if err:
            bits.append("ERRO: " + err)
        return " · ".join(bits)

    def object_detail(self, doc, name):
        o = doc.getObject(name) or next(iter(doc.getObjectsByLabel(name)), None)
        if o is None:
            return f"Objeto '{name}' não existe. Objetos: " + ", ".join(x.Name for x in doc.Objects[:40])
        lines = [self.object_line(o)]
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            return lines[0]
        lines.append("Faces:")
        for i, f in enumerate(shape.Faces[:40], 1):
            c = f.CenterOfMass
            kind = type(f.Surface).__name__
            try:
                u, v = f.Surface.parameter(c)
                n = f.normalAt(u, v)
                normal = f", normal ({n.x:.2f}, {n.y:.2f}, {n.z:.2f})"
            except Exception:
                normal = ""
            lines.append(f"  Face{i}: {kind}, área {f.Area:.1f}, centro ({c.x:.1f}, {c.y:.1f}, {c.z:.1f}){normal}")
        lines.append("Arestas (use os números em Part::Fillet):")
        for i, e in enumerate(shape.Edges[:60], 1):
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            kind = type(e.Curve).__name__
            extra = f", raio {e.Curve.Radius:.1f}" if hasattr(e.Curve, "Radius") else ""
            lines.append(f"  {i}: {kind}{extra}, {e.Length:.1f} mm, de ({a.x:.1f}, {a.y:.1f}, {a.z:.1f}) "
                         f"a ({b.x:.1f}, {b.y:.1f}, {b.z:.1f})")
        return L.truncate("\n".join(lines), 3500)

    def selection(self):
        if Gui is None or (self.turn and not self.turn.get("selection", True)):
            return []                             # chip "Ignorando": nem contexto, nem get_document, nem `sel`
        try:
            out = []
            for s in Gui.Selection.getSelectionEx("", 0):
                subs = list(s.SubElementNames) or [""]
                pts = list(s.PickedPoints)
                for sub, pt in itertools.zip_longest(subs, pts):
                    out.append({"objeto": s.Object, "elemento": sub or "", "ponto": pt})
            return out
        except Exception:
            return []

    def selection_text(self):
        items = self.selection()
        if not items:
            return ""
        lines = ["Seleção do usuário:"]
        for it in items[:6]:
            o, sub, pt = it["objeto"], it["elemento"], it["ponto"]
            line = f"- {o.Name} \"{o.Label}\" {sub or '(objeto inteiro)'}"
            try:
                el = o.getSubObject(sub) if sub else None
                if el is not None and el.ShapeType == "Face":
                    c = pt or el.CenterOfMass
                    u, v = el.Surface.parameter(c)
                    n = el.normalAt(u, v)
                    line += (f": face {type(el.Surface).__name__}, área {el.Area:.1f}, normal "
                             f"({n.x:.2f}, {n.y:.2f}, {n.z:.2f})")
                elif el is not None and el.ShapeType == "Edge":
                    line += f": aresta {type(el.Curve).__name__}, {el.Length:.1f} mm"
            except Exception:
                pass
            if pt is not None:
                line += f"; clicado em ({pt.x:.1f}, {pt.y:.1f}, {pt.z:.1f})"
            lines.append(line)
        return "\n".join(lines)

    # --- parâmetros ----------------------------------------------------------------
    def sheet(self, doc, create=False):
        for o in doc.Objects:
            if o.TypeId == "Spreadsheet::Sheet" and SHEET in (o.Name, o.Label):
                return o
        if not create:
            return None
        sh = doc.addObject("Spreadsheet::Sheet", SHEET)
        sh.Label = SHEET
        for cell, text in (("A1", "Parametro"), ("B1", "Valor"), ("C1", "Descricao")):
            sh.set(cell, text)
        sh.setStyle("A1:C1", "bold")
        return sh

    def params(self, doc):
        """[(alias, valor_mostrado, descricao)] lendo só células preenchidas (nunca sheet.get). O layout
        do Chat CAD é nome em A, valor em B e descrição em C, mas a planilha pode ser do usuário:
        vale alias em qualquer coluna (a descrição só é lida quando o alias está em B)."""
        sh = self.sheet(doc)
        if sh is None:
            return []
        try:
            cells = sh.getNonEmptyCells()
        except Exception:
            cells = [f"B{row}" for row in range(2, 400)]
        found = []
        for cell in cells:
            col, row = _cell_col_row(cell)
            alias = (sh.getAlias(cell) or "") if col else ""
            if alias:
                found.append((row, len(col), col, alias, cell))
        out = []
        for row, _, col, alias, cell in sorted(found):
            try:
                value = _fmt_q(getattr(sh, alias))
            except Exception:
                value = sh.getContents(cell)
            out.append((alias, value, sh.getContents(f"C{row}") if col == "B" else ""))
        return out

    def params_text(self, doc):
        ps = self.params(doc)
        return ("Parâmetros: " + "; ".join(f"{a}={v}" for a, v, _ in ps[:40])) if ps else ""

    def param(self, nome, valor, descricao="", unidade=None):
        """Usado dentro do run_python. Cria/atualiza o parâmetro e devolve "Parametros.<alias>".
        Também cria a variável Python <alias> com o número (modelos pequenos usam assim)."""
        doc = self.ns.get("doc") or self._ensure_doc()
        sh = self.sheet(doc, create=True)
        alias = find_alias(sh, nome)              # também acha "h_p" (param("h") na 2ª vez) e alias do usuário
        cell = sh.getCellFromAlias(alias) if alias else None
        if not cell:
            alias = make_alias(nome)
            row = 2
            while sh.getContents(f"A{row}") or sh.getContents(f"B{row}"):
                row += 1
            cell = f"B{row}"
            sh.set(f"A{row}", str(nome))
            if descricao:
                sh.set(f"C{row}", str(descricao))
            sh.set(cell, cell_text(valor, alias, unidade))
            try:
                sh.setAlias(cell, alias)
            except Exception:                     # o FreeCAD recusa nomes de unidade (h, l, t, m, min...)
                alias = alias + "_p"
                sh.setAlias(cell, alias)
        else:
            if unidade is None:                   # mantém a unidade que a célula já tinha (deg, sem unidade...)
                unidade = _cell_unit(sh, alias)
            sh.set(cell, cell_text(valor, alias, unidade))
            col, row = _cell_col_row(cell)
            if descricao and col == "B":          # layout do Chat CAD; em planilha do usuário não mexe
                sh.set(f"C{row}", str(descricao))
        sh.recompute()
        try:
            number = _num(getattr(sh, alias))
            for var in {alias, str(nome)}:          # também "diâmetro", se for um identificador válido
                if var.isidentifier() and var not in PROTECTED:
                    self.ns[var] = number
        except Exception:
            pass
        return f"{sh.Name}.{alias}"

    def qualify(self, expr, doc):
        """"furo_d / 2" -> "Parametros.furo_d / 2"; acusa nomes que não existem."""
        if isinstance(expr, (int, float)):
            return _num_text(expr)
        sh = self.sheet(doc)
        names = {o.Name for o in doc.Objects} | {o.Label for o in doc.Objects}

        def repl(m):
            word = m.group(1)
            if word in EXPR_WORDS and re.search(r"[\d.)]\s*$", m.string[:m.start()]):
                return word                        # unidade depois de número: "10 m", "30 deg"
            found = find_alias(sh, word)           # "diâmetro" -> "diametro"; "h" -> "h_p" (o que param() fez)
            if found:
                return f"{sh.Name}.{found}"
            if word in names or word in EXPR_WORDS or (sh is not None and word == sh.Name):
                return word
            existing = ", ".join(sorted(a for a, _, _ in self.params(doc))) or "nenhum ainda"
            raise NameError(f"'{word}' não é um parâmetro. Parâmetros que existem: {existing}. Use um desses "
                            f"nomes (sem acento) ou crie antes com param(\"{make_alias(word)}\", valor).")

        return re.sub(r"(?<![\w.])([^\W\d]\w*)(?![\w(])", repl, str(expr))

    def liga(self, obj, prop=None, expr=None, **props):
        """liga(obj, Length="largura", x="margem") ou liga(obj, "Placement.Base.z", "altura")."""
        doc = self.ns.get("doc") or App.ActiveDocument
        if isinstance(obj, str):
            obj = doc.getObject(obj) or next(iter(doc.getObjectsByLabel(obj)), None)
        if obj is None:
            raise ValueError("liga(): objeto não encontrado")
        if prop is not None:
            props[prop] = expr
        for key, value in props.items():
            path = PLACEMENT_KEYS.get(key, key)
            root = path.split(".")[0]
            if root not in obj.PropertiesList:
                useful = [p for p in obj.PropertiesList if p in DIM_PROPS or p == "Placement"]
                raise AttributeError(f"{obj.Name} ({obj.TypeId}) não tem '{root}'. Use: {', '.join(useful)}"
                                     " ou x=, y=, z=, angulo=")
            obj.setExpression(path, self.qualify(value, doc))
        return obj

    def _obj(self, obj):
        doc = self.ns.get("doc") or App.ActiveDocument
        if isinstance(obj, str):
            obj = doc.getObject(obj) or next(iter(doc.getObjectsByLabel(obj)), None)
        if obj is None or not hasattr(obj, "Shape"):
            raise ValueError("objeto não encontrado (passe o objeto ou o nome dele)")
        return obj

    def arestas(self, obj, eixo=None, x=None, y=None, z=None, tol=0.01):
        """Números (1, 2, ...) das arestas retas de obj, filtradas por direção e posição, para Part::Fillet.
        arestas(placa, eixo="y", z=95) -> arestas paralelas a Y na altura z=95 (os cantos de cima).
        x/y/z: a aresta inteira está nessa coordenada (ex.: x=0 é a face esquerda)."""
        obj = self._obj(obj)
        axis = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1)}.get(str(eixo).lower()) \
            if eixo else None
        if eixo and axis is None:
            raise ValueError("eixo deve ser 'x', 'y' ou 'z'")
        out = []
        for i, e in enumerate(obj.Shape.Edges, 1):
            if type(e.Curve).__name__ not in ("Line", "LineSegment"):
                continue
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            if axis is not None:
                d = b.sub(a)
                if d.Length < 1e-9 or abs(abs(d.normalize().dot(axis)) - 1) > 1e-6:
                    continue
            ok = True
            for comp, val in (("x", x), ("y", y), ("z", z)):
                if val is not None and (abs(getattr(a, comp) - val) > tol or abs(getattr(b, comp) - val) > tol):
                    ok = False
            if ok:
                out.append(i)
        if not out:
            raise ValueError(f"nenhuma aresta reta de {obj.Name} com eixo={eixo} x={x} y={y} z={z}; "
                             f"a caixa da peça vai de {obj.Shape.BoundBox}")
        return out

    def arredonda(self, obj, raio, arestas_n, nome="Arredondado"):
        """Cria um Part::Fillet de raio fixo nas arestas indicadas (números de arestas()). Devolve o objeto."""
        obj = self._obj(obj)
        doc = self.ns.get("doc") or App.ActiveDocument
        if isinstance(arestas_n, int):
            arestas_n = [arestas_n]
        n_edges = len(obj.Shape.Edges)
        bad = [n for n in arestas_n if not isinstance(n, int) or not 1 <= n <= n_edges]
        if bad or not arestas_n:
            raise ValueError(f"números de aresta inválidos {bad or arestas_n}: {obj.Name} tem arestas de 1 a {n_edges}; "
                             "use arestas(obj, eixo=..., x=/y=/z=...) para achar os números")
        if isinstance(raio, str):                 # nome de parâmetro: usa o valor atual da planilha
            sh = self.sheet(doc)
            alias = find_alias(sh, raio) if sh is not None else None
            if alias is None:
                raise ValueError(f"'{raio}' não é um parâmetro; passe um número (ex.: 10) ou crie com param()")
            raio = getattr(sh, alias)
        f = doc.addObject("Part::Fillet", nome)
        f.Base = obj
        r = float(getattr(raio, "Value", raio))
        f.Edges = [(n, r, r) for n in arestas_n]
        return f

    def set_parameter(self, nome, valor, descricao=""):
        doc = self._turn_doc()
        mtime = _mtime(doc.FileName)
        backup = self._backup(doc)
        known = find_alias(self.sheet(doc), nome) is not None
        broken_before = {o.Name for o in doc.Objects if object_error(o)}
        ns_before = dict(self.ns)
        step = self._open_step(doc, f"Chat CAD: {make_alias(nome)} = {valor}")
        try:
            self.ns["doc"] = doc
            ref = self.param(nome, valor, descricao)
            doc.recompute()
            broken = [o for o in doc.Objects if o.Name not in broken_before and object_error(o)]
            if broken:                        # ex.: furo maior que a placa -> não aplica
                raise ValueError("esse valor quebra a peça: " + "; ".join(
                    f"{o.Name}: {object_error(o)}{error_hint(object_error(o))}" for o in broken[:3]))
        except Exception as e:
            self._close_step(step, abort=True)
            try:
                doc.recompute()
            except Exception:
                pass
            self._restore_ns(ns_before)       # param() já tinha gravado a variável com o valor recusado
            self._drop_backup(backup, doc.FileName, mtime)
            return False, f"ERRO: {_err_text(e)}. Nada foi alterado.", []
        changed = self._close_step(step, abort=False)
        backup_note = self._mark_changed(changed)
        if not changed:
            self._drop_backup(backup, doc.FileName, mtime)
        alias = ref.split(".", 1)[1]          # o alias real ("h_p", alias da planilha do usuário...)
        head = f"{'Atualizei' if known else 'Criei'} {ref} = {_fmt_q(getattr(self.sheet(doc), alias))}."
        if not known:
            head += " (Era um parâmetro novo: ligue-o a alguma propriedade com liga.)"
        if backup_note:
            head += " " + backup_note
        return True, head + "\n" + self.verification(doc), []

    # --- imagem ------------------------------------------------------------------
    def screenshot(self, vista="isometrica", size=768, keep_camera=True):
        """PNG da vista 3D (enquadra, fotografa fora da tela e devolve a câmera do usuário)."""
        if Gui is None:
            return None, "sem interface gráfica"
        try:
            view = Gui.activeView()
        except Exception:
            view = None
        if view is None or not hasattr(view, "saveImage"):
            return None, "a aba da frente não é uma vista 3D"
        cam = view.getCamera() if keep_camera else None
        anim = False
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            if hasattr(view, "isAnimationEnabled") and hasattr(view, "setAnimationEnabled"):
                # com a animação de navegação ligada, viewIsometric/fitAll só começam a mover a câmera:
                # a foto sairia na câmera do usuário e a animação mexeria a vista depois do setCamera
                anim = bool(view.isAnimationEnabled())
                if anim:
                    view.setAnimationEnabled(False)
            getattr(view, {"frente": "viewFront", "topo": "viewTop", "direita": "viewRight"}
                    .get(vista, "viewIsometric"))()
            view.fitAll()
            w, h = view.getSize()
            k = min(1.0, size / max(w, h, 1))
            if os.environ.get("WAYLAND_DISPLAY"):           # offscreen sai preto no Wayland
                Gui.updateGui()
                view.saveImage(path, int(w * k), int(h * k), "Current", "Framebuffer")
            else:
                view.saveImage(path, int(w * k), int(h * k), "Current")
            with open(path, "rb") as f:
                return f.read(), ""
        except Exception as e:
            return None, f"não consegui fotografar a vista: {_err_text(e)}"
        finally:
            if cam:
                try:
                    view.setCamera(cam)
                except Exception:
                    pass
            if anim:
                try:
                    view.setAnimationEnabled(True)
                except Exception:
                    pass
            try:
                os.remove(path)
            except OSError:
                pass


def _linked_objects(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out += _linked_objects(v)
        return out
    if hasattr(value, "TypeId"):
        return [value]
    return []


def _in_body(o):
    try:
        parent = o.getParentGeoFeatureGroup()
        return parent is not None and parent.TypeId == "PartDesign::Body"
    except Exception:
        return False
