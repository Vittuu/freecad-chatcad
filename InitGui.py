# Chat CAD: painel de agente na lateral do FreeCAD. Abre/fecha com Ctrl+Shift+Espaço.
# O FreeCAD executa este arquivo com exec() e variáveis locais: nada aqui pode depender de nomes
# globais deste arquivo dentro de funções (por isso a nova tentativa fica em chatcad_panel.autostart).


def _chatcad_start():
    import glob
    import os
    import sys
    import traceback

    import FreeCAD

    # o FreeCAD já põe a pasta do addon no sys.path; isto é só um reforço, sem depender
    # do nome da pasta (ChatCAD, freecad-chatcad, instalado pelo Addon Manager...)
    for root in (os.path.join(FreeCAD.getUserAppDataDir(), "Mod"), os.path.join(FreeCAD.getHomePath(), "Mod")):
        for hit in glob.glob(os.path.join(root, "*", "chatcad_panel.py")):
            folder = os.path.dirname(hit)
            if folder not in sys.path:
                sys.path.append(folder)
    try:
        import chatcad_panel
        chatcad_panel.autostart()
    except Exception as e:  # nunca derrubar o FreeCAD por causa do painel
        log = os.path.join(FreeCAD.getUserAppDataDir(), "chatcad_startup.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass
        FreeCAD.Console.PrintError(f"Chat CAD: falha ao abrir o painel: {e} (detalhes em {log})\n")


from PySide import QtCore  # noqa: E402

QtCore.QTimer.singleShot(1500, _chatcad_start)
