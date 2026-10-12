# Chat CAD for FreeCAD

An AI agent side panel for [FreeCAD](https://www.freecad.org/). Describe a part, point at a face, or scribble on the 3D view, and an agent models it for you as a **parametric** FreeCAD document you can keep tweaking by hand.

Pick the agent you like: **Claude Code**, **OpenAI Codex**, **Google Antigravity**, or a **local model** through [Ollama](https://ollama.com) (or any OpenAI-compatible server such as LM Studio), on your own computer or on another PC of yours with a GPU.

> **Status: alpha.** Tested on Windows 11 with FreeCAD 1.1.4. Linux and macOS should work but are untested. The interface is in Brazilian Portuguese (translations welcome).

> [!WARNING]
> **Local AI is not there yet.** The local provider works and is fully integrated, but in our tests local models (qwen3:8b, qwen3.5:9b, qwen3.8:27b, on a laptop CPU and on a desktop GPU) only handled simple parts; **every real multi-feature part failed**, even when asked one feature at a time. For real work, use Codex, Claude Code or Antigravity. See [Tests](#tests) for the numbers and photos.

<p align="center">
  <img src="docs/screen-panel.png" width="96%" alt="Chat CAD docked in FreeCAD">
</p>

## Features

- **Agent panel docked inside FreeCAD** with a minimal dark/light UI that follows FreeCAD's theme: starting-point cards, quick actions, collapsible step cards that show what the agent ran (input and output), formatted answers, and a footer with time and tokens/s. Toggle it with `Ctrl+Shift+Space`.
- **Four providers:** Claude Code, Codex and Antigravity use **your existing CLI login/subscription** (no API keys stored by the addon). The **local** provider talks to Ollama or an OpenAI-compatible server directly from FreeCAD: nothing leaves your machine (or your private network).
- **Point and ask:** whatever you select in the 3D view (faces, edges, picked points) goes along with your message ("make a 5 mm hole *here*"). One click ignores the selection.
- **Draw what you mean:** capture the 3D view and scribble on it (pen, line, arrow, text, colors, undo/redo), start from a blank grid sheet, or paste/drag any image.
- **Parametric by default:** dimensions live in a `Parametros` spreadsheet with aliases; change a number and press `Ctrl+R`.
- **Safety nets:** a backup copy (`<name>.antes-<action>.FCStd`) before changes, each local-agent step is one undo entry (`Ctrl+Z`), failed steps are rolled back automatically, and an automatic check (valid single solid, no broken objects) runs before the agent may say "done".
- **Agent / Plan modes:** *Agent* edits the model; *Plan* only discusses and proposes.

<p align="center">
  <img src="docs/panel-start.png" width="31%" alt="Start screen">
  <img src="docs/conversation.png" width="31%" alt="A conversation with collapsible steps">
  <img src="docs/model-menu.png" width="31%" alt="Provider and model menu">
</p>

<p align="center"><img src="docs/sketch.png" width="70%" alt="Drawing on the 3D view before asking for a change"></p>

## How it works

```
                       ┌─ CLI providers ──> claude -p / codex exec / agy -p ──> freecad-mcp (uvx) ──┐
FreeCAD ── Chat CAD ───┤                                                                              ├─> FreeCAD
  panel                └─ Local provider ──> Ollama / OpenAI-compatible HTTP ──> tools run in-process ┘
```

- **CLI providers** run the agent CLI headless, stream its JSON events into the chat, and give it exactly one MCP server: [freecad-mcp](https://github.com/neka-nat/freecad-mcp), which drives the running FreeCAD.
- **Local provider** runs its own agent loop inside FreeCAD (no CLI, no MCP): a small, fixed tool set (`run_python`, `get_document`, `set_parameter`, `view_screenshot`), short Portuguese prompts tuned for 4–30B models, helpers such as `param()`, `liga()`, `arestas()` and `arredonda()`, error hints in Portuguese, and guards against the classic small-model failures (tool calls written as text, code shown instead of run, promising an action and stopping, answers cut by the length limit, the same failing call repeated).

## Requirements

1. **FreeCAD 1.0+** (tested with 1.1.4).
2. **For the CLI providers:** [uv](https://docs.astral.sh/uv/) (to run `freecad-mcp` with `uvx`) and **the freecad-mcp FreeCAD addon**: copy `addon/FreeCADMCP` from [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) into your FreeCAD `Mod` folder, restart FreeCAD, switch to the **MCP Addon** workbench and enable **FreeCAD MCP → Auto-Start Server**. Then at least one CLI, logged in:
   - [Claude Code](https://docs.claude.com/en/docs/claude-code): run `claude` once and `/login`.
   - [Codex CLI](https://github.com/openai/codex): `codex login`. The panel passes its MCP config on the command line.
   - [Antigravity CLI](https://antigravity.google/docs/cli) (`agy`): log in once, then register the server and allow its tools (headless mode can't ask):
     ```bash
     agy mcp add freecad -- uvx --python ">=3.12" freecad-mcp
     ```
     and in `~/.gemini/antigravity-cli/settings.json`: `{ "permissions": { "allow": ["mcp(freecad/*)"] } }`
3. **For the local provider:** [Ollama](https://ollama.com) with at least one model that supports tools, e.g. `ollama pull qwen3.5:9b` (tools + vision). No MCP addon or uv needed. See [Local models](#local-models).

The panel shows a banner when the selected provider is missing, logged out or unreachable (with a **Start Ollama** button for the local one).

## Installation

**Option A: git clone into the Mod folder**

```bash
git clone https://github.com/Vittuu/freecad-chatcad.git
```

Clone it into your FreeCAD user `Mod` folder, for example `%APPDATA%\FreeCAD\v1-1\Mod\` on Windows or `~/.local/share/FreeCAD/v1-1/Mod/` on Linux, then restart FreeCAD. The panel opens on the left.

**Option B: Addon Manager custom repository**

*Edit → Preferences → Addon Manager → Custom repositories*: add `https://github.com/Vittuu/freecad-chatcad` with branch `main`, then install **Chat CAD**.

## Local models

> **Not ready for real parts yet.** Simple parts (a plate with holes, a box) and changing existing dimensions work. Real multi-feature parts failed with every local model we tried, on CPU and on GPU, even one feature at a time ([results](#3-local-models-one-feature-per-message)). The integration is in place so it gets better as local models improve; today, use a cloud agent for real work.

Open the gear (⚙) in the panel header to pick the server (Ollama or OpenAI-compatible), its address, a vision model for describing attached images when the main model can't see, the thinking mode, context size, step limit, and memory options (**free the model's RAM after each answer**).

**Use a GPU if you can.** On a laptop CPU, models run at 1–4 tokens/s: simple parts work, complex ones don't (see the tests below). If you have another PC with a GPU, run Ollama there and point Chat CAD to it. A safe way over [Tailscale](https://tailscale.com), without opening firewall ports or exposing Ollama to your LAN:

```bash
# on the GPU machine (Ollama keeps listening only on 127.0.0.1)
tailscale serve --bg --tcp=11434 tcp://127.0.0.1:11434
```

Then, in Chat CAD's settings, set the address to `http://<the GPU machine's Tailscale IP>:11434`. Use the **IP**, not the MagicDNS name: when Ollama listens on localhost it rejects other host names with `403` (DNS-rebinding protection) but accepts the machine's own IP addresses.

## Tests

### 1. The same simple part on every agent

*"Crie uma placa de 60 x 40 x 5 mm com 4 furos de 5 mm nos cantos, a 6 mm das bordas."* Each result was checked numerically: single valid solid, exact volume (11 607.3 mm³), hole centers, parameters.

| Agent | Model | Runs on | Time | Result |
|---|---|---|---|---|
| Codex | account default | cloud | 93 s | ✅ correct |
| Antigravity | Gemini 3.8 Flash (High) | cloud | 208 s | ✅ correct (first try hit a network error) |
| **Local** | **qwen3:8b** | **laptop CPU (i7-1265U)** | **221 s** | ✅ **correct, first try** |
| Claude Code | Opus | cloud | 279 s | ✅ correct (PartDesign, self-corrected placement) |
| Local | qwen2.5:7b | laptop CPU | 323 s | ❌ holes in the wrong place |
| Local | qwen2.5:7b | desktop GPU (RX 7600) | **35 s** | ❌ broken solid (fast, but same model quality) |

### 2. A real part from a text description

The wall-mount remote holder below was first modeled interactively with Chat CAD. Its features were then written down as one text description (basket, back plate with rounded top corners, countersunk screw holes, U-shaped front cut-out with rounded corners, curved side gussets, honeycomb vent, engraved text) and given to each agent in a new, empty document.

<p align="center"><img src="docs/test-holder-iso.png" width="96%" alt="Same description, different agents (isometric)"></p>

Every result was checked numerically against the original (34 589 mm³, 60 × 40 × 95 mm): single valid solid, volume, size, screw holes, countersinks, text, parameters.

| Result | Isometric | Front | Time | Volume vs original | Verdict |
|---|---|---|---|---|---|
| **Original** (modeled interactively with Chat CAD) | <img src="docs/results/original-iso.png" width="160"> | <img src="docs/results/original-front.png" width="160"> | — | — | reference, 22 parameters |
| **Codex** (account default model) | <img src="docs/results/codex-iso.png" width="160"> | <img src="docs/results/codex-front.png" width="160"> | 6 min 21 s | +0.04 % | ✅ practically identical; 32 parameters, everything driven by the spreadsheet. A second run (the screenshot at the top) took 8 min 18 s, −0.1 % |
| **Claude Code** (Opus) | <img src="docs/results/claude-code-iso.png" width="160"> | <img src="docs/results/claude-code-front.png" width="160"> | 12 min 27 s | −0.01 % | ✅ identical shape; the U cut-out, gussets, honeycomb and text don't update from the spreadsheet (script-generated) |
| **Antigravity** (Gemini 3.8 Flash High) | <img src="docs/results/antigravity-iso.png" width="160"> | <img src="docs/results/antigravity-front.png" width="160"> | 9 min 28 s | −18.4 % | ⚠️ looks right, but the basket's cavity cuts 2 mm into the back plate, leaving it **1 mm** thick behind the basket |
| **Local qwen3:8b**, one message (laptop CPU) | <img src="docs/results/local-qwen3-8b-cpu-iso.png" width="160"> | <img src="docs/results/local-qwen3-8b-cpu-front.png" width="160"> | 25 min 41 s | −66.8 % | ❌ only a slab; first attempt produced nothing (answer cut by the length limit) |
| **Local qwen3.5:9b**, 8 messages (desktop GPU) | <img src="docs/results/local-qwen3.5-9b-gpu-iso.png" width="160"> | <img src="docs/results/local-qwen3.5-9b-gpu-front.png" width="160"> | ~7 min | — | ❌ failed at step 3 of 8, broken geometry |
| **Local qwen3.8:27b**, 8 messages (desktop GPU + CPU) | <img src="docs/results/local-qwen3.8-27b-gpu-iso.png" width="160"> | <img src="docs/results/local-qwen3.8-27b-gpu-front.png" width="160"> | ~24 min for 2 steps | — | ⚠️ steps 1–2 clean, but the front was left open; stopped (too slow) |

### 3. Local models, one feature per message

Same part, split into 8 short messages in one conversation (basket, back plate, rounded corners, screw holes, U cut-out, gussets, honeycomb, text). The run stops at the first failed step.

| Model | Runs on | Speed | Result |
|---|---|---|---|
| qwen3:8b | laptop CPU | ~3 tok/s | ❌ step 2 (wrote text into `App.Vector`) |
| qwen3.5:9b | laptop CPU | 3.6 tok/s, ~15 min/step | ❌ step 2 |
| qwen3.5:9b | desktop GPU (RX 7600, all layers on GPU) | ~21–40 tok/s, ~1 min/step | ❌ step 3 (basket cavity barely cut; edge selection) |
| qwen3.8:27b | desktop GPU + CPU (28 % on GPU) | ~1 tok/s, ~9–15 min/step | ⚠️ steps 1–2 clean and valid, but it opened the front instead of the back; stopped after step 2 (too slow: all 8 steps would take well over an hour) |

<p align="center"><img src="docs/test-local-steps.png" width="96%" alt="qwen3.8:27b building the holder step by step"></p>

**Takeaways (honest):**
- For real parts, today's cloud agents are far ahead. Codex was the fastest and most faithful in these tests.
- Local models handle simple parts and parameter tweaks; complex multi-feature parts still fail, even step by step. A GPU fixes **speed** (10–16× here), not **reasoning**.
- The automatic checks matter: they caught every broken local result, and numeric verification exposed a defect (Antigravity) that looked fine in a screenshot.
- Each local failure above became a fix in the agent (hints for the exact errors, `arestas()`/`arredonda()` helpers, "promised but didn't run" and "answer cut by length" recovery).

### Automated test suites

| Suite | What it covers | Result |
|---|---|---|
| `tests/test_llm.py` | prompt, stream parsers (Ollama NDJSON, OpenAI SSE), argument repair, tool calls leaked as text, history compaction, thinking modes | 31 / 31 |
| `tests/qt_test_agent.py` | local agent loop against a fake Ollama server: steps, stop, gates, nudges, vision bridge, prewarm | 61 / 61 |
| `tests/qt_test_backend.py` | CLI providers with fake executables: event parsing, errors, auth checks | 36 / 36 |
| `tests/qt_test_ui.py` | the panel offscreen, dark and light themes: every provider, conversation rendering, attachments, stop/queue, menus | 143 / 143 |
| `tests/fc_test_tools.py` | FreeCAD tools in `freecadcmd`: run/rollback/undo, timeouts, parameters, backups, verification, helpers | 78 / 78 |

Run them with FreeCAD's Python (`QT_QPA_PLATFORM=offscreen`, and a temporary `FREECAD_USER_HOME`); see each file's header. The pure tests also run with any Python: `python -m unittest tests.test_llm`.

## Security

The agents run **Python code inside your FreeCAD session** with your user's permissions.

- **Claude Code:** only the `freecad` MCP server is loaded (`--strict-mcp-config`); shell/edit/web tools are disallowed.
- **Codex:** runs with the `read-only` sandbox; only the `freecad` tools are pre-approved.
- **Antigravity:** only `mcp(freecad/*)` is allowed.
- **Local:** tools run in-process; each step is a FreeCAD transaction (rolled back on error), infinite loops are stopped, and nothing is sent anywhere except the model server you configure.

Keep backups of important files; the addon makes one before every change.

## Troubleshooting

- **The panel doesn't appear at startup.** Check `<FreeCAD user data>/chatcad_startup.log`. Note that with a `package.xml`, FreeCAD 1.x only runs `InitGui.py` for content declared as `<workbench>` (this addon does that, without creating a workbench).
- **A CLI agent "writes" tool calls as text, or says the FreeCAD tools are unavailable.** The MCP server didn't start. FreeCAD puts its own Python (3.11) first on `PATH`, while `freecad-mcp` needs Python ≥ 3.12; the panel strips FreeCAD from `PATH` and calls `uvx --python ">=3.12"`. Check `uvx freecad-mcp --help` in a terminal.
- **The very first CLI request says the FreeCAD tools aren't available, the next one works.** On first use, `uvx` downloads `freecad-mcp` and a Python ≥ 3.12, which can exceed the agent's MCP start-up timeout. Run `uvx --python ">=3.12" freecad-mcp --help` once in a terminal to warm the cache.
- **Remote Ollama answers `403`.** Use the server's IP address instead of a host name (see [Local models](#local-models)).
- **Remote Ollama times out although it's running.** Windows may have created *Block* firewall rules for `ollama.exe` (when its network prompt was dismissed). Prefer `tailscale serve` as above, which needs no firewall rule.
- **FreeCAD started from a packaged app sees a different profile.** Processes launched from MSIX-packaged apps (for example a terminal inside some desktop apps) may get a virtualized `%APPDATA%`, so addons installed there don't show up in the FreeCAD you open from the Start menu. Install into the real `%APPDATA%\FreeCAD\v1-1\Mod`.
- **Logs:** last CLI turn in `<FreeCAD user data>/chatcad_last.log`; attachments in `<FreeCAD user data>/chatcad_anexos/`.

## Credits

- [freecad-mcp](https://github.com/neka-nat/freecad-mcp) by neka-nat: the FreeCAD MCP server the CLI providers use.
- UI inspired by the agent sidebar of [OpenChatCut](https://github.com/0xsline/OpenChatCut).

## License

[LGPL-2.1](LICENSE), the same license as FreeCAD.

---

## 🇧🇷 Em português

**Chat CAD** é um painel de agente de IA dentro do FreeCAD. Você descreve a peça, clica numa face ou rabisca na vista 3D, e o agente modela para você, sempre de forma **paramétrica** (as medidas ficam na planilha `Parametros`; ajuste e aperte `Ctrl+R`).

**Agentes:** Claude Code, Codex e Antigravity (usam o login que você já tem) ou **IA local** pelo Ollama, no seu computador ou em outro PC seu com placa de vídeo (via Tailscale, veja *Local models*).

**Para usar:**
1. Instale o FreeCAD 1.0+ e clone este repositório na pasta `Mod` do FreeCAD; reinicie. O painel abre à esquerda (`Ctrl+Shift+Espaço` mostra e esconde).
2. **IA na nuvem:** instale o [uv](https://docs.astral.sh/uv/), o complemento do [freecad-mcp](https://github.com/neka-nat/freecad-mcp) (ligue *Auto-Start Server*) e faça login em `claude`, `codex` ou `agy` (o Antigravity precisa dos passos extras de *Requirements*).
3. **IA local:** instale o [Ollama](https://ollama.com) e um modelo com ferramentas (ex.: `ollama pull qwen3.5:9b`). Na engrenagem do painel você escolhe servidor, modelo de visão, raciocínio e se o modelo sai da memória depois de cada resposta.

> **IA local ainda não rola para peças de verdade.** Ela está integrada e funciona, mas nos nossos testes os modelos locais (qwen3:8b, qwen3.5:9b e qwen3.8:27b, no processador do notebook e na placa de vídeo do desktop) só acertaram peças simples. **Todas as peças reais falharam**, mesmo pedindo uma parte de cada vez. Para trabalho de verdade, use Codex, Claude Code ou Antigravity.

**O que os testes mostraram** (fotos de cada resultado em *Tests*): o Codex foi o mais rápido e fiel (suporte idêntico ao original em 6 min); o Claude Code também acertou a forma; o Antigravity pareceu certo, mas a conferência numérica achou a placa traseira com só 1 mm de espessura atrás do cesto. Placa de vídeo resolve a velocidade da IA local (10 a 16 vezes mais rápida), não o raciocínio.

Testado no Windows 11 com o FreeCAD 1.1.4. Versão alfa: relatos de bugs e contribuições são bem-vindos!
