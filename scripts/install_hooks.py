# -*- coding: utf-8 -*-
"""安装 git pre-commit 钩子：拦截敏感凭据与真实聊天数据。

拦截内容：
1. `.env`、`runtime/`、`imports/`、密钥文件、本地个人配置
2. 含聊天正文的数据产物（HTML/JSONL/CSV）
3. 文本文件里的真实微信 ID、API Key 形态、硬编码 Windows 用户路径
4. Python 文件语法错误（py_compile）

模板与测试夹具（`*.example`、`tests/fixtures/*`）豁免。

重装：python scripts/install_hooks.py （幂等，直接覆盖）
"""
import os
import sys

HOOK = r"""#!/bin/sh
# MessageHub pre-commit hook - installed by scripts/install_hooks.py
# 目标：拦住「敏感凭据」与「真实聊天数据」，而不是拦住正常开发。
# 绕过方式：git commit --no-verify（风险自担）

fail=0
for f in $(git diff --cached --name-only --diff-filter=ACMR); do
  # 1) 绝对禁止入库的文件
  case "$f" in
    .env|.env.local|.env.*.local)
      echo "[pre-commit] 禁止提交 .env（含 API key / SMTP 授权码）: $f"; fail=1;;
    runtime/*|imports/*)
      echo "[pre-commit] 禁止提交运行数据: $f"; fail=1;;
    *keys.json|*.key|*.pem)
      echo "[pre-commit] 疑似密钥文件: $f"; fail=1;;
    src/1/*|nul|_archive/*|.workbuddy/*)
      echo "[pre-commit] 垃圾/残留/本地归档文件: $f"; fail=1;;
    config/personal.json)
      echo "[pre-commit] 禁止提交本地个人配置（含真名与群名）: $f"; fail=1;;
  esac

  # 2) 模板文件豁免：配置模板、测试夹具、应用自身的 Jinja 模板必须能入库
  case "$f" in
    *.example|*.example.json|tests/fixtures/*|src/messagehub/templates/*)
      continue;;
  esac

  # 3) 数据产物：HTML/JSONL/CSV 可能含聊天正文
  case "$f" in
    *.html|*.htm|*.jsonl|*.csv)
      echo "[pre-commit] 数据产物不得入库（可能含真实聊天正文）: $f"
      echo "            模板类文件请改名为 .example，或用 --no-verify 并注明原因"
      fail=1;;
  esac

  # 4) 文本文件做敏感特征扫描
  case "$f" in
    *.md|*.json|*.py|*.html|*.bat|*.vbs|*.toml|*.yml)
      diff_content=$(git diff --cached -- "$f")
      # 真实微信账号 ID
      if echo "$diff_content" | grep -qE "wxid_[0-9a-z]{14,18}\b"; then
        echo "[pre-commit] 含真实微信账号 ID，疑似聊天数据: $f"; fail=1
      fi
      # 真实 API Key 形态
      if echo "$diff_content" | grep -qE "^\+.*(sk-[A-Za-z0-9]{20,}|ak-f[A-Za-z0-9]{20,})"; then
        echo "[pre-commit] 疑似真实 API Key: $f"; fail=1
      fi
      # 硬编码的 Windows 用户目录
      if echo "$diff_content" | grep -qE "^\+.*C:/Users/[A-Za-z]"; then
        echo "[pre-commit] 硬编码 Windows 用户路径（换机器会失效）: $f"; fail=1
      fi;;
  esac

  # 5) Python 语法检查
  case "$f" in
    *.py)
      if ! PYTHONDONTWRITEBYTECODE=1 python -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$f" 2>/dev/null; then
        # python 不在 PATH 时回退到项目虚拟环境
        PYFALLBACK=""
        if [ -x ".venv/Scripts/python.exe" ]; then
          PYFALLBACK=".venv/Scripts/python.exe"
        elif [ -x ".venv/bin/python" ]; then
          PYFALLBACK=".venv/bin/python"
        fi
        if [ -z "$PYFALLBACK" ] || ! "$PYFALLBACK" -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$f" 2>/dev/null; then
          echo "[pre-commit] Python 语法错误: $f"; fail=1
        fi
      fi;;
  esac
done

# 5) .env 是否仍被跟踪
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "[pre-commit] 警告：.env 仍被 git 跟踪！执行 git rm --cached .env 修复"
fi

if [ $fail -eq 1 ]; then
  echo ""
  echo "[pre-commit] 提交被拦截。确属误拦时用 --no-verify 并在提交信息中注明原因。"
  exit 1
fi
exit 0
"""


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hooks_dir = os.path.join(repo, ".git", "hooks")
    hook_path = os.path.join(hooks_dir, "pre-commit")
    if not os.path.isdir(hooks_dir):
        print(f"[install] 未找到 git hooks 目录: {hooks_dir}")
        return 1
    with open(hook_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HOOK)
    os.chmod(hook_path, 0o755)
    print(f"[install] pre-commit 已安装: {hook_path}")
    # 环境自检：.env 是否仍被跟踪
    env_tracked = os.system(f'git -C "{repo}" ls-files --error-unmatch .env >/dev/null 2>&1') == 0
    if env_tracked:
        print("[install] 警告: .env 仍被 git 跟踪，请执行 git rm --cached .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
