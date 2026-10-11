# Chat CAD: painel de agente na lateral do FreeCAD. Abre/fecha com Ctrl+Shift+Espaço.


def _chatcad_start():
    import glob
    import os
    import sys

    import FreeCAD

    # o FreeCAD já põe a pasta do addon no sys.path; isto é só um reforço, sem depender
    # do nome da pasta (ChatCAD, freecad-chatcad, instalado pelo Addon Manager...)
    roots = [os.path.join(FreeCAD.getUserAppDataDir(), "Mod"),
             os.path.join(FreeCAD.getHomePath(), "Mod")]
    for root in roots:
        for hit in glob.glob(os.path.join(root, "*", "chatcad_panel.py")):
            folder = os.path.dirname(hit)
            if folder not in sys.path:
                sys.path.append(folder)
    try:
        import chatcad_panel
        chatcad_panel.install()
    except Exception as e:  # nunca derrubar o FreeCAD por causa do painel
        FreeCAD.Console.PrintError(f"Chat CAD: falha ao abrir o painel: {e}\n")


from PySide import QtCore  # noqa: E402

QtCore.QTimer.singleShot(1500, _chatcad_start)
