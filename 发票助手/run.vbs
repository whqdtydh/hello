Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\AI\git\发票助手"
WshShell.Run "python -m app.ui.main_window", 0, False
