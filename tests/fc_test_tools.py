"""Testes das ferramentas dentro do FreeCAD sem interface.
Rodar (isolando a pasta de usuário do FreeCAD):
  set FREECAD_USER_HOME=<pasta temporária>
  freecadcmd -c "exec(open(r'<repo>/tests/fc_test_tools.py', encoding='utf-8').read())"
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.environ["CHATCAD_TESTS"]
sys.path.insert(0, os.path.dirname(HERE))

import FreeCAD as App  # noqa: E402
import chatcad_fctools as T  # noqa: E402

RESULTS = []


def check(name, cond, info=""):
    RESULTS.append((name, bool(cond), info))


def fresh():
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    doc = App.newDocument("Teste")
    doc.UndoMode = 1
    tools = T.FreeCADTools()
    tools.begin_turn("teste de ferramentas")
    return doc, tools


GOOD = '''param("largura", 60); param("profundidade", 40); param("espessura", 4)
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
print("pronto")
'''

# código real gerado pelo qwen3:8b (último passo aponta Base para ele mesmo)
QWEN3_8B = '''param("largura", 60); param("profundidade", 40); param("altura", 4); param("furo_d", 4); param("distancia", 8)
placa = doc.addObject("Part::Box", "Placa")
liga(placa, Length="largura", Width="profundidade", Height="altura")
furo1 = doc.addObject("Part::Cylinder", "Furo1")
liga(furo1, Radius="furo_d / 2", Height="altura", x="distancia", y="profundidade / 2")
furo2 = doc.addObject("Part::Cylinder", "Furo2")
liga(furo2, Radius="furo_d / 2", Height="altura", x="largura - distancia", y="profundidade / 2")
peca = doc.addObject("Part::Cut", "Peca")
peca.Base = placa
peca.Tool = furo1
peca = doc.addObject("Part::Cut", "Peca2")
peca.Base = peca
peca.Tool = furo2
'''

# código real gerado pelo qwen2.5:7b (usa os parâmetros como variáveis e sobrescreve Tool)
QWEN25_7B = '''param('largura', 60); param('altura', 40); param('espessura', 4); param('furo_d', 4); param('distancia_furo', 8)
suporte = doc.addObject('Part::Box', 'Suporte')
suporte.Length = largura
suporte.Width = altura
suporte.Height = espessura
liga(suporte, Length='largura', Width='altura', Height='espessura')

furo1 = doc.addObject('Part::Cylinder', 'Furo1')
furo1.Radius = furo_d / 2
furo1.Height = espessura
furo1.Placement = App.Placement(Vector(distancia_furo, distancia_furo, 0), App.Rotation())

furo2 = doc.addObject('Part::Cylinder', 'Furo2')
furo2.Radius = furo_d / 2
furo2.Height = espessura
furo2.Placement = App.Placement(Vector(largura - distancia_furo, distancia_furo, 0), App.Rotation())

peca = doc.addObject('Part::Cut', 'Peca')
peca.Base = suporte
peca.Tool = furo1
peca.Tool = furo2
'''


def run_all():
    # 1. código bom: paramétrico, sólido único, auxiliares ocultos
    doc, t = fresh()
    ok, text, _ = t.run_python(GOOD, "placa com furos")
    check("good_ok", ok, text)
    check("good_print", "pronto" in text, text)
    check("good_single_solid", "sólido único válido" in text and "60.0 x 40.0 x 4.0" in text, text)
    check("good_hidden", "Furo1" in text.split("Ocultei")[-1] and not doc.Placa.Visibility, text)
    check("good_params", "largura=60 mm" in text, text)
    check("final_warnings_clean", t.final_warnings() == "", t.final_warnings())
    check("good_expr", doc.getObject("Furo2") and dict(doc.Furo2.ExpressionEngine).get(".Placement.Base.x") == "Parametros.largura - Parametros.margem",
          str(doc.Furo2.ExpressionEngine))
    # 2. set_parameter muda a peça; Ctrl+Z (doc.undo) volta
    ok, text, _ = t.set_parameter("largura", "80 mm")
    check("setparam_ok", ok and abs(doc.Peca.Shape.BoundBox.XLength - 80) < 1e-6, text)
    check("setparam_hole_follows", abs(doc.Furo2.Placement.Base.x - 72) < 1e-6, str(doc.Furo2.Placement))
    doc.undo()
    doc.recompute()
    check("undo_restores", abs(doc.Peca.Shape.BoundBox.XLength - 60) < 1e-6, str(doc.Peca.Shape.BoundBox))
    check("undo_names", True, str(doc.UndoNames[:3]) + " / redo " + str(doc.RedoNames[:3]))
    # 3. erro no meio do código: tudo desfeito
    n = len(doc.Objects)
    ok, text, _ = t.run_python('a = doc.addObject("Part::Box", "Temp")\nprint("antes")\nx = 1 / 0\n')
    check("runtime_error_reported", not ok and "ZeroDivisionError" in text and "linha 3" in text, text)
    check("runtime_error_rollback", len(doc.Objects) == n and doc.getObject("Temp") is None, text)
    check("runtime_error_output", "antes" in text, text)
    # 4. erro de sintaxe
    ok, text, _ = t.run_python('x = (1,\nprint(x)\n')
    check("syntax_error", not ok and "SyntaxError" in text, text)
    # 5. laço infinito
    import time as _t
    t2 = T.FreeCADTools(max_lines=200000)
    t2.begin_turn("laco")
    t0 = _t.monotonic()
    ok, text, _ = t2.run_python("i = 0\nwhile True:\n    i += 1\n")
    text += f" [{_t.monotonic() - t0:.2f}s]"
    check("timeout", not ok and "TimeoutError" in text, text)
    RESULTS.append(("timeout_text", True, text))
    # 6. sys.exit não derruba o FreeCAD
    ok, text, _ = t.run_python("import sys\nsys.exit(3)\n")
    check("sysexit_caught", not ok and "SystemExit" in text, text)
    # 7. liga com nome inexistente
    ok, text, _ = t.run_python('liga(doc.Placa, Length="comprimento_total")')
    check("liga_unknown_name", not ok and "comprimento_total" in text and "param(" in text, text)
    # 8. liga em propriedade que não existe
    ok, text, _ = t.run_python('liga(doc.Placa, Radius="largura")')
    check("liga_bad_prop", not ok and "Length" in text, text)
    # 8b. erros reais do qwen3:8b: texto num Vector e Radius num Part::Fillet -> dica em português
    ok, text, _ = t.run_python('b = doc.addObject("Part::Box", "TmpB")\n'
                               'b.Placement.Base = App.Vector(0, "profundidade" + "espessura", 0)\n')
    check("hint_vector_text", not ok and "Dica" in text and "liga(" in text and doc.getObject("TmpB") is None, text)
    ok, text, _ = t.run_python('b = doc.addObject("Part::Box", "TmpP")\nb.Placement = App.Vector(0, 37, 0)\n')
    check("hint_placement_vector", not ok and "Dica" in text and "App.Placement(" in text, text)
    ok, text, _ = t.run_python('f = doc.addObject("Part::Fillet", "TmpF")\nf.Radius = 10\n')
    check("hint_fillet_radius", not ok and "Dica" in text and "f.Edges" in text, text)
    # 8c. ajudantes de arredondamento: acha arestas pela posição e arredonda (cantos de cima de uma placa)
    ok, text, _ = t.run_python(
        'pl = doc.addObject("Part::Box", "PlacaArr")\npl.Length = 60\npl.Width = 3\npl.Height = 95\n'
        'pl.Placement.Base = App.Vector(200, 37, 0)\ndoc.recompute()\n'
        'ns = arestas(pl, eixo="y", z=95)\nprint("arestas", ns)\n'
        'arr = arredonda(pl, 10, ns, "PlacaArredondada")\n')
    import math as _m
    arr = doc.getObject("PlacaArredondada")
    want = 60 * 3 * 95 - 2 * (1 - _m.pi / 4) * 100 * 3
    check("arestas_arredonda", ok and arr is not None and arr.Shape.isValid()
          and abs(arr.Shape.Volume - want) < 1e-3 and "arestas [" in text, (text, arr and arr.Shape.Volume, want))
    ok, text, _ = t.run_python('arredonda(doc.PlacaArr, 2, [999])\n')
    check("arredonda_bad_edges", not ok and "arestas de 1 a" in text, text)
    # 9. alias com acento e espaço
    ok, text, _ = t.run_python('print(param("Diâmetro do pino", 3.5))')
    check("alias_normalized", ok and "Parametros.diametro_do_pino" in text, text)
    # 9b. o modelo cria "diâmetro" e usa "diâmetro" no liga e no Python (caso real do qwen3:8b)
    ok, text, _ = t.run_python('param("diâmetro", 40)\ncil = doc.addObject("Part::Cylinder", "Cil")\n'
                               'liga(cil, Radius="diâmetro / 2", Height="espessura")\nprint(diâmetro)')
    check("accent_alias_in_liga", ok and dict(doc.Cil.ExpressionEngine).get("Radius") == "Parametros.diametro / 2"
          and "40" in text, text)
    ok, text, _ = t.run_python('liga(doc.Placa, Length="comprimento")')
    check("nameerror_lists_params", not ok and "Parâmetros que existem" in text and "largura" in text, text)
    check("good_no_cut_hint", not t.cut_hints(doc, {"Peca"}), t.cut_hints(doc, {"Peca"}))
    # 9b2. furo fora do centro (caso real do qwen3:8b/qwen2.5:7b): sólido válido, mas com dica
    ok, text, _ = t.run_python('a = doc.addObject("Part::Cylinder", "Disco")\na.Radius = 20\na.Height = 15\n'
                               'b = doc.addObject("Part::Cylinder", "FuroX")\nb.Radius = 5\nb.Height = 15\n'
                               'b.Placement.Base = Vector(15, 7.5, 0)\nc = doc.addObject("Part::Cut", "Disco2")\n'
                               'c.Base = a\nc.Tool = b\n')
    check("off_center_hint", ok and "FuroX sai para fora de Disco" in text, text)
    ok, text, _ = t.run_python('p = doc.addObject("Part::Box", "Chapa")\np.Length = 20\np.Width = 20\np.Height = 4\n'
                               'h = doc.addObject("Part::Cylinder", "FuroAlto")\nh.Radius = 3\nh.Height = 10\n'
                               'h.Placement.Base = Vector(10, 10, -3)\nk = doc.addObject("Part::Cut", "Chapa2")\n'
                               'k.Base = p\nk.Tool = h\n')
    check("tall_tool_no_hint", ok and "sai para fora" not in text, text)
    # 9c. código roda mas o recálculo quebra (caso real: MultiFuse com uma forma só) -> desfeito + dica
    n = len(doc.Objects)
    ok, text, _ = t.run_python('f = doc.addObject("Part::Cylinder", "F")\nm = doc.addObject("Part::MultiFuse", "M")\n'
                               'm.Shapes = [f]\nc = doc.addObject("Part::Cut", "C")\nc.Base = doc.Placa\nc.Tool = m\n')
    check("recompute_error_rollback", not ok and len(doc.Objects) == n and "Dica" in text and "Tool" in text, text)
    # 9d. set_parameter que quebra a peça não é aplicado
    ok, text, _ = t.set_parameter("espessura", 0)
    check("setparam_breaks_rollback", not ok and abs(doc.Peca.Shape.BoundBox.ZLength - 4) < 1e-6, text)
    # 10. get_document e detalhe com arestas numeradas
    info = t.document_text()
    check("get_document", "Parametros" in info and "Peca" in info and "Furo1" in info, info)
    detail = t.document_text("Peca")
    check("get_document_detail", "Arestas" in detail and "Face1" in detail, detail[:400])
    check("get_document_unknown", "não existe" in t.document_text("Nada"))

    # 11. código real do qwen3:8b (Base = ele mesmo)
    doc, t = fresh()
    ok, text, _ = t.run_python(QWEN3_8B)
    check("qwen3_selfref_detected", (not ok) or "ERRO" in text or "OBJETOS COM ERRO" in text, text)
    RESULTS.append(("qwen3_selfref_text", True, text))
    # 12. código real do qwen2.5:7b (variáveis + Tool sobrescrito): roda, mas a verificação acusa 2 peças
    doc, t = fresh()
    ok, text, _ = t.run_python(QWEN25_7B)
    check("qwen25_runs_with_vars", ok, text)
    check("qwen25_two_parts_warned", "peças visíveis" in text, text)
    check("final_warnings_flags_two_parts", "peças visíveis separadas" in t.final_warnings(), t.final_warnings())
    # 13. documento sem undo: remove o que criou
    doc, t = fresh()
    doc.UndoMode = 0
    ok, text, _ = t.run_python('doc.addObject("Part::Box", "B1")\nraise ValueError("x")\n')
    check("no_undo_fallback", not ok and doc.getObject("B1") is None, text)
    # 14. sem documento aberto: cria "Peca"
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    t = T.FreeCADTools()
    t.begin_turn("cubo")
    ok, text, _ = t.run_python('param("lado", 20)\nc = doc.addObject("Part::Box", "Cubo")\n'
                               'liga(c, Length="lado", Width="lado", Height="lado")\n')
    check("auto_doc", ok and App.ActiveDocument.Name.startswith("Peca"), text)
    # 15. backup do arquivo salvo antes da primeira mudança do turno
    import tempfile
    folder = tempfile.mkdtemp()
    doc = App.ActiveDocument
    doc.saveAs(os.path.join(folder, "suporte.FCStd"))
    t.begin_turn("Faça um furo de 5 mm no topo")
    ok, text, _ = t.set_parameter("lado", 25)
    files = sorted(os.listdir(folder))
    check("backup_created", any(f.startswith("suporte.antes-") for f in files), str(files))
    check("end_turn_saves", "salvo" in t.end_turn(), str(files))


def saved_doc(folder, name):
    doc = App.newDocument(name)
    doc.UndoMode = 1
    doc.addObject("Part::Box", "Base0")
    doc.recompute()
    doc.saveAs(os.path.join(folder, name + ".FCStd"))
    return doc


def t_namespace_rollback():
    # C2: passo desfeito leva junto as variáveis; param() recusado não deixa o valor recusado
    doc, t = fresh()
    ok, text, _ = t.run_python(GOOD)
    ok, text, _ = t.run_python('extra = doc.addObject("Part::Box", "Extra")\nplaca = None\nx = 1 / 0\n')
    check("ns_rollback_new_var", not ok and "extra" not in t.ns, sorted(k for k in t.ns if not k.startswith("_")))
    check("ns_rollback_rebind", t.ns.get("placa") is not None and t.ns["placa"].Name == "Placa", str(t.ns.get("placa")))
    ok, text, _ = t.set_parameter("espessura", 0)
    check("ns_rollback_setparam", not ok and t.ns.get("espessura") == 4, f"{t.ns.get('espessura')} {text}")
    ok, text, _ = t.run_python('param("espessura", 0)\n')            # quebra no recálculo -> desfeito
    check("ns_rollback_param_in_step", not ok and t.ns.get("espessura") == 4, f"{t.ns.get('espessura')} {text}")
    ok, text, _ = t.run_python("math = 5\n")
    ok, text, _ = t.run_python("print(math.pi)\n")
    check("ns_fixed_reinjected", ok and "3.14" in text, text)


def t_unit_aliases():
    # C6: nomes que são unidades (h, m...) viram "<nome>_p" e continuam achados depois
    doc, t = fresh()
    ok, text, _ = t.run_python('param("h", 20)\nc = doc.addObject("Part::Box", "CaixaH")\nliga(c, Height="h")\n')
    check("unit_alias_same_step", ok and dict(doc.CaixaH.ExpressionEngine).get("Height") == "Parametros.h_p", text)
    ok, text, _ = t.run_python('param("h", 25)\n')
    check("unit_alias_resend", ok and abs(doc.CaixaH.Height.Value - 25) < 1e-6, text)
    ok, text, _ = t.set_parameter("h", 30)
    check("unit_alias_setparam", ok and "Atualizei" in text and abs(doc.CaixaH.Height.Value - 30) < 1e-6, text)
    t.run_python('param("m", 3)\n')
    q = t.qualify("2 m + m", doc)
    check("unit_word_after_number", q == "2 m + Parametros.m_p", q)


def t_default_unit():
    # C7: só palavra inteira vira ângulo
    got = {a: T.default_unit(a) for a in ("espessura_flange", "largura_retangulo", "lado_triangulo", "manga_d",
                                          "altura_degrau", "angulo", "angulo_dobra", "inclinacao", "ang", "rotacao_2")}
    want = {"espessura_flange": "mm", "largura_retangulo": "mm", "lado_triangulo": "mm", "manga_d": "mm",
            "altura_degrau": "mm", "angulo": "deg", "angulo_dobra": "deg", "inclinacao": "deg", "ang": "deg",
            "rotacao_2": "deg"}
    check("default_unit_tokens", got == want, str(got))


def t_deadline():
    # C8: `except Exception` não engole o limite; `except:` puro é pego pelo flag
    t2 = T.FreeCADTools(max_lines=200000)
    t2.begin_turn("laco")
    fresh_doc = App.ActiveDocument
    ok, text, _ = t2.run_python("n = 0\nwhile True:\n    try:\n        n += 1\n    except Exception as e:\n        pass\n")
    check("timeout_not_caught_by_except_exception", not ok and "TimeoutError" in text, text)
    ok, text, _ = t2.run_python("for i in range(300000):\n    try:\n        x = i\n    except:\n        pass\n")
    check("timeout_bare_except_flagged", not ok and "TimeoutError" in text, text)
    # C9: função definida num passo anterior também é rastreada
    ok, text, _ = t2.run_python("def gira(n):\n    i = 0\n    while i < n:\n        pass\n")
    ok2, text2, _ = t2.run_python("gira(4)\n")
    check("timeout_old_step_function", ok and not ok2 and "TimeoutError" in text2, text + " / " + text2)
    check("timeout_doc_untouched", fresh_doc is None or fresh_doc.Name in App.listDocuments(), "")


def t_backup_failure(folder):
    # C10: backup que falha não derruba o passo
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    saved_doc(folder, "rofail")
    t = T.FreeCADTools()
    t.begin_turn("furo no topo")
    orig = T.shutil.copy2

    def boom(*a, **k):
        raise PermissionError(13, "Permissão negada")
    T.shutil.copy2 = boom
    try:
        ok, text, _ = t.run_python('doc.addObject("Part::Box", "Novo")\n')
    finally:
        T.shutil.copy2 = orig
    check("backup_failure_not_fatal", ok and "backup não foi possível" in text and App.ActiveDocument.getObject("Novo"), text)


def t_pinned_doc(folder):
    # C11: o turno fica no documento em que começou e salva só esse
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    a = saved_doc(folder, "agente")
    b = saved_doc(folder, "usuario")
    App.setActiveDocument("agente")
    t = T.FreeCADTools()
    t.begin_turn("mais um bloco")
    App.setActiveDocument("usuario")               # o usuário troca de aba enquanto o modelo pensa
    b.addObject("Part::Box", "EdicaoDoUsuario")    # edição dele, não salva
    mb = os.path.getmtime(b.FileName)
    ok, text, _ = t.run_python('doc.addObject("Part::Box", "DoAgente")\n')
    check("pinned_doc_used", ok and a.getObject("DoAgente") and not b.getObject("DoAgente"), text)
    ma = os.path.getmtime(a.FileName)
    note = t.end_turn()
    check("end_turn_saves_turn_doc", "agente.FCStd" in note and os.path.getmtime(b.FileName) == mb
          and os.path.getmtime(a.FileName) != ma, note)
    # C25: código que cria outro documento e falha: o documento some; com sucesso o diff é do doc do turno
    App.setActiveDocument("agente")
    ok, text, _ = t.run_python('d = App.newDocument("Suporte")\nd.addObject("Part::Box", "X")\nx = 1 / 0\n')
    check("abort_closes_created_doc", not ok and not any(n.startswith("Suporte") for n in App.listDocuments()), text)
    ok, text, _ = t.run_python('d = App.newDocument("Outro")\nb2 = doc.addObject("Part::Box", "Bx")\n')
    check("diff_uses_turn_doc", ok and "+Bx" in text and "-Base0" not in text and "-DoAgente" not in text, text)
    for n in [n for n in App.listDocuments() if n.startswith("Outro")]:
        App.closeDocument(n)


def t_no_undo():
    # C26: undo desligado: o passo com erro é desfeito por inteiro (inclusive remoções e parâmetros)
    doc, t = fresh()
    t.run_python(GOOD)
    doc.UndoMode = 0
    ok, text, _ = t.run_python('doc.removeObject("Peca")\nparam("largura", 99)\nx = 1 / 0\n')
    check("no_undo_full_rollback", not ok and doc.getObject("Peca") is not None
          and abs(doc.Parametros.largura.Value - 60) < 1e-6 and doc.UndoMode == 0, text)
    ok, text, _ = t.set_parameter("espessura", 0)
    check("no_undo_setparam_rollback", not ok and abs(doc.Parametros.espessura.Value - 4) < 1e-6
          and abs(doc.Peca.Shape.BoundBox.ZLength - 4) < 1e-6, text)


def t_read_only(folder):
    # C27: passo só de leitura não marca o turno como mudado nem deixa backup
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    saved_doc(folder, "leitura")
    t = T.FreeCADTools()
    t.begin_turn("qual o volume")
    ok, text, _ = t.run_python("print(doc.Base0.Shape.Volume)\n")
    files = sorted(os.listdir(folder))
    check("read_only_not_changed", ok and not t.turn["changed"] and "1000" in text, text)
    check("read_only_no_backup", not any(f.startswith("leitura.antes-") for f in files), str(files))
    check("read_only_no_save", t.end_turn() == "", "")
    ok, text, _ = t.run_python('doc.Base0.Length = 20\n')
    files = sorted(os.listdir(folder))
    check("change_after_read_backs_up", ok and t.turn["changed"] and any(f.startswith("leitura.antes-") for f in files),
          str(files) + " " + text)


def t_param_units_precision():
    doc, t = fresh()
    # C28: atualizar mantém a unidade que a célula já tinha
    t.run_python('param("furos", 4, unidade="")\nparam("caimento", 30, unidade="deg")\n')
    t.set_parameter("furos", 6)
    t.set_parameter("caimento", 45)
    c1 = doc.Parametros.getContents(doc.Parametros.getCellFromAlias("furos"))
    c2 = doc.Parametros.getContents(doc.Parametros.getCellFromAlias("caimento"))
    check("update_keeps_unit", c1 in ("=6", "6") and c2 == "=45 deg", f"{c1} / {c2}")
    # C29: precisão total
    got = (T.cell_text(1250.125, "comprimento"), T.cell_text(40 / 3, "x"), T.cell_text(60.0, "x"),
           T.cell_text(1234567, "x"), t.qualify(31.41592653, doc))
    check("full_precision", got == ("=1250.125 mm", "=13.333333333333334 mm", "=60 mm", "=1234567 mm", "31.41592653"),
          str(got))


def t_user_sheet():
    # C31: planilha do usuário com alias na coluna C (nome em A, unidade em B)
    doc, t = fresh()
    sh = doc.addObject("Spreadsheet::Sheet", "Parametros")
    sh.set("A5", "largura")
    sh.set("B5", "mm")
    sh.set("C5", "=50 mm")
    sh.setAlias("C5", "largura")
    doc.recompute()
    ok, text, _ = t.set_parameter("largura", 80, "largura da placa")
    check("user_sheet_value_kept", ok and sh.getContents("C5") == "=80 mm", sh.getContents("C5") + " " + text)
    ok, text, _ = t.run_python('b = doc.addObject("Part::Box", "B")\nliga(b, Length="largura")\n')
    check("user_sheet_liga", ok and abs(doc.B.Length.Value - 80) < 1e-6, text)
    check("user_sheet_params", "largura=80 mm" in t.params_text(doc), t.params_text(doc))


def t_screenshot_animation():
    # C30: animação desligada durante a foto e religada depois; câmera do usuário de volta
    log = []

    class View:
        anim = True

        def getCamera(self):
            return "camera-do-usuario"

        def setCamera(self, c):
            log.append(("setCamera", c, self.anim))

        def isAnimationEnabled(self):
            return self.anim

        def setAnimationEnabled(self, v):
            self.anim = v
            log.append(("anim", v))

        def viewIsometric(self):
            log.append(("iso", self.anim))

        def fitAll(self):
            log.append(("fit", self.anim))

        def getSize(self):
            return 400, 300

        def saveImage(self, path, w, h, bg, *a):
            with open(path, "wb") as f:
                f.write(b"PNG")

    view = View()

    class FakeGui:
        @staticmethod
        def activeView():
            return view

    old = T.Gui
    T.Gui = FakeGui
    try:
        png, err = T.FreeCADTools().screenshot()
    finally:
        T.Gui = old
    check("screenshot_no_animation", png == b"PNG" and ("iso", False) in log and ("fit", False) in log
          and log[-1] == ("anim", True) and ("setCamera", "camera-do-usuario", False) in log and view.anim, str(log))


def t_ignored_selection():
    """C17: com o chip "Ignorando" a seleção não entra no contexto, no get_document nem em `sel`."""
    doc, tools = fresh()
    box = doc.addObject("Part::Box", "Caixa")
    doc.recompute()

    class Sel:
        Object, ObjectName, SubElementNames, PickedPoints = box, "Caixa", ("Face6",), ()

    class FakeGui:
        class Selection:
            @staticmethod
            def getSelectionEx(*a):
                return [Sel()]

    old = T.Gui
    T.Gui = FakeGui
    try:
        tools.begin_turn("use a face")
        on = (tools.context_text(), tools.document_text(), tools.selection())
        tools.begin_turn("ignore a face", selection=False)
        off = (tools.context_text(), tools.document_text(), tools.selection())
    finally:
        T.Gui = old
    check("selection_in_context", "Face6" in on[0] and "Face6" in on[1] and len(on[2]) == 1, on[0])
    check("selection_ignored", "Face6" not in off[0] and "Face6" not in off[1] and off[2] == []
          and "Caixa" in off[1], off)


def run_review():
    import tempfile
    folder = tempfile.mkdtemp()
    for fn, args in ((t_namespace_rollback, ()), (t_unit_aliases, ()), (t_default_unit, ()), (t_deadline, ()),
                     (t_backup_failure, (folder,)), (t_pinned_doc, (folder,)), (t_no_undo, ()),
                     (t_read_only, (tempfile.mkdtemp(),)), (t_param_units_precision, ()), (t_user_sheet, ()),
                     (t_screenshot_animation, ()), (t_ignored_selection, ())):
        try:
            fn(*args)
        except Exception:
            RESULTS.append((f"crash_{fn.__name__}", False, traceback.format_exc()))


try:
    run_all()
except Exception:
    RESULTS.append(("crash", False, traceback.format_exc()))
run_review()

out_path = os.environ.get("CHATCAD_TEST_OUT", os.path.join(HERE, "fc_results.txt"))
with open(out_path, "w", encoding="utf-8") as f:
    for name, ok, info in RESULTS:
        f.write(f"{'PASS' if ok else 'FAIL'} {name}\n")
        if not ok or name.endswith("_text") or name == "undo_names":
            f.write("    " + str(info).replace("\n", "\n    ") + "\n")
    f.write(f"\n{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} ok\n")
