' MessageHub 静默启动器（启动文件夹 / 注册表 Run 键）
' 幂等：uvicorn 端口占用时退出 + moect_live 单实例锁（8765）自动去重
'
' 路径全部相对本文件推导，换机器无需修改：
'   本文件位于 <项目根>/scripts/autostart_silent.vbs
'   → 项目根 = 本文件所在目录的上一级

Option Explicit
Dim shell, fso, here, project, python, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
project = fso.GetParentFolderName(here)

' 优先用项目虚拟环境，其次 PATH 里的 python
python = project & "\.venv\Scripts\python.exe"
If Not fso.FileExists(python) Then python = "python.exe"

shell.CurrentDirectory = project & "\src"

' 1) 实时采集器（完全隐藏；已有实例时 8765 锁冲突立即退出）
'    窗口样式 0：不留任务栏/托盘痕迹。有 5 分钟级计划任务看门狗兜底，
'    进程意外退出后自动复活，无需人工干预。
cmd = """" & python & """ """ & project & "\scripts\moect_live.py"" --loop --interval 120 --no-refresh --selected-groups --include-private"
shell.Run cmd, 0, False

' 2) API 服务（完全隐藏；8000 已被占用时 uvicorn 端口冲突自动退出）
cmd = """" & python & """ -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000 --log-level warning"
shell.Run cmd, 0, False
