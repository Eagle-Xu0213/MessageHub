# ============================================================
#  MessageHub 一键安装向导（由 setup.bat 调起）
#  流程：探测 Python 3.11+ -> 创建 .venv -> 安装依赖 -> 生成配置 -> 自检
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
Write-Host "[1/5] 找到 Python：$py"
Write-Host ""

# ---------- 步骤 2：创建虚拟环境 ----------
$venvPy = Join-Path $ROOT ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host "[2/5] .venv 已存在，跳过创建。"
} else {
    Write-Host "[2/5] 创建虚拟环境 .venv ..."
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
Write-Host "[3/5] 升级 pip ..."
& $venvPy -m pip install --upgrade pip 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      （pip 升级失败，继续尝试安装依赖）" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "[3/5] 安装依赖（首次需联网下载，约 1-2 分钟）..."
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
Write-Host "[4/5] 生成配置文件（已有的不会被覆盖）..."
if (-not (Test-Path (Join-Path $ROOT ".env"))) {
    Copy-Item (Join-Path $ROOT ".env.example") (Join-Path $ROOT ".env")
}
$personalJson = Join-Path $ROOT "config\personal.json"
$newCfg = -not (Test-Path $personalJson)
if ($newCfg) {
    Copy-Item (Join-Path $ROOT "config\personal.example.json") $personalJson
}
if ($newCfg) {
    Write-Host ""
    Info @(
" 现在会打开记事本，请填写 config\personal.json 里两项关键配置：",
"   - identity ：你的微信昵称 / 真名（nicknames、real_name、sender_names）",
"   - groups   ：你的工作群名（程序靠它识别""谁在给你派活""）",
" 填完保存并关闭记事本，安装会自动继续。",
" 暂时不想填也可以直接关闭，之后随时用记事本编辑该文件。",
" 各字段含义见 README「配置」章节。"
    )
    Write-Host ""
    Start-Process notepad.exe -ArgumentList "`"$personalJson`"" -Wait
} else {
    Write-Host "  配置文件已存在，跳过编辑。仍想修改可随时用记事本打开 config\personal.json。"
}
Write-Host ""

# ---------- 步骤 5：自检 ----------
Write-Host "[5/5] 自检（验证核心模块能正常导入）..."
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
Write-Host "  2.（可选）要用微信实时采集："
Write-Host "     a. 安装 Moect —— 打开 cmd 执行：winget install Moect"
Write-Host "     b. 登录微信桌面版，手动运行一次 Moect，确认它能"
Write-Host "        解密并生成快照（这一步不通过，后面都白搭）"
Write-Host "     c. 环境自检："
Write-Host "        .venv\Scripts\python scripts\moect_live.py --check"
Write-Host "     d. 详细步骤看 README「Moect 集成」章节。"
Write-Host ""
Write-Host "  3. 遇到问题请看 README 的「排错 FAQ」章节。"
Write-Host "============================================"
Write-Host ""
Wait-Key
exit 0
