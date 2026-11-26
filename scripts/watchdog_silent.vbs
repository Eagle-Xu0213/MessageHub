Option Explicit
Dim shell, fso, here, project, python, scriptPath, command, result
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
project = fso.GetParentFolderName(here)
python = fso.BuildPath(project, ".venv\Scripts\pythonw.exe")
scriptPath = fso.BuildPath(here, "watchdog.py")
If Not fso.FileExists(python) Then WScript.Quit 1
command = """" & python & """ """ & scriptPath & """"
On Error Resume Next
result = shell.Run(command, 0, True)
If Err.Number <> 0 Then WScript.Quit 1
WScript.Quit result
