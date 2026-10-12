import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chatcad_llm as L  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
ALLOWED = {"run_python", "get_document", "set_parameter"}


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        data = f.read()
    return data[:data.rfind(b"\n") + 1]          # gravações cortadas: só linhas inteiras


def run_stream(parser, data, size=7):
    ev = []
    for i in range(0, len(data), size):           # pedaços pequenos: corta linhas e UTF-8 no meio
        ev += parser.feed(data[i:i + size])
    return ev + parser.flush()


class Streams(unittest.TestCase):
    def test_ollama_tool_call_real_recording(self):
        ev = run_stream(L.NdjsonStream(), fixture("ollama_qwen3_8b_toolcall.ndjson"))
        tools = [e[1] for e in ev if e[0] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "run_python")
        self.assertIn("liga(placa", tools[0]["arguments"]["code"])
        done = [e[1] for e in ev if e[0] == "done"][0]
        self.assertEqual(done["reason"], "stop")
        self.assertGreater(done["prompt_tokens"], 1000)

    def test_ollama_thinking_split(self):
        ev = run_stream(L.NdjsonStream(), fixture("ollama_qwen3_4b_thinking_partial.ndjson"), size=13)
        kinds = {e[0] for e in ev}
        self.assertEqual(kinds, {"think"})            # com think=true nada vaza no conteúdo

    def test_ollama_error_line(self):
        ev = run_stream(L.NdjsonStream(), b'{"error":"model \'x\' not found"}\n')
        self.assertEqual(ev, [("error", "model 'x' not found")])

    def test_openai_sse_real_recording(self):
        ev = run_stream(L.SseStream(), fixture("openai_qwen25_toolcall.sse"), size=5)
        tools = [e[1] for e in ev if e[0] == "tool"]
        self.assertEqual(tools[0]["name"], "run_python")
        args, err = L.parse_args(tools[0]["arguments"])
        self.assertEqual(err, "")
        self.assertIn("code", args)
        self.assertEqual(ev[-1][0], "done")

    def test_openai_streamed_argument_fragments(self):
        lines = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "run_python", "arguments": "{\"co"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                "arguments": "de\": \"print(1)\"}"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {
                "name": "get_document", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        data = b"".join(b"data: " + json.dumps(x).encode() + b"\n\n" for x in lines) + b"data: [DONE]\n\n"
        ev = run_stream(L.SseStream(), data, size=3)
        tools = [e[1] for e in ev if e[0] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["run_python", "get_document"])
        self.assertEqual(L.parse_args(tools[0]["arguments"])[0], {"code": "print(1)"})

    def test_openai_stream_without_done_marker(self):
        ev = run_stream(L.SseStream(), b'data: {"choices":[{"delta":{"content":"oi"}}]}\n\n')
        self.assertEqual(ev[0], ("text", "oi"))
        self.assertEqual(ev[-1][0], "done")


class Think(unittest.TestCase):
    def test_tags_split_across_chunks(self):
        sp = L.ThinkSplitter()
        out = []
        for piece in ["Oi <thi", "nk>pensando", " muito</th", "ink>Resposta"]:
            out += sp.feed(piece)
        out += sp.flush()
        self.assertEqual("".join(s for k, s in out if k == "think"), "pensando muito")
        self.assertEqual("".join(s for k, s in out if k == "text"), "Oi Resposta")

    def test_implicit_open(self):
        sp = L.ThinkSplitter(inside=True)
        out = sp.feed("raciocínio</think>\n\nFeito.") + sp.flush()
        self.assertEqual([k for k, _ in out], ["think", "text"])

    def test_think_param(self):
        q34, q38, q25 = {"thinking": [True]}, {"thinking": [False, True]}, {"thinking": []}
        self.assertIs(L.think_param(q34, "off", False), True)
        self.assertIs(L.think_param(q38, "off", False), False)
        self.assertIs(L.think_param(q38, "auto", True), True)
        self.assertIsNone(L.think_param(q25, "on", False))
        self.assertEqual(L.think_param({"thinking": ["low", "medium", "high"]}, "off", False), "low")
        # qwen3.8:27b no Ollama 0.40: [False, "low", "medium", "xhigh"] -> desligado de verdade quando "off"
        q388 = {"thinking": [False, "low", "medium", "xhigh"]}
        self.assertIs(L.think_param(q388, "off", False), False)
        self.assertEqual(L.think_param(q388, "auto", True), "medium")
        self.assertEqual(L.think_param(q388, "on", False), "xhigh")
        self.assertEqual(L.think_param({"thinking": ["low", "medium", "high"]}, "on", False), "high")


class Args(unittest.TestCase):
    def test_raw_newlines_in_json_string(self):
        self.assertEqual(L.parse_args('{"code": "a = 1\nprint(a)"}')[0]["code"], "a = 1\nprint(a)")

    def test_fences_trailing_comma_double_encoding_python_dict(self):
        self.assertEqual(L.parse_args('```json\n{"code": "x",}\n```')[0], {"code": "x"})
        self.assertEqual(L.parse_args(json.dumps(json.dumps({"code": "x"})))[0], {"code": "x"})
        self.assertEqual(L.parse_args("{'code': 'x', 'ok': True}")[0], {"code": "x", "ok": True})
        self.assertIsNone(L.parse_args('{"code": ')[0])

    def test_normalize_aliases_and_unwrap(self):
        tool, args, err = L.normalize_call("execute_python", {"codigo": "doc.recompute()"}, ALLOWED)
        self.assertEqual((tool, err), ("run_python", ""))
        self.assertEqual(args["code"], "doc.recompute()")
        _, args, _ = L.normalize_call("run_python", {"arguments": '{"code": "print(1)"}'}, ALLOWED)
        self.assertEqual(args, {"code": "print(1)"})
        _, args, _ = L.normalize_call("set_parameter", {"name": "largura", "value": 45}, ALLOWED)
        self.assertEqual(args, {"nome": "largura", "valor": "45"})
        self.assertEqual(L.normalize_call("mcp__freecad__execute_code", {"code": "x=1"}, ALLOWED)[0],
                         "run_python")

    def test_double_escaped_code_and_fenced_code(self):
        _, args, _ = L.normalize_call("run_python", {"code": "a = 1\\nprint(a)"}, ALLOWED)
        self.assertEqual(args["code"], "a = 1\nprint(a)")
        _, args, _ = L.normalize_call("run_python", {"code": "```python\nprint(1)\n```"}, ALLOWED)
        self.assertEqual(args["code"], "print(1)")

    def test_unknown_missing_and_plan_mode(self):
        self.assertIsNone(L.normalize_call("apagar_tudo", {}, ALLOWED)[0])
        self.assertIn("faltou", L.normalize_call("run_python", {}, ALLOWED)[2])
        self.assertIsNone(L.normalize_call("run_python", {"code": "x"}, {"get_document"})[0])

    def test_raw_python_string_as_args(self):
        tool, args, err = L.normalize_call("run_python", 'doc.addObject("Part::Box", "B")', ALLOWED)
        self.assertEqual(err, "")
        self.assertIn("addObject", args["code"])

    def test_text_tool_calls(self):
        text = ('Vou criar.\n<tool_call>\n{"name": "run_python", "arguments": {"code": "print(1)"}}\n'
                '</tool_call>')
        calls, cleaned = L.extract_text_calls(text, ALLOWED)
        self.assertEqual(calls[0]["name"], "run_python")
        self.assertEqual(cleaned, "Vou criar.")
        calls, _ = L.extract_text_calls('{"name": "get_document", "arguments": {}}', ALLOWED)
        self.assertEqual(calls[0]["name"], "get_document")
        self.assertEqual(L.extract_text_calls("Uma resposta normal {com chaves}", ALLOWED)[0], [])
        self.assertTrue(L.showed_code_instead_of_calling(
            "Use:\n```python\ncaixa = doc.addObject('Part::Box')\n```"))

    def test_xml_style_text_calls(self):
        # formato que o qwen3.5 às vezes vaza no conteúdo (issues #14493/#14745 do Ollama)
        text = ("Vou criar a placa.\n<tool_call>\n<function=run_python>\n<parameter=code>\n"
                "placa = doc.addObject(\"Part::Box\", \"Placa\")\nplaca.Length = 60\n</parameter>\n"
                "<parameter=resumo>\nCria a placa\n</parameter>\n</function>\n</tool_call>")
        calls, cleaned = L.extract_text_calls(text, ALLOWED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "run_python")
        self.assertIn("placa.Length = 60", calls[0]["arguments"]["code"])
        self.assertEqual(calls[0]["arguments"]["resumo"], "Cria a placa")
        self.assertEqual(cleaned, "Vou criar a placa.")
        tool, args, err = L.normalize_call(calls[0]["name"], calls[0]["arguments"], ALLOWED)
        self.assertEqual((tool, err), ("run_python", ""))
        # o filtro esconde o bloco mesmo com as marcações partidas entre os pedaços do stream
        h = L.HideTags()
        chunks = ["Vou criar a placa.\n<tool", "_call>\n<funct", "ion=run_python>\n<parameter=code>\nx=1",
                  "\n</parameter>\n</func", "tion>\n</tool_", "call> Pronto."]
        self.assertEqual("".join(h.feed(c) for c in chunks), "Vou criar a placa.\n Pronto.")
        # formato do Llama 3.x: corpo em JSON (C22)
        llama = 'Vou criar.<function=run_python>{"code": "caixa = doc.addObject(\'Part::Box\')"}</function>'
        calls, cleaned = L.extract_text_calls(llama, ALLOWED)
        self.assertEqual(calls[0]["arguments"], {"code": "caixa = doc.addObject('Part::Box')"})
        self.assertEqual(cleaned, "Vou criar.")
        self.assertEqual(L.normalize_call("run_python", calls[0]["arguments"], ALLOWED)[2], "")
        wrapped = '<function=run_python>{"name": "run_python", "arguments": {"code": "x = 1"}}</function>'
        self.assertEqual(L.extract_text_calls(wrapped, ALLOWED)[0][0]["arguments"], {"code": "x = 1"})
        self.assertEqual(L.extract_text_calls("<function=get_document></function>", ALLOWED)[0][0]["arguments"], {})
        # nome de ferramenta desconhecido não vira chamada
        self.assertEqual(L.extract_text_calls("<function=apagar_tudo><parameter=x>1</parameter></function>",
                                              ALLOWED)[0], [])


class Fences(unittest.TestCase):
    def test_fence_filter_streaming(self):
        f = L.FenceFilter()
        text = "Vou criar:\n```python\nx = doc.addObject('Part::Box')\n```\nPronto."
        out = "".join(f.feed(text[i:i + 2]) for i in range(0, len(text), 2)) + f.flush()
        self.assertEqual(out, "Vou criar:\n\nPronto.")

    def test_code_from_text_takes_last_freecad_block(self):
        text = ("Tentei:\n```python\npeca.Base = 'Cilindro'\n```\nCorrigido:\n```python\n"
                "peca.Base = doc.getObject('Cilindro')\n```\nfim")
        code, cleaned = L.code_from_text(text)
        self.assertEqual(code, "peca.Base = doc.getObject('Cilindro')")
        self.assertNotIn("```", cleaned)
        self.assertEqual(L.code_from_text("só texto, sem código")[0], "")
        self.assertEqual(L.code_from_text("```python\nprint('oi')\n```")[0], "")   # não é FreeCAD


class Gate(unittest.TestCase):
    def test_new_warnings_only(self):
        # C1: o portão só cobra o que este turno criou
        multi = ("2 peças visíveis separadas (Caixa, Tampa); se o pedido era uma peça só, corte com "
                 "Part::Cut ou junte com Part::Fuse")
        base = L.warning_items("Velho: forma inválida; " + multi)
        self.assertEqual(len(base), 2)
        self.assertTrue(base[1].endswith("Part::Fuse"))
        self.assertEqual(L.new_warnings("Velho: forma inválida; " + multi, base), "")
        self.assertEqual(L.new_warnings("Velho: forma inválida; Novo: erro", base), "Novo: erro")
        self.assertEqual(L.new_warnings(multi, []), multi)
        self.assertEqual(L.new_warnings("nenhuma peça visível", ["nenhuma peça visível"]), "nenhuma peça visível")

    def test_explains_warning(self):
        self.assertTrue(L.explains_warning("A caixa e a tampa ficam separadas de propósito, para imprimir."))
        self.assertTrue(L.explains_warning("Não corrigi porque é intencional: são duas peças."))
        self.assertTrue(L.explains_warning("O erro no objeto Velho já existia antes deste pedido."))
        self.assertFalse(L.explains_warning("Corrigi."))
        self.assertFalse(L.explains_warning("Pronto, agora está certo."))
        self.assertFalse(L.explains_warning(""))


class SeeCode(unittest.TestCase):
    def test_asks_to_see_code(self):
        # D5: pedido para VER código não executa o bloco da resposta
        self.assertTrue(L.asks_to_see_code("mostre o código Python para apagar todos os objetos"))
        self.assertTrue(L.asks_to_see_code("me dá um script que faz uma caixa"))
        self.assertTrue(L.asks_to_see_code("ctx" + L.CONTEXT_MARK + "como seria o código dessa peça?"))
        self.assertFalse(L.asks_to_see_code("crie uma caixa 20x20x10"))
        self.assertFalse(L.asks_to_see_code("execute este código: doc.addObject('Part::Box')"))
        self.assertFalse(L.asks_to_see_code("mostre a peça de frente"))


class History(unittest.TestCase):
    def _conv(self, turns):
        msgs = [{"role": "system", "content": "S" * 300}]
        for t in range(turns):
            msgs.append({"role": "user", "content": "ctx" * 200 + L.CONTEXT_MARK + f"pedido {t}",
                         "images": ["A" * 10]})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{t}", "name": "run_python", "arguments": {"code": "x" * 900}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{t}", "name": "run_python", "content": "r" * 1500})
            msgs.append({"role": "assistant", "content": f"feito {t}"})
        return msgs

    def test_compact_keeps_pairs_and_current_turn(self):
        msgs = self._conv(6)
        out = L.compact(msgs, budget=2000)
        self.assertLessEqual(L.estimate_tokens(out), 2000)
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[-4:], msgs[-4:])                      # turno atual intacto
        called = {c["id"] for m in out for c in m.get("tool_calls") or []}
        answered = {m["tool_call_id"] for m in out if m["role"] == "tool"}
        self.assertEqual(called, answered)                          # nenhuma chamada órfã
        old_users = [m for m in out[:-4] if m["role"] == "user"]
        self.assertTrue(all("images" not in m and L.CONTEXT_MARK not in m["content"] for m in old_users))

    def test_thinking_counts_and_is_dropped_first(self):
        # C4: o raciocínio volta ao servidor; precisa contar na estimativa e sair primeiro
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "pedido"}]
        for i in range(4):
            msgs.append({"role": "assistant", "content": "", "thinking": "t" * 3000, "tool_calls": [
                {"id": f"c{i}", "name": "run_python", "arguments": {"code": "x"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "name": "run_python", "content": "OK"})
        self.assertGreater(L.estimate_tokens(msgs[2:3]), 1000)
        out = L.compact(msgs, budget=2000)
        self.assertLessEqual(L.estimate_tokens(out), 2000)
        thinks = [m for m in out if m.get("thinking")]
        self.assertEqual(thinks, [msgs[-2]])                       # só a última resposta mantém
        self.assertEqual([m["role"] for m in out], [m["role"] for m in msgs])   # nada mais saiu
        self.assertTrue(all(m.get("content") == "OK" for m in out if m["role"] == "tool"))
        self.assertIn("thinking", msgs[2])                          # não mexe na lista original

    def test_no_compaction_when_fits(self):
        msgs = self._conv(1)
        self.assertIs(L.compact(msgs, budget=10 ** 6), msgs)

    def test_truncate_keeps_tail(self):
        t = L.truncate("A" * 3000 + "Traceback final", 200)
        self.assertTrue(t.endswith("Traceback final"))
        self.assertLess(len(t), 260)


class Formats(unittest.TestCase):
    def test_round_trip_formats(self):
        msgs = [{"role": "system", "content": "s"},
                {"role": "user", "content": "oi", "images": ["QUJD"]},
                {"role": "assistant", "content": "", "thinking": "hm",
                 "tool_calls": [{"id": "c1", "name": "run_python", "arguments": {"code": "print('á')"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "run_python", "content": "OK"}]
        o = L.to_ollama(msgs)
        self.assertEqual(o[1]["images"], ["QUJD"])
        self.assertEqual(o[2]["tool_calls"][0]["function"]["arguments"], {"code": "print('á')"})
        self.assertEqual(o[3]["tool_name"], "run_python")
        a = L.to_openai(msgs)
        self.assertEqual(a[1]["content"][1]["type"], "image_url")
        self.assertEqual(json.loads(a[2]["tool_calls"][0]["function"]["arguments"]), {"code": "print('á')"})
        self.assertNotIn("thinking", a[2])
        b = L.openai_body("qwen3:8b", msgs[:2] + [{"role": "user", "content": "x"}], [], False, {})
        self.assertTrue(b["messages"][0]["content"].endswith("/no_think"))
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})

    def test_openai_no_think_survives_tool_steps(self):
        # C5: do passo 2 em diante a última mensagem é um resultado de ferramenta; o /no_think fica
        # no sistema (fixo) e o kwarg vai sempre
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "caixa"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "name": "run_python", "arguments": {"code": "x"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "run_python", "content": "OK"}]
        b = L.openai_body("qwen3:8b", msgs, [], False, {})
        self.assertEqual(b["messages"][0]["content"], "s /no_think")
        self.assertEqual(b["messages"][1]["content"], "caixa")
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(msgs[0]["content"], "s")                      # histórico não muda
        sem_sistema = L.openai_body("qwen3:8b", msgs[1:], [], False, {})
        self.assertTrue(sem_sistema["messages"][0]["content"].endswith("/no_think"))
        ligado = L.openai_body("qwen3:8b", msgs, [], True, {})
        self.assertNotIn("chat_template_kwargs", ligado)
        self.assertEqual(ligado["messages"][0]["content"], "s")
        self.assertNotIn("chat_template_kwargs", L.openai_body("llama3.1:8b", msgs, [], False, {}))
        # o agente decide pela opção Raciocínio (o catálogo OpenAI não tem "thinking")
        self.assertIs(L.openai_think("off", True), False)
        self.assertIs(L.openai_think("auto", False), False)
        self.assertIs(L.openai_think("auto", True), True)
        self.assertIs(L.openai_think("on", False), True)

    def test_model_info_and_rank(self):
        show_q34 = {"capabilities": ["completion", "tools", "thinking"],
                    "thinking": {"values": [True], "default": True},
                    "model_info": {"qwen3.context_length": 262144}, "details": {"parameter_size": "4.0B"}}
        show_q38 = {"capabilities": ["completion", "tools", "thinking"], "thinking": {"values": [False, True]},
                    "model_info": {"qwen3.context_length": 40960}, "details": {"parameter_size": "8.2B"}}
        show_g3 = {"capabilities": ["completion", "vision"], "details": {"parameter_size": "4.3B"}}
        show_q25 = {"capabilities": ["completion", "tools"], "details": {"parameter_size": "7.6B"}}
        infos = [L.model_info("qwen3:4b", show_q34), L.model_info("gemma3:4b", show_g3),
                 L.model_info("qwen2.5:7b", show_q25), L.model_info("qwen3:8b", show_q38)]
        self.assertEqual([i["name"] for i in L.rank_models(infos)], ["qwen3:8b", "qwen2.5:7b", "qwen3:4b"])
        self.assertIn("sempre pensa", L.model_label(infos[0]))
        self.assertTrue(infos[1]["vision"] and not infos[1]["tools"])

    def test_prefix_is_small(self):
        # CPU lê ~10 tokens/s: o prefixo fixo (prompt + ferramentas) precisa ficar pequeno
        size = len(L.SYSTEM_PROMPT_LOCAL) + len(json.dumps(L.tool_schemas(), ensure_ascii=False))
        self.assertLess(size, 4600)



class Promise(unittest.TestCase):
    def test_announces_action(self):
        # caso real do qwen3.5:9b: criou a caixa maciça, anunciou a abertura e encerrou a vez
        self.assertTrue(L.announces_action(
            "Ok! Caixa criada. Agora vou criar a abertura superior (topo) e a abertura traseira (frente em Y=40)."))
        self.assertTrue(L.announces_action("Vou criar a placa traseira agora:"))
        self.assertFalse(L.announces_action("Pronto! A placa 60 x 40 x 5 foi criada. Ajuste largura na planilha."))
        self.assertFalse(L.announces_action("Criei a peça. Se quiser, posso arredondar os cantos."))
        self.assertFalse(L.announces_action(""))
        self.assertIn("execute", L.NUDGE_DO_IT)


if __name__ == "__main__":
    unittest.main()
