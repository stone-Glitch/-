#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话框基础工具 - 线程安全文本操作、友好错误翻译、临时目录管理
"""
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from logger import default_logger as logger


# ===== dialogs 级临时目录跟踪 + atexit 兜底清理 =====
_DIALOG_TEMP_DIRS: list[Path] = []
_DIALOG_TEMP_DIRS_LOCK = threading.Lock()


def register_dialog_temp_dir(p) -> None:
    """把临时目录注册到 atexit 兜底。"""
    if not p:
        return
    try:
        pp = Path(p)
    except Exception:
        return
    with _DIALOG_TEMP_DIRS_LOCK:
        _DIALOG_TEMP_DIRS.append(pp)


def unregister_dialog_temp_dir(p) -> None:
    """手动清理后把注册项移除。"""
    if not p:
        return
    try:
        pp = Path(p)
    except Exception:
        return
    with _DIALOG_TEMP_DIRS_LOCK:
        try:
            _DIALOG_TEMP_DIRS.remove(pp)
        except ValueError:
            pass


def force_cleanup_dialog_temp_dirs() -> int:
    """立即清理所有已注册但还存在的临时目录。"""
    with _DIALOG_TEMP_DIRS_LOCK:
        all_dirs = list(_DIALOG_TEMP_DIRS)
        _DIALOG_TEMP_DIRS.clear()
    removed = 0
    for d in all_dirs:
        try:
            if d.exists():
                import shutil as _shu
                _shu.rmtree(str(d), ignore_errors=True)
                removed += 1
        except Exception:
            pass
    return removed


import atexit
atexit.register(force_cleanup_dialog_temp_dirs)


# ===== 线程安全的 Text 控件写入 =====
def _append_text(app, widget, text: str, tag: str | None = None, see_end: bool = True) -> None:
    """
    线程安全 + 窗口已销毁兜底 把 text 追加到 Tk Text widget。
    """
    def _do():
        try:
            try:
                if not widget.winfo_exists():
                    return
            except Exception:
                return
            state = widget.cget("state")
            is_disabled = str(state).lower() == "disabled"
            if is_disabled:
                widget.configure(state="normal")
            if tag:
                widget.insert(tk.END, str(text), tag)
            else:
                widget.insert(tk.END, str(text))
            if see_end:
                try:
                    if widget.winfo_exists():
                        widget.see(tk.END)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                if widget.winfo_exists() and is_disabled:
                    widget.configure(state="disabled")
            except Exception:
                pass
    try:
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            app.after(0, _do)
    except Exception as e:
        logger.debug("_append_text 调度失败: %s", e)


def _clear_text(app, widget) -> None:
    """线程安全 + 窗口已销毁兜底 清空 Text widget 内容"""
    def _do():
        try:
            try:
                if not widget.winfo_exists():
                    return
            except Exception:
                return
            state = widget.cget("state")
            is_disabled = str(state).lower() == "disabled"
            if is_disabled:
                widget.configure(state="normal")
            widget.delete("1.0", tk.END)
        except Exception:
            pass
        finally:
            try:
                if widget.winfo_exists() and is_disabled:
                    widget.configure(state="disabled")
            except Exception:
                pass
    try:
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            app.after(0, _do)
    except Exception as e:
        logger.debug("_clear_text 调度失败: %s", e)


# ===== 新手友好错误翻译 =====
def friendly_error(err: object) -> tuple[str, str, str]:
    """
    接收 Exception / str，返回 (标题, 主体, 下一步建议)。
    目的：不让用户看到 UnicodeDecodeError / PermissionError 这种词。
    """
    msg = str(err) if not isinstance(err, Exception) else str(err)
    typ = type(err).__name__ if isinstance(err, Exception) else ""
    lower = (typ + " " + msg).lower()

    # === 文件 / 路径 ===
    if isinstance(err, FileNotFoundError) or "filenotfound" in lower:
        return ("找不到文件 😟",
                f"程序在找这个文件但没找到：\n{msg}\n",
                "👉 确认一下：\n  ① 文件真的在那个文件夹里吗？\n  ② 文件名有没有拼错？\n  ③ 文件是不是被你挪走了？")
    if isinstance(err, PermissionError) or "permission denied" in lower or "拒绝访问" in msg or "access" in lower:
        return ("没有权限打开 😟",
                f"要访问的文件/文件夹被系统锁定或权限不足：\n{msg}\n",
                "👉 试一下：\n  ① 先关闭其它可能占用这个文件的程序\n  ② 文件如果在 C:\\Program Files 里，换个普通目录（如 D:\\分子文件）\n  ③ 以管理员身份运行软件")
    if isinstance(err, IsADirectoryError) or "is a directory" in lower:
        return ("这是个文件夹，不是文件 😅",
                f"你或程序把文件夹当成文件来用了：\n{msg}\n",
                "👉 重新选择一次，要选具体的文件（.mol / .xyz / .csv 等）")
    if "路径" in msg and ("非法" in msg or "无效" in msg or ".." in msg):
        return ("文件名不合法 😅",
                msg,
                "👉 文件名里不要出现这些字符：\\ / : * ? \" < > | \n   也不要写 '../' 往上级目录跑")

    # === 分子文件解析 ===
    if "xyz" in lower and ("解析" in msg or "format" in lower or "cannot" in lower):
        return ("分子文件读不懂 😟",
                f"这份 .xyz 或结构文件格式不对：\n{msg}\n",
                "👉 检查一下文件前两行：\n  第 1 行 = 原子总数（一个数字）\n  第 2 行 = 注释（可以空一行）\n  第 3 行起 = 元素符号 x y z")
    if "openbabel" in lower or "obabel" in lower or "obabel not found" in lower:
        return ("没检测到 OpenBabel 😟",
                "需要先安装 OpenBabel 才能做分子格式转换 / 画图",
                "👉 安装方法（任选其一）：\n  ① conda install openbabel\n  ② pip install openbabel\n  ③ 官网下载 https://openbabel.org/")

    # === PSI4 计算 ===
    if "psi4" in lower and ("not found" in lower or "module not found" in lower or "no module named" in lower):
        return ("没检测到 PSI4 😟",
                "做量子化学计算需要先装 PSI4",
                "👉 推荐用 conda 安装（约 1GB）：\n  conda install -c psi4 psi4\n  不想装也没关系，本软件的文件管理/动画功能都能用")
    if "psi4" in lower and ("basis" in lower or "basis set" in lower):
        return ("基组名字不对 😅",
                msg,
                "👉 在下拉框里选一个常见的：6-31g* / def2-svp / cc-pvdz")
    if "psi4" in lower and ("pcm" in lower or "solvent" in lower):
        return ("溶剂模型计算失败 😟",
                f"PCM/SMD 算不下去：\n{msg}\n",
                "👉 自动切换回气相重新计算过了，你可以直接用气相结果\n   或者换个溶剂再试")
    if "scf" in lower and "not converged" in lower:
        return ("波函数没收敛 😟",
                f"电子结构迭代没算出来：\n{msg}\n",
                "👉 试一下：\n  ① 把方法改成 HF（更简单更稳）\n  ② 检查初始分子结构是不是特别奇怪\n  ③ 增加迭代步数")
    if "内存" in msg or "memory" in lower:
        return ("内存不够啦 😟", msg,
                "👉 在参数里把 PSI4 内存调大，或关闭其它占内存的大程序")

    # === 字符编码 ===
    if isinstance(err, UnicodeDecodeError) or "unicodedecodeerror" in lower or "codec" in lower:
        return ("文件编码看不懂 😟",
                f"文件是用其它编码存的，解析失败：\n{msg}\n",
                "👉 用记事本打开该文件 → 另存为 → 编码选「UTF-8」再保存")
    if isinstance(err, UnicodeEncodeError) or "unicodeencodeerror" in lower:
        return ("写入时编码失败 😟",
                "文件名或内容中有奇怪的字符，写入失败",
                "👉 把文件名改成纯英文 / 中文数字，避免 emoji 或奇怪符号")

    # === 映射 / 对照表 ===
    if "csv" in lower and ("列" in msg or "english" in lower or "chinese" in lower):
        return ("CSV 格式不对 😟",
                msg,
                "👉 对照表 .csv 长这样（两列，第一行表头可省略）：\n   english,chinese\n   ch4,甲烷\n   h2o,水")

    # === 反应动画 ===
    if "至少提供" in msg or "请至少" in msg and ("反应物" in msg or "产物" in msg):
        return ("还差一些东西 😅", msg,
                "👉 用上面 「常见反应模板」 一键填好，或者手动在反应物/产物列表里各添加至少 1 个文件")
    if "atom" in lower and "对齐" in lower:
        return ("原子对不上 😟", msg,
                "👉 反应物和产物的原子种类/数量要一致\n   （或者用本软件的「模板」来生成，已经帮你配平好了）")
    if "图像" in msg or "image" in lower or "pillow" in lower or "pil" in lower:
        return ("图像模块没装 😟", "做 GIF / 图片预览需要 Pillow",
                "👉 执行：pip install Pillow")
    if "ffmpeg" in lower:
        return ("没检测到 ffmpeg 😟", "导出 MP4 需要 ffmpeg",
                "👉 安装后再导出 MP4；GIF 不需要 ffmpeg 可以直接生成")

    # === 兜底 ===
    title = "出了点小问题"
    body = f"具体信息：\n{msg}" if msg else "程序遇到了预料之外的情况"
    suggestion = "👉 如果反复出现，把报错文字发给开发者即可"
    return (title, body, suggestion)


def show_friendly_error(app, err: object, parent=None) -> None:
    """封装：把异常对象转成大白话弹框"""
    title, body, hint = friendly_error(err)
    parent = parent or app
    try:
        messagebox.showerror(title, f"{body}\n\n{hint}", parent=parent)
    except Exception:
        try:
            tk.messagebox.showerror(title, f"{body}\n\n{hint}", parent=parent)
        except Exception:
            print(f"[{title}] {body}\n{hint}")