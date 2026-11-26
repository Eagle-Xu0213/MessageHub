Option Explicit
Dim shell, fso, scriptPath, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptPath = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "watchdog_silent.vbs")
command = """" & fso.BuildPath(fso.GetParentFolderName(WScript.FullName), "wscript.exe") & """ //B """ & scriptPath & """"
On Error Resume Next
shell.Run command, 0, False
If Err.Number <> 0 Then WScript.Quit 1
