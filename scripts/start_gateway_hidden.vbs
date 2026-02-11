Option Explicit

Dim shell, fso, scriptDir, batPath, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = scriptDir & "\start_gateway.bat"
cmd = "cmd /c """ & batPath & """ --hidden-run"

shell.Run cmd, 0, False
