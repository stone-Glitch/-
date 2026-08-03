#!/usr/bin/env python3
"""
full_force_sync.py
大范围全覆盖同步脚本：
- 自动添加所有变更（新增、修改、删除）
- 提交全部改动
- 安全强制推送（--force-with-lease）完全覆盖远程分支
用法: python full_force_sync.py ["自定义提交信息"]
"""

import subprocess
import sys
import os
from datetime import datetime

def run_git_command(cmd, check=True, capture=True):
    """执行 Git 命令，可选择是否捕获输出"""
    try:
        if capture:
            result = subprocess.run(
                cmd,
                shell=True,
                check=check,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return result.stdout.strip(), result.stderr.strip()
        else:
            # 实时输出模式（用于 push 显示进度）
            subprocess.run(cmd, shell=True, check=check)
            return "", ""
    except subprocess.CalledProcessError as e:
        print(f"❌ Git 命令失败: {cmd}")
        print(e.stderr)
        sys.exit(1)

def get_current_branch():
    """获取当前分支名"""
    out, _ = run_git_command("git branch --show-current")
    return out

def has_changes():
    """检查工作区是否有任何变更（包括未跟踪文件）"""
    out, _ = run_git_command("git status --porcelain")
    return bool(out.strip())

def main():
    # 1. 检查是否在 Git 仓库内
    if not os.path.isdir(".git"):
        print("❌ 错误: 当前目录不是 Git 仓库，请进入仓库目录后运行。")
        sys.exit(1)

    # 2. 检查是否有变更需要提交
    if not has_changes():
        print("ℹ️  工作区是干净的，没有任何变更。无需提交，直接进行强制覆盖推送。")
    else:
        print("📦 检测到工作区变更，执行全量暂存 (git add -A)...")
        run_git_command("git add -A", capture=False)
        print("✅ 已暂存所有变更（新增/修改/删除）")

        # 3. 提交
        commit_msg = sys.argv[1] if len(sys.argv) > 1 else f"大范围全覆盖自动同步于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        run_git_command(f'git commit -m "{commit_msg}"', capture=False)
        print(f"✅ 已提交: {commit_msg}")

    # 4. 获取当前分支
    branch = get_current_branch()
    if not branch:
        print("❌ 无法获取当前分支，请确保已切换到有效分支。")
        sys.exit(1)

    # 5. 安全强制推送（全覆盖远程）
    print(f"🚀 正在执行安全强制推送 (git push --force-with-lease) 到 origin/{branch} ...")
    # 这里使用 capture=False 让 git 输出实时日志（如进度条）
    run_git_command(f"git push --force-with-lease origin {branch}", capture=False)
    print(f"✅ 大范围全覆盖完成！本地分支 {branch} 已完全覆盖远程仓库。")

if __name__ == "__main__":
    main()