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

# ---------- 步骤 5：Moect 可选安装（微信实时采集） ----------
# 已装则跳过；未装询问。默认回车 = 不装（保守，纯手动导入模式照常可用）。
function Test-MoectInstalled {
    # 与 scripts/moect_live.py 的探测顺序保持一致（含 .env 的 MOECT_EXE）
    $envFile = Join-Path $ROOT ".env"
    if (Test-Path $envFile) {
        $line = (Get-Content $envFile -Encoding UTF8 |
            Where-Object { $_ -match '^\s*MOECT_EXE\s*=' } |
            Select-Object -First 1)
        if ($line) {
            $p = ($line -split '=', 2)[1].Trim().Trim('"')
            if ($p -and (Test-Path $p)) { return $p }
        }
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Moect\Moect.exe"),
        (Join-Path $env:ProgramFiles "Moect\Moect.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Moect\Moect.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

$moectPath = Test-MoectInstalled
if ($moectPath) {
    Write-Host "[5/6] 检测到已安装 Moect：$moectPath"
    Write-Host "      微信实时采集就绪（详见 README「Moect 集成」章节）。"
} else {
    Write-Host "[5/6] 可选：安装 Moect（微信数据库解密工具，用于实时采集）"
    Write-Host "      不装也能用：手动导入聊天记录文件（JSONL/CSV）功能完全一样。"
    $answer = Read-Host "      要用微信实时采集，现在自动安装 Moect 吗？[y/N]"
    if ($answer -match '^[Yy]') {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "      正在通过 winget 安装 Moect ..."
            & winget install --id Moect --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -eq 0) {
                $moectPath = Test-MoectInstalled
                if ($moectPath) {
                    Write-Host "      ✓ Moect 安装成功：$moectPath"
                } else {
                    Write-Host "      ✓ Moect 已安装（安装位置不在常见探测路径，稍后自检时确认）" -ForegroundColor Yellow
                }
            } else {
                Write-Host "      ✗ winget 安装失败（退出码 $LASTEXITCODE）。" -ForegroundColor Yellow
                Write-Host "        可稍后手动安装：winget install Moect" -ForegroundColor Yellow
                Write-Host "        或从 GitHub Releases 下载：https://github.com/ILoveScratch2/Moect/releases" -ForegroundColor Yellow
            }
        } else {
            Write-Host "      ✗ 本机没有 winget（Win10 早期版本可能没有）。" -ForegroundColor Yellow
            Write-Host "        手动安装方式见 README「Moect 集成」章节。" -ForegroundColor Yellow
        }
    } else {
        Write-Host "      已跳过。之后想用实时采集，随时可执行：winget install Moect"
    }
}
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
if ($moectPath) {
    Write-Host "  2. 微信实时采集（Moect 已装好）："
    Write-Host "     a. 登录微信桌面版，手动运行一次 Moect，确认它能"
    Write-Host "        解密并生成快照（这一步不通过，后面都白搭）"
    Write-Host "     b. 环境自检："
    Write-Host "        .venv\Scripts\python scripts\moect_live.py --check"
    Write-Host "     c. 详细步骤看 README「Moect 集成」章节。"
} else {
    Write-Host "  2. 数据来源（二选一）："
    Write-Host "     a. 手动导入聊天记录文件（JSONL/CSV/TXT）——"
    Write-Host "        把文件丢进 imports\ 目录，网页里点「扫描导入」"
    Write-Host "     b. 微信实时采集：之后执行 winget install Moect，"
    Write-Host "        再看 README「Moect 集成」章节"
}
Write-Host ""
Write-Host "  3. 遇到问题请看 README 的「排错 FAQ」章节。"
Write-Host "============================================"
Write-Host ""
Wait-Key
exit 0
