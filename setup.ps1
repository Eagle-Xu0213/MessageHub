# ============================================================
#  MessageHub 一键安装向导（由 setup.bat 调起）
#  流程：探测 Python 3.11+ -> 创建 .venv -> 安装依赖 -> 生成配置
#        -> Moect 可选安装（询问） -> 自检
#  零基础用户双击 setup.bat 即可；每一步失败都有明确提示。
#  重复运行是安全的：已完成的步骤自动跳过，配置不会被覆盖。
# ============================================================

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

function Wait-Key {
    # 与传统 pause 体验一致：按任意键继续（含回车）
    cmd /c pause | Out-Null
}

function Fail {
    param([string[]]$Lines)
    Write-Host ""
    foreach ($l in $Lines) { Write-Host $l -ForegroundColor Red }
    Write-Host ""
    Wait-Key
    exit 1
}

function Info {
    param([string[]]$Lines)
    foreach ($l in $Lines) { Write-Host $l }
}

Write-Host "============================================"
Write-Host "  MessageHub 安装向导"
Write-Host "============================================"
Write-Host ""

# ---------- 步骤 1：探测 Python 3.11+ ----------
function Find-RealPython {
    # 返回第一个版本 >= 3.11 的真实 python.exe；排除 Microsoft Store 假别名
    $candidates = @()
    $pc = Get-Command python -ErrorAction SilentlyContinue
    if ($pc -and $pc.Source -and $pc.Source -notlike '*WindowsApps*') {
        $candidates += $pc.Source
    }
    try {
        $out = & py -3 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates += [string]($out | Select-Object -Last 1) }
    } catch { }

    foreach ($c in $candidates) {
        try {
            $v = & $c -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
            $v = [string]($v | Select-Object -Last 1)
            if ($v -match '^\d{3,}$' -and [int]$v -ge 311) { return $c }
        } catch { }
    }
    return $null
}

$py = Find-RealPython
if (-not $py) {
    Fail @(
"[错误] 没有找到 Python 3.11 或更高版本。",
"",
" 请任选一种方式安装，装完后关闭本窗口，重新双击 setup.bat：",
"",
"   方式一（推荐，Win10/11 自带）：",
"     按 Win+R 输入 cmd 回车，在窗口里执行：",
"       winget install Python.Python.3.12",
"",
"   方式二：浏览器打开 https://www.python.org/downloads/",
"     下载 Windows installer (64-bit)，安装时【务必勾选",
"     Add python.exe to PATH】再点 Install Now。",
"",
" 装好后请打开一个【新的】cmd 窗口验证：python --version",
" 能显示 3.11 以上版本号即可。"
    )
}
Write-Host "[1/6] 找到 Python：$py"
Write-Host ""

# ---------- 步骤 2：创建虚拟环境 ----------
$venvPy = Join-Path $ROOT ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host "[2/6] .venv 已存在，跳过创建。"
} else {
    Write-Host "[2/6] 创建虚拟环境 .venv ..."
    & $py -m venv (Join-Path $ROOT ".venv")
    if ($LASTEXITCODE -ne 0) {
        Fail @(
"[错误] 创建虚拟环境失败。",
"   可尝试：删除项目里的 .venv 文件夹后重新双击 setup.bat。"
        )
    }
}
Write-Host ""

# ---------- 步骤 3：安装依赖 ----------
Write-Host "[3/6] 升级 pip ..."
& $venvPy -m pip install --upgrade pip 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      （pip 升级失败，继续尝试安装依赖）" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "[3/6] 安装依赖（首次需联网下载，约 1-2 分钟）..."
& $venvPy -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    Fail @(
"",
"[错误] 依赖安装失败。常见原因：",
"   1. 网络不通或被代理拦截 —— 检查网络后重新双击 setup.bat",
"   2. 国内网络较慢 —— 打开 cmd，进入项目目录后手动执行：",
"        .venv\Scripts\pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple",
"      装完再重新双击 setup.bat 继续后面的步骤。"
    )
}
Write-Host ""

# ---------- 步骤 4：生成配置文件（已有的不覆盖） ----------
Write-Host "[4/6] 生成配置文件（已有的不会被覆盖）..."
if (-not (Test-Path (Join-Path $ROOT ".env"))) {
    Copy-Item (Join-Path $ROOT ".env.example") (Join-Path $ROOT ".env")
}
Write-Host "  首次打开工作台后，可在网页中填写微信名字并选择关注的会话。"
Write-Host ""

# ---------- 步骤 5：微信自动采集由独立脚本配置 ----------
Write-Host "[5/6] 微信自动采集使用独立的 Moect 源码，无需安装图形程序。"
Write-Host "      安装完成后请按 README 运行 scripts\install_scheduled_tasks.ps1。"
Write-Host "      如只使用手动导入，可以跳过。"
Write-Host ""

# ---------- 步骤 6：自检 ----------
Write-Host "[6/6] 自检（验证核心模块能正常导入）..."
$env:PYTHONIOENCODING = "utf-8"
& $venvPy -c "import messagehub.app; print('核心模块导入成功')"
if ($LASTEXITCODE -ne 0) {
    Fail @(
"",
"[错误] 自检失败。",
"   若提示 .venv 相关错误：删除项目里的 .venv 文件夹后重新双击 setup.bat。",
"   其他报错请把窗口里的红色错误信息完整截图，到 GitHub Issues 反馈。"
    )
}

Write-Host ""
Write-Host "============================================"
Write-Host "  安装完成！下一步："
Write-Host ""
Write-Host "  1. 双击「启动 MessageHub.bat」，浏览器会自动打开"
Write-Host "     http://127.0.0.1:8000/"
Write-Host ""
Write-Host "  2. 微信自动采集：登录微信后，执行："
Write-Host '     powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"'
Write-Host "     手动导入可跳过此步。详见 README。"
Write-Host ""
Write-Host "  3. 遇到问题请看 README 的「排错 FAQ」章节。"
Write-Host "============================================"
Write-Host ""
Wait-Key
exit 0
