#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话框模块 - 文件类型选择、PSI4 计算、OpenBabel 工具
修复：返回值解包、进度回调传递、子线程 UI 操作必须走 after(0)
"""
import os
import sys
import csv
import json
import atexit
import weakref
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from pathlib import Path

from logger import default_logger as logger
from constants import PSI4_PRESETS, PSI4_TASKS, SUPPORTED_EXTS, RUN_PRESETS
import openbabel_utils as ob_utils


# ===== dialogs 级临时目录跟踪 + atexit 兜底清理 =====
# 所有模板按钮 / 对话框临时创建的目录都在这里注册一份，进程结束时统一 rmtree
_DIALOG_TEMP_DIRS: list[Path] = []
_DIALOG_TEMP_DIRS_LOCK = threading.Lock()


def register_dialog_temp_dir(p) -> None:
    """把临时目录注册到 atexit 兜底。可以是 str / Path。忽略 None 或空串。"""
    if not p:
        return
    try:
        pp = Path(p)
    except Exception:
        return
    with _DIALOG_TEMP_DIRS_LOCK:
        _DIALOG_TEMP_DIRS.append(pp)


def unregister_dialog_temp_dir(p) -> None:
    """手动清理后把注册项移除，避免 atexit 扫一堆已清理的路径。"""
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
    """立即清理所有已注册但还存在的临时目录；返回实际删掉的目录数量。"""
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


atexit.register(force_cleanup_dialog_temp_dirs)


class Dialogs:
    def __init__(self, app, controller):
        self.app = app
        self.controller = controller

    # ---- 线程安全的 Text 控件写入（所有后台子线程必须走这个！） ----
    def _append_text(self, widget, text: str, tag: str | None = None, see_end: bool = True) -> None:
        """
        **线程安全 + 窗口已销毁兜底** 把 text 追加到 Tk Text widget。
        无论在主线程 / 子线程调用都 OK；widget 已不存在 / 窗口已关闭时静默跳过，
        避免关闭 PSI4 对话框后后台线程抛出 TclError 污染日志。
        """
        def _do():
            try:
                # ———— 先确认 widget 还活着（窗口关闭或父控件已 destroy 都会让此处 False）————
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
                # cget / insert 过程中 window 被销毁也会抛 TclError，一律吞掉（非致命）
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
                self.app.after(0, _do)
        except Exception as e:
            logger.debug("Dialogs._append_text 调度失败: %s", e)

    def _clear_text(self, widget) -> None:
        """**线程安全 + 窗口已销毁兜底** 清空 Text widget 内容"""
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
                self.app.after(0, _do)
        except Exception as e:
            logger.debug("Dialogs._clear_text 调度失败: %s", e)

    # ---- 新手友好：把技术错误翻译成大白话 + 告诉用户怎么做 ----
    @staticmethod
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

    def show_friendly(self, err: object, parent=None) -> None:
        """封装：把异常对象转成大白话弹框"""
        title, body, hint = self.friendly_error(err)
        parent = parent or self.app
        try:
            messagebox.showerror(title, f"{body}\n\n{hint}", parent=parent)
        except Exception:
            try:
                tk.messagebox.showerror(title, f"{body}\n\n{hint}", parent=parent)
            except Exception:
                print(f"[{title}] {body}\n{hint}")

    # ---- 安全的外部工具路径解析（B607/CWE-426 可执行文件劫持防御） ----
    @staticmethod
    def _resolve_iqmol_exe(name_or_path: str) -> str:
        """
        安全解析 IQmol 可执行文件绝对路径：
        - 用户输入绝对路径 → expanduser + resolve(strict=True) + 允许符号链接
        - 用户输入相对名 → shutil.which 解析到 PATH 中的绝对路径，解析失败直接抛 RuntimeError
        - 无论哪种方式，**真实路径**都不能在 tempdir / cwd / 用户主目录（防止工作目录同名恶意可执行劫持）
        - 拒绝执行相对名，因为 Windows CreateProcessW 会先搜当前目录。

        H-1 修复：不再拒绝符号链接；Linux/macOS 下 /usr/local/bin/IQmol 几乎都是 symlink，
        之前的检查会导致 IQmol 永远找不到。
        """
        import shutil as _shutil
        import tempfile as _tempfile

        def _safe_real(p: Path, *, display_name: str = "IQmol") -> Path:
            try:
                real = p.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError(f"{display_name} 路径不存在或不可读: {p}") from exc
            if not real.is_file():
                raise RuntimeError(f"{display_name} 路径不是文件: {real}")
            # 拒绝 tempdir / cwd / home 下的真实可执行（用户可写目录，可能存在同名恶意文件）
            unsafe_roots: list[Path] = []
            for _cand in (
                _tempfile.gettempdir(),
                os.getcwd(),
                os.path.expanduser("~"),
            ):
                try:
                    unsafe_roots.append(Path(_cand).resolve(strict=False))
                except Exception:
                    pass
            for root in unsafe_roots:
                try:
                    real.relative_to(root)
                    raise RuntimeError(
                        f"出于安全考虑，拒绝执行在可写目录下的 {display_name} 真实路径: {real}（父目录={root}），"
                        "请使用系统路径（如 /Applications/IQmol.app/Contents/MacOS/IQmol）下的安装。"
                    )
                except ValueError:
                    pass
            return real

        candidate = str(name_or_path).strip() or "IQmol"
        if os.sep in candidate or (os.altsep and os.altsep in candidate) or Path(candidate).is_absolute():
            abs_path = Path(candidate).expanduser()
            return str(_safe_real(abs_path, display_name="IQmol"))
        # 仅接受大写 IQmol / 小写 iqmol，避免常见大小写拼错时 which 不到
        resolved: str | None = None
        for name in filter(None, {candidate, candidate.lower(), "IQmol", "iqmol"}):
            r = _shutil.which(name)
            if r:
                resolved = r
                break
        if not resolved:
            raise RuntimeError(
                f"未在 PATH 中找到 IQmol（当前输入: {candidate!r}），请安装并添加到 PATH，"
                "或在对话框中指定 IQmol 可执行文件的绝对路径（已拒绝使用相对名执行，防止工作目录同名恶意可执行劫持）。"
            )
        return str(_safe_real(Path(resolved), display_name="IQmol"))

    def _safe_open_file(self, target: str | os.PathLike[str]) -> None:
        """
        用系统默认程序打开文件/文件夹：
        - Windows: 保持 os.startfile
        - macOS: 写死绝对路径 /usr/bin/open（CWE-426 防止 PATH 劫持）
        - Linux: 写死绝对路径 /usr/bin/xdg-open（同上）
        """
        target_str = os.fspath(target)
        if sys.platform == "win32":
            os.startfile(target_str)
            return
        if sys.platform == "darwin":
            if not Path("/usr/bin/open").exists():
                raise OSError("macOS 缺少系统工具 /usr/bin/open")
            subprocess.run(["/usr/bin/open", target_str], check=False)
            return
        if Path("/usr/bin/xdg-open").exists():
            subprocess.run(["/usr/bin/xdg-open", target_str], check=False)
            return
        raise OSError("找不到 xdg-open (Linux) 或 open (macOS) 系统工具")

    def _ask_xyz_files(self):
        return filedialog.askopenfilenames(
            initialdir=str(self.app.controller.model.work_dir),
            filetypes=[("XYZ files", "*.xyz"), ("All files", "*.*")],
        )

    def _add_to_listbox(self, listbox, files):
        for f in files:
            listbox.insert(tk.END, f)

    # ---------- 文件类型选择 ----------
    def show_ext_filter_dialog(self):
        dialog = tk.Toplevel(self.app)
        dialog.title("选择文件类型")
        dialog.geometry("350x300")
        dialog.transient(self.app)
        dialog.grab_set()

        all_exts = sorted(SUPPORTED_EXTS)
        current_exts = {e.strip() for e in self.app.ext_filter_var.get().split(',') if e.strip()}
        if not current_exts:
            current_exts = set(all_exts)

        ext_vars = {}
        select_all_var = tk.BooleanVar(value=True)

        def update_select_all():
            all_checked = all(var.get() for var in ext_vars.values())
            select_all_var.set(all_checked)

        def on_select_all_change():
            state = select_all_var.get()
            for var in ext_vars.values():
                var.set(state)

        canvas = tk.Canvas(dialog, borderwidth=0)
        frame = ttk.Frame(canvas)
        scrollbar = ttk.Scrollbar(dialog, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        canvas.create_window((0, 0), window=frame, anchor="nw")

        ttk.Checkbutton(frame, text="全选", variable=select_all_var, command=on_select_all_change).grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)

        for i, ext in enumerate(all_exts, start=1):
            var = tk.BooleanVar(value=ext in current_exts)
            ext_vars[ext] = var
            chk = ttk.Checkbutton(frame, text=ext, variable=var)
            chk.grid(row=i, column=0, sticky=tk.W, padx=20, pady=2)
            var.trace('w', lambda *args: update_select_all())

        update_select_all()

        frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)

        def on_ok():
            selected = [ext for ext, var in ext_vars.items() if var.get()]
            if not selected:
                self.app.helpers.on_log("⚠️ 未选择任何文件类型，将显示所有支持的类型", 'warning')
                self.app.ext_filter_var.set("")
            else:
                self.app.ext_filter_var.set(",".join(selected))
            self.app.helpers.update_ext_display()
            self.controller.scan_files()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)

    # ---------- PSI4 计算对话框 ----------
    def show_psi4_dialog(self):
        selected = self.app.helpers.get_selected_filenames()
        if not selected and self.app.fix_mode_var.get() != "scan":
            self.app.helpers.on_log("⚠️ 请先在文件列表中选择一个或多个文件", 'warning')
            return

        from psi4_compute import check_psi4_installed
        _ok, _msg, _det = check_psi4_installed()
        if not _ok:
            self.app.helpers.on_log(f"❌ {_msg}", 'error')
            return
        # 显示警告（例如 CPHF NMR 没开）：把 warnings 列出来给用户
        for _w in _det.get("warnings", []):
            self.app.helpers.on_log(f"⚠️ {_w}", 'warning')
        self.app.helpers.on_log(f"✅ {_msg}", 'info')

        dialog = tk.Toplevel(self.app)
        dialog.title("⚡ PSI4 计算设置")
        dialog.geometry("600x650")
        dialog.transient(self.app)
        dialog.grab_set()

        ttk.Label(dialog, text=f"已选 {len(selected)} 个文件", font=('Arial', 10, 'bold')).pack(pady=10)

        # 任务类型
        frame1 = ttk.Frame(dialog)
        frame1.pack(pady=5, fill=tk.X, padx=10)
        ttk.Label(frame1, text="任务类型:").pack(side=tk.LEFT, padx=5)
        # 汉化：下拉直接显示中文，内部用英文 key 做真实计算值
        TASK_DISPLAY_TO_KEY = {v: k for k, v in PSI4_TASKS.items()}
        TASK_KEY_TO_DISPLAY = dict(PSI4_TASKS)
        initial_key = self.app.psi4_last_task
        if initial_key not in TASK_KEY_TO_DISPLAY:
            initial_key = "energy"
        task_var = tk.StringVar(value=initial_key)
        task_menu_var = tk.StringVar(value=TASK_KEY_TO_DISPLAY[initial_key])
        task_menu = ttk.Combobox(
            frame1,
            textvariable=task_menu_var,
            values=list(TASK_DISPLAY_TO_KEY.keys()),
            state="readonly",
            width=15,
        )
        task_menu.pack(side=tk.LEFT, padx=5)
        task_desc_var = tk.StringVar(value=TASK_KEY_TO_DISPLAY[initial_key])
        ttk.Label(frame1, textvariable=task_desc_var, foreground="gray").pack(side=tk.LEFT, padx=10)

        def _sync_from_display(*_args, **_kwargs):
            """下拉的中文 -> task_var(英文key) + task_desc_var(中文) 同步。"""
            disp = task_menu_var.get()
            if disp in TASK_DISPLAY_TO_KEY:
                real_key = TASK_DISPLAY_TO_KEY[disp]
                task_var.set(real_key)
                task_desc_var.set(disp)

        def _sync_from_key(*_args, **_kwargs):
            """别处修改了 task_var(英文key) -> 下拉显示同步回中文。"""
            real_key = task_var.get()
            if real_key in TASK_KEY_TO_DISPLAY:
                disp = TASK_KEY_TO_DISPLAY[real_key]
                task_menu_var.set(disp)
                task_desc_var.set(disp)

        task_menu_var.trace_add("write", _sync_from_display)
        task_menu.bind("<<ComboboxSelected>>", lambda e: _sync_from_display())
        task_var.trace_add("write", _sync_from_key)

        # 运行级别
        frame_runlevel = ttk.Frame(dialog)
        frame_runlevel.pack(pady=5, fill=tk.X, padx=10)
        runlevel_grid = ttk.Frame(frame_runlevel)
        runlevel_grid.pack(fill=tk.X)
        ttk.Label(runlevel_grid, text="🎯 运行级别：").grid(row=0, column=0, padx=5, sticky=tk.W)
        runlevel_var = tk.StringVar(value="")
        runlevel_combo = ttk.Combobox(runlevel_grid, textvariable=runlevel_var, values=list(RUN_PRESETS.keys()), state="readonly", width=40)
        runlevel_combo.grid(row=0, column=1, padx=5, sticky=tk.W)

        ff_hint_label = ttk.Label(frame_runlevel, text="快速模式：会跳过 PSI4，直接使用 MMFF94/UFF 力场优化", foreground="red")
        ff_hint_label.pack_forget()

        # 预设
        frame2 = ttk.Frame(dialog)
        frame2.pack(pady=5, fill=tk.X, padx=10)
        ttk.Label(frame2, text="预设:").pack(side=tk.LEFT, padx=5)
        preset_var = tk.StringVar(value="标准 (B3LYP/6-31G*)")
        preset_combo = ttk.Combobox(frame2, textvariable=preset_var, values=list(PSI4_PRESETS.keys()), state="readonly", width=25)
        preset_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(frame2, text="应用", command=lambda: self._apply_preset(preset_var, method_var, basis_var)).pack(side=tk.LEFT, padx=5)

        # 方法/基组
        frame3 = ttk.Frame(dialog)
        frame3.pack(pady=5, fill=tk.X, padx=10)
        ttk.Label(frame3, text="方法:").pack(side=tk.LEFT, padx=5)
        method_var = tk.StringVar(value=self.app.psi4_last_method)
        ttk.Entry(frame3, textvariable=method_var, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Label(frame3, text="基组:").pack(side=tk.LEFT, padx=10)
        basis_var = tk.StringVar(value=self.app.psi4_last_basis)
        ttk.Entry(frame3, textvariable=basis_var, width=12).pack(side=tk.LEFT, padx=5)

        # 扫描参数
        self.scan_frame = ttk.LabelFrame(dialog, text="扫描参数 (仅扫描任务)", padding="5")

        # 扫描模式
        ttk.Label(self.scan_frame, text="模式:").pack(side=tk.LEFT, padx=5)
        self.scan_mode_var = tk.StringVar(value="线性插值（反应物→产物）")
        scan_mode_menu = ttk.Combobox(self.scan_frame, textvariable=self.scan_mode_var,
                                      values=["线性插值（反应物→产物）", "刚性扫描（原子对）"],
                                      state="readonly", width=25)
        scan_mode_menu.pack(side=tk.LEFT, padx=5)

        # 反应物列表 (线性插值)
        self.react_frame = ttk.LabelFrame(self.scan_frame, text="反应物文件 (多选)", padding="3")
        self.reactant_listbox = tk.Listbox(self.react_frame, height=3, selectmode=tk.EXTENDED, width=20)
        scroll_r = ttk.Scrollbar(self.react_frame, orient=tk.VERTICAL, command=self.reactant_listbox.yview)
        self.reactant_listbox.configure(yscrollcommand=scroll_r.set)
        self.reactant_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_r.pack(side=tk.RIGHT, fill=tk.Y)
        btn_r_add = ttk.Button(self.react_frame, text="添加", command=self._add_reactant)
        btn_r_del = ttk.Button(self.react_frame, text="删除选中", command=self._del_reactant)
        btn_r_add.pack(side=tk.LEFT, padx=2)
        btn_r_del.pack(side=tk.LEFT, padx=2)
        self.react_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # 产物列表 (线性插值)
        self.prod_frame = ttk.LabelFrame(self.scan_frame, text="产物文件 (多选)", padding="3")
        self.product_listbox = tk.Listbox(self.prod_frame, height=3, selectmode=tk.EXTENDED, width=20)
        scroll_p = ttk.Scrollbar(self.prod_frame, orient=tk.VERTICAL, command=self.product_listbox.yview)
        self.product_listbox.configure(yscrollcommand=scroll_p.set)
        self.product_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_p.pack(side=tk.RIGHT, fill=tk.Y)
        btn_p_add = ttk.Button(self.prod_frame, text="添加", command=self._add_product)
        btn_p_del = ttk.Button(self.prod_frame, text="删除选中", command=self._del_product)
        btn_p_add.pack(side=tk.LEFT, padx=2)
        btn_p_del.pack(side=tk.LEFT, padx=2)
        self.prod_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # 步数
        step_frame = ttk.Frame(self.scan_frame)
        ttk.Label(step_frame, text="步数:").pack(side=tk.LEFT, padx=5)
        self.interp_steps_var = tk.StringVar(value="20")
        ttk.Entry(step_frame, textvariable=self.interp_steps_var, width=6).pack(side=tk.LEFT, padx=5)
        step_frame.pack(side=tk.BOTTOM, pady=5)

        # 刚性扫描参数 (初始隐藏)
        self.rigid_frame = ttk.Frame(self.scan_frame)
        ttk.Label(self.rigid_frame, text="原子对 (如 1-2):").pack(side=tk.LEFT, padx=2)
        self.scan_atoms_var = tk.StringVar(value="1-2")
        ttk.Entry(self.rigid_frame, textvariable=self.scan_atoms_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.rigid_frame, text="起始(Å):").pack(side=tk.LEFT, padx=2)
        self.scan_start_var = tk.StringVar(value="1.5")
        ttk.Entry(self.rigid_frame, textvariable=self.scan_start_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.rigid_frame, text="终止(Å):").pack(side=tk.LEFT, padx=2)
        self.scan_end_var = tk.StringVar(value="4.0")
        ttk.Entry(self.rigid_frame, textvariable=self.scan_end_var, width=6).pack(side=tk.LEFT, padx=2)
        self.rigid_frame.pack_forget()

        def on_mode_change(event):
            if self.scan_mode_var.get() == "线性插值（反应物→产物）":
                self.react_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
                self.prod_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
                self.rigid_frame.pack_forget()
            else:
                self.react_frame.pack_forget()
                self.prod_frame.pack_forget()
                self.rigid_frame.pack(fill=tk.X, pady=5)
        scan_mode_menu.bind("<<ComboboxSelected>>", on_mode_change)
        # 初始状态
        on_mode_change(None)

        # 高级选项
        advanced_frame = ttk.LabelFrame(dialog, text="高级选项", padding="5")
        advanced_frame.pack(pady=5, fill=tk.X, padx=10)

        ttk.Label(advanced_frame, text="电荷:").pack(side=tk.LEFT, padx=5)
        charge_var = tk.StringVar(value="0")
        ttk.Entry(advanced_frame, textvariable=charge_var, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(advanced_frame, text="多重度:").pack(side=tk.LEFT, padx=10)
        mult_var = tk.StringVar(value="1")
        ttk.Entry(advanced_frame, textvariable=mult_var, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(advanced_frame, text="溶剂:").pack(side=tk.LEFT, padx=10)
        solvent_var = tk.StringVar(value="")
        ttk.Combobox(advanced_frame, textvariable=solvent_var, values=["", "water", "ethanol", "methanol", "acetone", "thf"], state="readonly", width=10).pack(side=tk.LEFT, padx=5)
        d3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(advanced_frame, text="DFT-D3", variable=d3_var).pack(side=tk.LEFT, padx=10)
        ttk.Label(advanced_frame, text="内存(GB):").pack(side=tk.LEFT, padx=10)
        memory_var = tk.IntVar(value=4)
        ttk.Spinbox(advanced_frame, from_=1, to=128, textvariable=memory_var, width=5).pack(side=tk.LEFT, padx=5)

        # 输出目录
        frame4 = ttk.Frame(dialog)
        frame4.pack(pady=5, fill=tk.X, padx=10)
        ttk.Label(frame4, text="输出目录:").pack(side=tk.LEFT, padx=5)
        out_dir_var = tk.StringVar(value="")
        ttk.Entry(frame4, textvariable=out_dir_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Button(frame4, text="浏览", command=lambda: self.app.helpers.browse_dir(out_dir_var)).pack(side=tk.LEFT, padx=5)
        ttk.Label(frame4, text="(留空使用源目录)", foreground="gray").pack(side=tk.LEFT, padx=5)

        # 结果显示
        result_text = scrolledtext.ScrolledText(dialog, height=8, wrap=tk.WORD, font=('Consolas', 9))
        result_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="▶ 开始计算", command=lambda: self._run_psi4_batch(
            selected, task_var, method_var, basis_var, charge_var, mult_var,
            solvent_var, d3_var, out_dir_var, preset_var, result_text, dialog
        )).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=10)

        # 根据任务类型初始显示扫描参数：用 task_var 追踪（无论是用户切换下拉还是运行级别预设改任务都会生效）
        def on_task_var_changed(*_args, **_kwargs):
            if task_var.get() == 'scan':
                self.scan_frame.pack(pady=5, fill=tk.X, padx=10, before=advanced_frame)
            else:
                self.scan_frame.pack_forget()

        task_var.trace_add("write", on_task_var_changed)
        on_task_var_changed()  # 初始状态

        def on_runlevel_change(event):
            value = runlevel_var.get()
            if not value:
                ff_hint_label.pack_forget()
                return
            preset_info = RUN_PRESETS.get(value)
            if not preset_info:
                return
            task_type = preset_info.get("task_type", "")
            method = preset_info.get("method", "")
            basis = preset_info.get("basis", "")
            preset_name = preset_info.get("preset_name", "")
            solvent = preset_info.get("solvent", None)
            d3 = preset_info.get("d3", False)
            memory_gb = preset_info.get("memory_gb", 4)

            if task_type == "_ff_optimize":
                task_var.set("optimize")
                task_desc_var.set(PSI4_TASKS.get("optimize", ""))
                ff_hint_label.pack(pady=5, padx=5, anchor=tk.W)
            else:
                task_var.set(task_type)
                task_desc_var.set(PSI4_TASKS.get(task_type, ""))
                ff_hint_label.pack_forget()

            method_var.set(method)
            basis_var.set(basis)

            if preset_name in PSI4_PRESETS:
                preset_var.set(preset_name)

            solvent_var.set(solvent if solvent else "")
            d3_var.set(d3)
            memory_var.set(memory_gb)

            if task_var.get() == 'scan':
                self.scan_frame.pack(pady=5, fill=tk.X, padx=10, before=advanced_frame)
            else:
                self.scan_frame.pack_forget()

        runlevel_combo.bind("<<ComboboxSelected>>", on_runlevel_change)

    def _apply_preset(self, preset_var, method_var, basis_var):
        preset = preset_var.get()
        if preset in PSI4_PRESETS:
            info = PSI4_PRESETS[preset]
            method_var.set(info.get("method", ""))
            basis_var.set(info.get("basis", ""))

    def _add_reactant(self):
        self._add_to_listbox(self.reactant_listbox, self._ask_xyz_files())

    def _del_reactant(self):
        selected = self.reactant_listbox.curselection()
        for idx in reversed(selected):
            self.reactant_listbox.delete(idx)

    def _add_product(self):
        self._add_to_listbox(self.product_listbox, self._ask_xyz_files())

    def _del_product(self):
        selected = self.product_listbox.curselection()
        for idx in reversed(selected):
            self.product_listbox.delete(idx)

    def _run_psi4_batch(self, files, task_var, method_var, basis_var, charge_var,
                        mult_var, solvent_var, d3_var, out_dir_var, preset_var,
                        result_text, dialog):
        from psi4_compute import run_psi4_task, run_linear_scan, run_rigid_scan

        task = task_var.get()
        method = method_var.get().strip()
        basis = basis_var.get().strip()
        charge = int(charge_var.get() or 0)
        mult = int(mult_var.get() or 1)
        solvent = solvent_var.get().strip() or None
        d3 = d3_var.get()
        out_dir = out_dir_var.get().strip() or None
        preset = preset_var.get()

        if not method or not basis:
            result_text.insert(tk.END, "❌ 方法和基组不能为空\n")
            return

        # 不管什么任务，都先记忆最近一次配置
        self.app.psi4_last_method = method
        self.app.psi4_last_basis = basis
        self.app.psi4_last_task = task
        self.app.config_data["psi4_config"] = {
            "last_method": method,
            "last_basis": basis,
            "last_task": task,
        }
        from config import save_config
        save_config(self.app.config_data)

        # 扫描任务特殊处理
        if task == 'scan':
            mode = self.scan_mode_var.get()
            if mode == "线性插值（反应物→产物）":
                reactant_files = list(self.reactant_listbox.get(0, tk.END))
                product_files = list(self.product_listbox.get(0, tk.END))
                if not reactant_files or not product_files:
                    result_text.insert(tk.END, "❌ 请添加反应物和产物文件\n")
                    return
                try:
                    steps = int(str(self.interp_steps_var.get()).strip())
                    if steps < 2:
                        raise ValueError
                except (ValueError, TypeError):
                    result_text.insert(tk.END, "❌ 步数必须为大于1的整数\n")
                    return

                def task_process(**kwargs):
                    self._append_text(result_text, "🔬 开始线性插值扫描\n")
                    self._append_text(result_text, f"   反应物: {len(reactant_files)} 个文件\n")
                    self._append_text(result_text, f"   产物: {len(product_files)} 个文件\n")
                    self._append_text(result_text, f"   步数: {steps}, 方法: {method}, 基组: {basis}\n")

                    res = self.app.controller.model.run_linear_scan(
                        reactant_files, product_files, steps, method, basis, out_dir,
                        preset, solvent, d3, charge, mult,
                        progress_callback=kwargs.get('_progress_callback')
                    )
                    self._display_scan_result(res, result_text)
                    self.app.controller.scan_files()

                self.app.helpers.run_task(task_process)
                dialog.destroy()
                return

            else:  # 刚性扫描
                if not files:
                    result_text.insert(tk.END, "❌ 请选择分子文件\n")
                    return
                fname = files[0]
                file_path = Path(self.app.controller.model.work_dir) / fname
                try:
                    _raw_atoms = str(self.scan_atoms_var.get()).strip().split('-')
                    idx1, idx2 = map(int, _raw_atoms)
                    idx1 -= 1
                    idx2 -= 1
                except (ValueError, TypeError):
                    result_text.insert(tk.END, "❌ 原子对格式错误，请使用如 '1-2'\n")
                    return
                try:
                    start = float(str(self.scan_start_var.get()).strip())
                    end = float(str(self.scan_end_var.get()).strip())
                    steps = int(str(self.interp_steps_var.get()).strip())
                    if steps <= 0:
                        raise ValueError
                except (ValueError, TypeError):
                    result_text.insert(tk.END, "❌ 距离范围或步数格式错误\n")
                    return

                def task_process(**kwargs):
                    self._append_text(result_text, f"🔬 开始刚性扫描: {fname}\n")
                    self._append_text(result_text, f"   方法: {method}, 基组: {basis}\n")
                    self._append_text(result_text, f"   原子对: {idx1+1}-{idx2+1}, 距离: {start}~{end} Å, 步数: {steps}\n")

                    res = run_rigid_scan(
                        str(file_path), (idx1, idx2), (start, end, steps),
                        method, basis, out_dir, preset, solvent, d3,
                        charge, mult, _progress_callback=kwargs.get('_progress_callback')
                    )
                    self._display_scan_result(res, result_text)
                    self.app.controller.scan_files()

                self.app.helpers.run_task(task_process)
                dialog.destroy()
                return

        # 非扫描任务：批量计算
        total = len(files)
        result_text.insert(tk.END, f"🔬 开始批量计算，共 {total} 个文件\n")
        result_text.insert(tk.END, f"   任务: {task}, 方法: {method}, 基组: {basis}\n")
        if solvent:
            result_text.insert(tk.END, f"   溶剂: {solvent}\n")
        if d3:
            result_text.insert(tk.END, f"   DFT-D3 已启用\n")
        result_text.see(tk.END)

        def task_process(**kwargs):
            for idx, fname in enumerate(files):
                file_path = Path(self.app.controller.model.work_dir) / fname
                self._append_text(result_text, f"\n--- ({idx+1}/{total}) {fname} ---\n")

                try:
                    res = run_psi4_task(
                        str(file_path), task, method, basis, out_dir, preset,
                        solvent, d3, charge, mult,
                        _progress_callback=kwargs.get('_progress_callback')
                    )
                    def update_result(r=res, fname=fname):
                        if r["success"]:
                            self._append_text(result_text, "✅ 成功!\n")
                            if r.get("energy") is not None:
                                self._append_text(result_text, f"   能量: {r['energy']:.6f} Hartree\n")
                            if r.get("optimized_xyz"):
                                self._append_text(result_text, "   优化结构已保存\n")
                            if r.get("fchk_file"):
                                self._append_text(result_text, f"   .fchk: {os.path.basename(r['fchk_file'])}\n")
                            self.app.helpers.on_log(f"✅ PSI4 计算完成: {fname}", 'success')
                        else:
                            self._append_text(result_text, f"❌ 失败: {r.get('error', '未知错误')}\n")
                            self.app.helpers.on_log(f"❌ PSI4 计算失败: {fname}", 'error')
                    if threading.current_thread() is threading.main_thread():
                        update_result()
                    else:
                        self.app.after(0, update_result)
                except Exception as e:
                    self._append_text(result_text, f"❌ 异常: {e}\n")
                    self.app.helpers.on_log(f"❌ PSI4 异常: {e}", 'error')

            self._append_text(result_text, "\n🎉 所有任务处理完成！\n")
            self.app.controller.scan_files()

        self.app.helpers.run_task(task_process)

    def _display_scan_result(self, res, result_text):
        if res["success"]:
            self._append_text(result_text, "✅ 扫描完成!\n")
            self._append_text(result_text, f"   XYZ动画: {os.path.basename(res.get('xyz_file', ''))}\n")
            if res.get('plot_file'):
                self._append_text(result_text, f"   能量曲线: {os.path.basename(res['plot_file'])}\n")
            if res.get('ts_file'):
                self._append_text(result_text, f"   TS初猜: {os.path.basename(res['ts_file'])}\n")
            self.app.helpers.on_log("✅ 扫描完成", 'success')
        else:
            self._append_text(result_text, f"❌ 扫描失败: {res.get('error', '未知错误')}\n")
            self.app.helpers.on_log("❌ 扫描失败", 'error')

    def _ask_ob_files(self):
        return filedialog.askopenfilenames(
            initialdir=str(self.app.controller.model.work_dir),
            filetypes=[("分子文件", "*.mol *.xyz *.sdf *.mol2 *.fchk *.out"), ("全部", "*.*")]
        )

    def _add_unique_to_listbox(self, listbox, files):
        for f in files:
            name = os.path.basename(f)
            if name not in listbox.get(0, tk.END):
                listbox.insert(tk.END, name)

    def _delete_selected_from_listbox(self, listbox):
        for idx in reversed(listbox.curselection()):
            listbox.delete(idx)

    # ---------- OpenBabel 工具对话框 ----------
    def show_openbabel_dialog(self):
        available, msg, det = ob_utils.check_openbabel()
        if not available:
            self.app.helpers.on_log(f"❌ Open Babel 不可用: {msg}", 'error')
            return
        # 显示警告（例如某功能缺失）
        for _w in det.get("warnings", []):
            self.app.helpers.on_log(f"⚠️ OB: {_w}", 'warning')
        self.app.helpers.on_log(f"✅ OB: {msg}", 'info')

        dialog = tk.Toplevel(self.app)
        dialog.title("🔬 Open Babel 工具")
        dialog.geometry("700x600")
        dialog.transient(self.app)
        dialog.grab_set()

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 标签1：格式转换
        tab_convert = ttk.Frame(notebook, padding=10)
        notebook.add(tab_convert, text="📄 格式转换")

        ttk.Label(tab_convert, text="将分子文件转换为其他格式", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))
        ttk.Label(tab_convert, text="批量转换文件（输出格式统一）:").grid(row=1, column=0, sticky="nw")

        convert_list_frame = ttk.Frame(tab_convert)
        convert_list_frame.grid(row=1, column=1, padx=5, sticky="nsew")
        convert_listbox = tk.Listbox(convert_list_frame, height=8, selectmode=tk.EXTENDED, width=45)
        convert_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        convert_scroll = ttk.Scrollbar(convert_list_frame, orient=tk.VERTICAL, command=convert_listbox.yview)
        convert_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        convert_listbox.configure(yscrollcommand=convert_scroll.set)

        selected = self.app.helpers.get_selected_filenames()
        for s in selected:
            convert_listbox.insert(tk.END, s)

        convert_btn_col = ttk.Frame(tab_convert)
        convert_btn_col.grid(row=1, column=2, padx=5, sticky="nw")

        def add_convert_files():
            self._add_unique_to_listbox(convert_listbox, self._ask_ob_files())

        def del_convert_selected():
            self._delete_selected_from_listbox(convert_listbox)

        ttk.Button(convert_btn_col, text="添加...", command=add_convert_files).pack(side=tk.TOP, pady=2, fill=tk.X)
        ttk.Button(convert_btn_col, text="删除选中", command=del_convert_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

        ttk.Label(tab_convert, text="输出格式:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        formats = ob_utils.get_supported_formats()
        convert_fmt_var = tk.StringVar(value="xyz" if "xyz" in formats else (formats[0] if formats else ""))
        ttk.Combobox(tab_convert, textvariable=convert_fmt_var, values=formats, state="readonly", width=15).grid(row=2, column=1, sticky=tk.W, padx=5, pady=(10, 0))
        ttk.Label(tab_convert, text="💡 例如: .mol → .xyz", foreground="gray").grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=5)

        ttk.Button(tab_convert, text="🔄 立即转换", command=lambda: self._run_convert_batch(convert_listbox, convert_fmt_var.get(), dialog)).grid(row=4, column=1, pady=10)

        # 标签2：SMILES 生成
        tab_smiles = ttk.Frame(notebook, padding=10)
        notebook.add(tab_smiles, text="🧪 SMILES → 分子")

        ttk.Label(tab_smiles, text="输入 SMILES 生成 3D 分子", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        ttk.Label(tab_smiles, text="SMILES:").grid(row=1, column=0, sticky=tk.W)
        smiles_entry = ttk.Entry(tab_smiles, width=40)
        smiles_entry.insert(0, "CCO")
        smiles_entry.grid(row=1, column=1, padx=5)

        ttk.Label(tab_smiles, text="快速选择:").grid(row=2, column=0, sticky=tk.W, pady=5)
        common_smiles = {"乙醇": "CCO", "苯": "c1ccccc1", "水": "O", "甲烷": "C", "乙烯": "C=C", "乙烷": "CC"}
        combo = ttk.Combobox(tab_smiles, values=list(common_smiles.keys()), state="readonly", width=15)
        combo.grid(row=2, column=1, sticky=tk.W, padx=5)
        combo.bind("<<ComboboxSelected>>", lambda e: smiles_entry.delete(0, tk.END) or smiles_entry.insert(0, common_smiles[combo.get()]))

        ttk.Label(tab_smiles, text="文件名前缀:").grid(row=3, column=0, sticky=tk.W, pady=(10, 0))
        prefix_entry = ttk.Entry(tab_smiles, width=30)
        prefix_entry.insert(0, "my_molecule")
        prefix_entry.grid(row=3, column=1, sticky=tk.W, padx=5, pady=(10, 0))

        gen3d_var = tk.BooleanVar(value=True)
        opt_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tab_smiles, text="生成 3D 结构", variable=gen3d_var).grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Checkbutton(tab_smiles, text="力场优化", variable=opt_var).grid(row=4, column=1, sticky=tk.W, pady=5)

        def do_smiles_generate():
            smiles = smiles_entry.get().strip()
            if not smiles:
                self.app.helpers.on_log("❌ 请输入 SMILES", 'error')
                return
            prefix = prefix_entry.get().strip() or "mol_from_smiles"
            result = self.app.controller.model.generate_from_smiles(smiles, prefix, generate_3d=gen3d_var.get(), optimize=opt_var.get())
            if result.get("error"):
                self.app.helpers.on_log(f"❌ SMILES 生成失败: {result['error']}", 'error')
            else:
                self.app.helpers.on_log(f"✅ 生成成功: {os.path.basename(result['mol'])}", 'success')
                self.app.controller.scan_files()
                dialog.destroy()

        ttk.Button(tab_smiles, text="🧬 生成分子", command=do_smiles_generate).grid(row=5, column=1, pady=10)

        batch_frame = ttk.LabelFrame(tab_smiles, text="批量 SMILES 导入", padding=8)
        batch_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(10, 0))

        ttk.Label(batch_frame, text="每行一条，支持 \"SMILES 名称\" 空格分隔（名称可选）:").grid(row=0, column=0, sticky=tk.W)
        batch_text = scrolledtext.ScrolledText(batch_frame, height=6, wrap=tk.WORD, font=('Consolas', 9))
        batch_text.grid(row=1, column=0, sticky="nsew", pady=5)
        batch_frame.grid_rowconfigure(1, weight=1)
        batch_frame.grid_columnconfigure(0, weight=1)

        ttk.Button(batch_frame, text="🧬 批量生成", command=lambda: self._run_smiles_batch(batch_text, gen3d_var.get(), opt_var.get(), dialog)).grid(row=2, column=0, pady=5)

        tab_smiles.grid_rowconfigure(6, weight=1)
        tab_smiles.grid_columnconfigure(1, weight=1)

        # 标签3：结构优化
        tab_opt = ttk.Frame(notebook, padding=10)
        notebook.add(tab_opt, text="🔧 结构优化")

        ttk.Label(tab_opt, text="用力场优化分子结构", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))
        ttk.Label(tab_opt, text="选择分子文件:").grid(row=1, column=0, sticky="nw")

        opt_list_frame = ttk.Frame(tab_opt)
        opt_list_frame.grid(row=1, column=1, padx=5, sticky="nsew")
        opt_listbox = tk.Listbox(opt_list_frame, height=8, selectmode=tk.EXTENDED, width=45)
        opt_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        opt_scroll = ttk.Scrollbar(opt_list_frame, orient=tk.VERTICAL, command=opt_listbox.yview)
        opt_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        opt_listbox.configure(yscrollcommand=opt_scroll.set)

        for s in selected:
            opt_listbox.insert(tk.END, s)

        opt_btn_col = ttk.Frame(tab_opt)
        opt_btn_col.grid(row=1, column=2, padx=5, sticky="nw")

        def add_opt_files():
            self._add_unique_to_listbox(opt_listbox, self._ask_ob_files())

        def del_opt_selected():
            self._delete_selected_from_listbox(opt_listbox)

        ttk.Button(opt_btn_col, text="添加...", command=add_opt_files).pack(side=tk.TOP, pady=2, fill=tk.X)
        ttk.Button(opt_btn_col, text="删除选中", command=del_opt_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

        ttk.Label(tab_opt, text="力场:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        forcefield_var = tk.StringVar(value="mmff94")
        ttk.Combobox(tab_opt, textvariable=forcefield_var, values=["mmff94", "uff"], state="readonly", width=15).grid(row=2, column=1, sticky=tk.W, padx=5, pady=(10, 0))

        ttk.Button(tab_opt, text="⚡ 开始优化", command=lambda: self._run_optimize_batch(opt_listbox, forcefield_var.get(), dialog)).grid(row=4, column=1, pady=10)

        # 标签4：描述符
        tab_desc = ttk.Frame(notebook, padding=10)
        notebook.add(tab_desc, text="📊 描述符")

        work_dir = self.app.controller.model.work_dir

        ttk.Label(tab_desc, text="一键计算分子性质（支持批量 + CSV 导出）", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 10))
        ttk.Label(tab_desc, text="分子文件列表:").grid(row=1, column=0, sticky="nw")

        desc_list_frame = ttk.Frame(tab_desc)
        desc_list_frame.grid(row=1, column=1, padx=5, sticky="nsew", columnspan=2)
        desc_listbox = tk.Listbox(desc_list_frame, height=8, selectmode=tk.EXTENDED)
        desc_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        desc_scroll = ttk.Scrollbar(desc_list_frame, orient=tk.VERTICAL, command=desc_listbox.yview)
        desc_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        desc_listbox.configure(yscrollcommand=desc_scroll.set)

        if selected:
            for s in selected:
                desc_listbox.insert(tk.END, s)

        desc_btn_col = ttk.Frame(tab_desc)
        desc_btn_col.grid(row=1, column=3, padx=5, sticky="nw")

        def add_desc_files():
            files = filedialog.askopenfilenames(
                initialdir=str(work_dir),
                filetypes=[("分子文件", "*.mol *.xyz *.sdf *.mol2"), ("全部", "*.*")]
            )
            for f in files:
                name = os.path.basename(f)
                if name not in desc_listbox.get(0, tk.END):
                    desc_listbox.insert(tk.END, name)

        def del_desc_selected():
            for idx in reversed(desc_listbox.curselection()):
                desc_listbox.delete(idx)

        ttk.Button(desc_btn_col, text="添加...", command=add_desc_files).pack(side=tk.TOP, pady=2, fill=tk.X)
        ttk.Button(desc_btn_col, text="删除选中", command=del_desc_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

        desc_result = scrolledtext.ScrolledText(tab_desc, height=10, wrap=tk.WORD, font=('Consolas', 9))
        desc_result.grid(row=2, column=0, columnspan=4, pady=10, sticky="nsew")
        tab_desc.grid_rowconfigure(2, weight=1)
        tab_desc.grid_columnconfigure(1, weight=1)

        def load_from_main():
            names = self.app.helpers.get_selected_filenames()
            for name in names:
                full = Path(work_dir) / name
                if full.exists() and name not in desc_listbox.get(0, tk.END):
                    desc_listbox.insert(tk.END, name)
            self.app.helpers.on_log(f"📄 已从主界面加载 {len(names)} 个文件到列表", 'info')

        def do_descriptors():
            items = list(desc_listbox.get(0, tk.END))
            if not items:
                self.app.helpers.on_log("❌ 列表中没有文件", 'error')
                return
            sel = desc_listbox.curselection()
            if sel:
                fname = items[sel[0]]
            else:
                fname = items[0]

            def task_process(**kwargs):
                path = Path(work_dir) / fname
                desc = self.app.controller.model.calculate_descriptors(str(path))
                def update_ui():
                    self._clear_text(desc_result)
                    if "error" in desc:
                        self._append_text(desc_result, f"❌ 错误: {desc['error']}")
                    else:
                        self._append_text(desc_result, f"📋 {fname} 计算结果:\n")
                        for key, val in desc.items():
                            self._append_text(desc_result, f"{key}: {val}\n", see_end=False)
                self.app.after(0, update_ui)
            self.app.helpers.run_task(task_process)

        def do_batch_csv():
            items = list(desc_listbox.get(0, tk.END))
            if not items:
                self.app.helpers.on_log("❌ 列表中没有文件可批量计算", 'error')
                return
            out_path = filedialog.asksaveasfilename(
                initialdir=str(work_dir),
                initialfile="descriptors.csv",
                filetypes=[("CSV", "*.csv")]
            )
            if not out_path:
                return

            def task_process(**kwargs):
                rows = []
                fieldnames = ["file"]
                for fname in items:
                    path = Path(work_dir) / fname
                    base = os.path.basename(fname)
                    try:
                        desc = self.app.controller.model.calculate_descriptors(str(path))
                        if "error" in desc:
                            row = {"file": base, "error": desc["error"]}
                        else:
                            row = {"file": base, **desc}
                            for k in desc.keys():
                                if k not in fieldnames:
                                    fieldnames.append(k)
                    except Exception as e:
                        row = {"file": base, "error": str(e)}
                    if "error" in row and "error" not in fieldnames:
                        fieldnames.append("error")
                    rows.append(row)
                try:
                    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(rows)
                    def done():
                        self.app.helpers.on_log(f"💾 CSV 已导出: {os.path.basename(out_path)}（共 {len(rows)} 条）", 'success')
                        self.app.controller.scan_files()
                    self.app.after(0, done)
                except Exception as e:
                    def fail():
                        self.app.helpers.on_log(f"❌ CSV 写出失败: {e}", 'error')
                    self.app.after(0, fail)
            self.app.helpers.run_task(task_process)

        desc_btn_row = ttk.Frame(tab_desc)
        desc_btn_row.grid(row=3, column=0, columnspan=4, pady=5)
        ttk.Button(desc_btn_row, text="📄 从主界面选中的分子加载", command=load_from_main).pack(side=tk.LEFT, padx=5)
        ttk.Button(desc_btn_row, text="📊 计算描述符", command=do_descriptors).pack(side=tk.LEFT, padx=5)
        ttk.Button(desc_btn_row, text="💾 批量计算并导出 CSV", command=do_batch_csv).pack(side=tk.LEFT, padx=5)

        # 标签5：分子叠加
        tab_align = ttk.Frame(notebook, padding=10)
        notebook.add(tab_align, text="🔗 分子叠加")

        ttk.Label(tab_align, text="将两个分子按骨架对齐", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))
        ttk.Label(tab_align, text="参考分子（单选）:").grid(row=1, column=0, sticky="nw")

        ref_list_frame = ttk.Frame(tab_align)
        ref_list_frame.grid(row=1, column=1, padx=5, sticky="nsew")
        ref_listbox = tk.Listbox(ref_list_frame, height=3, selectmode=tk.BROWSE, width=45)
        ref_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ref_scroll = ttk.Scrollbar(ref_list_frame, orient=tk.VERTICAL, command=ref_listbox.yview)
        ref_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        ref_listbox.configure(yscrollcommand=ref_scroll.set)

        if selected and len(selected) >= 1:
            ref_listbox.insert(tk.END, selected[0])

        ref_btn_col = ttk.Frame(tab_align)
        ref_btn_col.grid(row=1, column=2, padx=5, sticky="nw")

        def add_ref_files():
            self._add_unique_to_listbox(ref_listbox, self._ask_ob_files())

        def del_ref_selected():
            self._delete_selected_from_listbox(ref_listbox)

        ttk.Button(ref_btn_col, text="添加...", command=add_ref_files).pack(side=tk.TOP, pady=2, fill=tk.X)
        ttk.Button(ref_btn_col, text="删除选中", command=del_ref_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

        ttk.Label(tab_align, text="移动分子（多选）:").grid(row=2, column=0, sticky="nw", pady=(10, 0))

        mob_list_frame = ttk.Frame(tab_align)
        mob_list_frame.grid(row=2, column=1, padx=5, sticky="nsew", pady=(10, 0))
        mob_listbox = tk.Listbox(mob_list_frame, height=8, selectmode=tk.EXTENDED, width=45)
        mob_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        mob_scroll = ttk.Scrollbar(mob_list_frame, orient=tk.VERTICAL, command=mob_listbox.yview)
        mob_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        mob_listbox.configure(yscrollcommand=mob_scroll.set)

        for s in selected[1:] if len(selected) > 1 else []:
            mob_listbox.insert(tk.END, s)

        mob_btn_col = ttk.Frame(tab_align)
        mob_btn_col.grid(row=2, column=2, padx=5, sticky="nw", pady=(10, 0))

        def add_mob_files():
            self._add_unique_to_listbox(mob_listbox, self._ask_ob_files())

        def del_mob_selected():
            self._delete_selected_from_listbox(mob_listbox)

        ttk.Button(mob_btn_col, text="添加...", command=add_mob_files).pack(side=tk.TOP, pady=2, fill=tk.X)
        ttk.Button(mob_btn_col, text="删除选中", command=del_mob_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

        ttk.Button(tab_align, text="🔗 执行叠加", command=lambda: self._run_align_batch(ref_listbox, mob_listbox, dialog)).grid(row=3, column=1, pady=10)

        ttk.Label(dialog, text="所有操作在后台运行，请查看日志", foreground="blue").pack(pady=5)

    def _run_convert_batch(self, listbox, out_fmt, dialog):
        items = list(listbox.get(0, tk.END))
        if not items:
            self.app.helpers.on_log("❌ 列表中没有文件", 'error')
            return
        if not out_fmt:
            self.app.helpers.on_log("❌ 请选择输出格式", 'error')
            return
        dialog.destroy()

        work_dir = self.app.controller.model.work_dir

        def task_process(**kwargs):
            all_ok = True
            for name in items:
                input_path = Path(work_dir) / name
                base = input_path.stem
                output_path = work_dir / f"{base}.{out_fmt}"
                try:
                    res = self.app.controller.model.convert_file(str(input_path), str(output_path), out_fmt)
                    success = res.get("success", False)
                    msg = res.get("message", "")
                    self.app.helpers.on_log(f"{'✅' if success else '❌'} 转换 {name}: {msg}", 'success' if success else 'error')
                    if not success:
                        all_ok = False
                except Exception as e:
                    self.app.helpers.on_log(f"❌ 转换 {name} 异常: {e}", 'error')
                    all_ok = False
            if all_ok:
                self.app.controller.scan_files()
        self.app.helpers.run_task(task_process)

    def _run_optimize_batch(self, listbox, forcefield, dialog):
        items = list(listbox.get(0, tk.END))
        if not items:
            self.app.helpers.on_log("❌ 列表中没有文件", 'error')
            return
        dialog.destroy()

        work_dir = self.app.controller.model.work_dir

        def task_process(**kwargs):
            all_ok = True
            for name in items:
                input_path = Path(work_dir) / name
                base = input_path.stem
                ext = input_path.suffix
                output_path = work_dir / f"{base}_opt{ext}"
                try:
                    res = self.app.controller.model.optimize_geometry(str(input_path), str(output_path), forcefield)
                    success = res.get("success", False)
                    msg = res.get("message", "")
                    self.app.helpers.on_log(f"{'✅' if success else '❌'} 优化 {name}: {msg}", 'success' if success else 'error')
                    if not success:
                        all_ok = False
                except Exception as e:
                    self.app.helpers.on_log(f"❌ 优化 {name} 异常: {e}", 'error')
                    all_ok = False
            if all_ok:
                self.app.controller.scan_files()
        self.app.helpers.run_task(task_process)

    def _run_smiles_batch(self, text_widget, gen3d, opt, dialog):
        raw = text_widget.get(1.0, tk.END).strip()
        if not raw:
            self.app.helpers.on_log("❌ 请输入 SMILES 内容", 'error')
            return
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            self.app.helpers.on_log("❌ 没有有效 SMILES 行", 'error')
            return
        dialog.destroy()

        def task_process(**kwargs):
            all_ok = True
            for idx, line in enumerate(lines):
                parts = line.split(None, 1)
                smiles = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else f"smi_idx_{idx+1:03d}"
                if not smiles:
                    continue
                try:
                    res = self.app.controller.model.generate_from_smiles(smiles, name, generate_3d=gen3d, optimize=opt)
                    if res.get("error"):
                        self.app.helpers.on_log(f"❌ SMILES 生成失败 {name}: {res['error']}", 'error')
                        all_ok = False
                    else:
                        self.app.helpers.on_log(f"✅ 生成成功 {name}: {os.path.basename(res['mol'])}", 'success')
                except Exception as e:
                    self.app.helpers.on_log(f"❌ SMILES 生成异常 {name}: {e}", 'error')
                    all_ok = False
            if all_ok:
                self.app.controller.scan_files()
        self.app.helpers.run_task(task_process)

    def _run_align_batch(self, ref_listbox, mob_listbox, dialog):
        ref_sel = ref_listbox.curselection()
        if not ref_sel:
            self.app.helpers.on_log("❌ 请选择参考分子", 'error')
            return
        ref_name = ref_listbox.get(ref_sel[0])
        mob_items = list(mob_listbox.get(0, tk.END))
        if not mob_items:
            self.app.helpers.on_log("❌ 移动分子列表为空", 'error')
            return
        dialog.destroy()

        work_dir = self.app.controller.model.work_dir
        ref_path = Path(work_dir) / ref_name
        ref_stem = ref_path.stem

        def task_process(**kwargs):
            all_ok = True
            for mob_name in mob_items:
                mob_path = Path(work_dir) / mob_name
                mob_stem = mob_path.stem
                out_path = work_dir / f"{mob_stem}_aligned_to_{ref_stem}.xyz"
                try:
                    res = self.app.controller.model.align_molecules(str(ref_path), str(mob_path), str(out_path))
                    success = res.get("success", False)
                    msg = res.get("message", "")
                    self.app.helpers.on_log(f"{'✅' if success else '❌'} 叠加 {mob_name}: {msg}", 'success' if success else 'error')
                    if not success:
                        all_ok = False
                except Exception as e:
                    self.app.helpers.on_log(f"❌ 叠加 {mob_name} 异常: {e}", 'error')
                    all_ok = False
            if all_ok:
                self.app.controller.scan_files()
        self.app.helpers.run_task(task_process)

    def preview_2d_structure(self):
        selected = self.app.helpers.get_selected_filenames()
        if not selected:
            self.app.helpers.on_log("⚠️ 请先选择一个文件", 'warning')
            return
        fname = selected[0]
        ext = Path(fname).suffix.lower()
        mol_exts = ('.mol', '.xyz', '.sdf', '.mol2')
        if ext not in mol_exts:
            self.app.helpers.on_log(f"⚠️ 不支持的文件类型 {ext}，仅支持 {', '.join(mol_exts)}", 'warning')
            messagebox.showwarning("不支持", f"仅支持以下分子文件类型:\n{', '.join(mol_exts)}")
            return

        def task_process(**kwargs):
            res = self.app.controller.model.render_png_2d(fname)
            success = res.get("success", False)
            msg = res.get("message", "")
            png_path = res.get("output_path")

            def done():
                if not success or not png_path or not os.path.exists(png_path):
                    self.app.helpers.on_log(f"❌ 2D 预览失败: {msg}", 'error')
                    messagebox.showerror("预览失败", f"2D 结构渲染失败:\n{msg}")
                    return
                self.app.helpers.on_log(f"✅ 2D 预览: {msg}", 'success')
                self._show_png_preview(png_path, fname)
            self.app.after(0, done)
        self.app.helpers.run_task(task_process)

    def _show_png_preview(self, png_path: str, fname: str):
        try:
            from PIL import Image, ImageTk
            has_pil = True
        except ImportError:
            has_pil = False

        dialog = tk.Toplevel(self.app)
        dialog.title(f"🖼️ 2D 结构预览 - {os.path.basename(fname)}")
        dialog.geometry("900x700")
        dialog.transient(self.app)

        try:
            if has_pil:
                with Image.open(png_path) as img:
                    img.load()
                    img.thumbnail((850, 620), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img.copy())
                label = tk.Label(dialog, image=photo)
                label.image = photo
                label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            else:
                try:
                    photo = tk.PhotoImage(file=png_path)
                    label = tk.Label(dialog, image=photo)
                    label.image = photo
                    label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
                except Exception as e_tk:
                    dialog.destroy()
                    if messagebox.askyesno(
                        "无法显示图片",
                        f"PIL/Pillow 未安装，且 tk.PhotoImage 无法加载 PNG:\n{e_tk}\n\n是否用系统默认程序打开图片？"
                    ):
                        try:
                            self._safe_open_file(png_path)
                        except Exception as e_open:
                            messagebox.showerror("打开失败", f"无法打开图片:\n{e_open}")
                    else:
                        messagebox.showinfo("提示", "请安装 Pillow:\n  pip install Pillow")
                    return
        except Exception as e:
            dialog.destroy()
            messagebox.showerror("预览异常", f"显示图片时出错:\n{e}")
            return

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📂 打开文件位置", command=lambda: self._open_png_folder(png_path)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _open_png_folder(self, png_path: str):
        try:
            folder = os.path.dirname(os.path.abspath(png_path))
            self._safe_open_file(folder)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件夹:\n{e}")

    # ============ OB-PATH：OpenBabel 手动路径设置（问题三）============
    def show_obabel_path_dialog(self, parent=None, on_saved_callback=None) -> None:
        """
        打开「OpenBabel 路径设置」对话框：
          - 显示当前使用的路径（来自自动解析 / 手动配置）
          - 提供「浏览」选择 obabel(.exe) 可执行文件
          - 保存按钮：写入 config["obabel_path"] 并生效到 openbabel_utils
          - 自动测试按钮：调用 check_openbabel() 显示结果
        """
        app = self.app
        parent = parent or app
        dialog = tk.Toplevel(parent)
        dialog.title("OpenBabel 路径设置")
        dialog.transient(parent)
        dialog.grab_set()
        try:
            dialog.geometry("680x300")
        except Exception:
            pass
        try:
            dialog.configure(bg="#EEF3FF")
        except Exception:
            pass

        # —— 字体（跟随用户 config.font_size，避免对话框字体小）——
        F = getattr(app, "_fonts", {})
        BASE = F.get("BASE",      ("Microsoft YaHei", 12))
        BOLD = F.get("BOLD",      ("Microsoft YaHei", 12, "bold"))
        SMALL = F.get("SMALL",    ("Microsoft YaHei", 11))
        BTN  = F.get("BTN",       ("Microsoft YaHei", 12, "bold"))

        main = tk.Frame(dialog, bg="#EEF3FF")
        main.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        tk.Label(main, text="🧭  OpenBabel 可执行文件路径设置",
                 bg="#EEF3FF", fg="#1A2142",
                 font=F.get("H1", ("Microsoft YaHei", 14, "bold"))
                 ).pack(anchor="w", pady=(0, 10))

        tip = ("如果自动找不到 obabel 命令行，可在这里手动选择它的可执行文件\n"
               "  Windows：obabel.exe（一般在 C:\\Program Files\\OpenBabel-3.1.1\\ 或 ~/Anaconda3/Library/bin/）\n"
               "  Linux/macOS：一般在 /usr/bin/obabel、~/anaconda3/bin/obabel")
        tk.Label(main, text=tip, bg="#EEF3FF", fg="#6B7599",
                 font=SMALL, justify="left").pack(anchor="w", pady=(0, 12))

        # 当前路径
        row_cur = tk.Frame(main, bg="#EEF3FF")
        row_cur.pack(fill=tk.X, pady=(0, 8))
        tk.Label(row_cur, text="当前解析到的路径：", bg="#EEF3FF", fg="#1A2142",
                 font=BOLD).pack(side=tk.LEFT)
        cur_var = tk.StringVar(value="(请先点「重新检测」)")
        cur_label = tk.Label(row_cur, textvariable=cur_var, bg="#FFFFFF", fg="#3B6EFF",
                             font=SMALL, relief=tk.SUNKEN, padx=8, pady=4, justify="left")
        cur_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        # 手动输入行
        row_path = tk.Frame(main, bg="#EEF3FF")
        row_path.pack(fill=tk.X, pady=(6, 8))
        tk.Label(row_path, text="手动指定路径：", bg="#EEF3FF", fg="#1A2142",
                 font=BOLD, width=14, anchor="w").pack(side=tk.LEFT)
        path_var = tk.StringVar(value=str((getattr(app, "config_data", {}) or {}).get("obabel_path", "") or ""))
        entry = ttk.Entry(row_path, textvariable=path_var, font=BASE)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6))

        def _browse():
            filetypes = [("OpenBabel 可执行文件", "*.exe"), ("所有文件", "*.*")] \
                if sys.platform == "win32" else [("所有文件", "*.*")]
            initdir = str(Path(path_var.get()).parent) if path_var.get() and os.path.exists(path_var.get()) else os.path.expanduser("~")
            selected = filedialog.askopenfilename(
                parent=dialog,
                title="选择 obabel 可执行文件",
                initialdir=initdir,
                filetypes=filetypes,
            )
            if selected:
                path_var.set(selected)

        ttk.Button(row_path, text="浏览…", command=_browse,
                   style="Aurora.TButton").pack(side=tk.LEFT, padx=(0, 2))

        def _auto():
            """清除手动路径并重新检测"""
            path_var.set("")
            _detect()

        ttk.Button(row_path, text="使用自动查找", command=_auto,
                   style="Aurora.TButton").pack(side=tk.LEFT, padx=2)

        # 结果 / 建议区
        result_var = tk.StringVar(value="")
        res_label = tk.Label(main, textvariable=result_var, bg="#EEF3FF", fg="#1A2142",
                             font=BASE, justify="left", anchor="w")
        res_label.pack(fill=tk.X, pady=(4, 8))

        def _detect():
            # 先把当前输入的路径写入内存（不先写配置，允许取消）
            v = path_var.get().strip()
            if v:
                ob_utils.set_manual_obabel_path(v)
            else:
                ob_utils.set_manual_obabel_path(None)
            ok, msg, det = ob_utils.check_openbabel()
            cur_var.set(str(det.get("resolved_cli_path") or "(未解析到)")
                        + ("   （手动路径）" if det.get("manual_path_used") else "   （自动）"))
            status = ("✅ " + msg) if ok else ("❌ " + msg)
            result_var.set(status)
            return ok, msg, det

        def _test():
            ok, msg, det = _detect()
            if ok:
                messagebox.showinfo("OpenBabel 检测通过", f"{msg}\n\n诊断：\n" + "\n  • ".join([""] + (det.get("diagnosis") or ["未发现问题"])))
            else:
                lines = [msg]
                if det.get("diagnosis"):
                    lines.append("")
                    lines.append("诊断建议：")
                    lines.extend("  • " + d for d in det["diagnosis"])
                lines.append("")
                lines.append(det.get("install_guide", ""))
                messagebox.showwarning("OpenBabel 不可用", "\n".join(lines))

        def _save():
            v = path_var.get().strip()
            try:
                cfg = app.config_data if hasattr(app, "config_data") else {}
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["obabel_path"] = v
                app.config_data = cfg
                try:
                    from config import save_config
                    save_config(cfg)
                except Exception as _se:
                    logger.warning("保存 obabel_path 到配置失败：%s", _se)
            except Exception as _e:
                logger.warning("保存 obabel_path 到 config 失败：%s", _e)
            # 写内存
            ob_utils.set_manual_obabel_path(v)
            # 重新检测并同步状态栏指示灯
            try:
                fn = getattr(app.helpers, "check_environment", None)
                if callable(fn):
                    fn(announce_missing=False)
            except Exception:
                pass
            result_var.set("✅ 已保存！下次启动仍继续使用该路径。")
            messagebox.showinfo("已保存", "OpenBabel 路径已写入配置并生效，可点「测试」验证。")
            if callable(on_saved_callback):
                try:
                    on_saved_callback()
                except Exception:
                    pass

        btns = tk.Frame(main, bg="#EEF3FF")
        btns.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btns, text="🔍 重新检测", command=_detect,
                   style="Aurora.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="🧪 测试可用性", command=_test,
                   style="Aurora.Primary.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="💾 保存到配置", command=_save,
                   style="Aurora.BigAccent.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="关闭", command=dialog.destroy,
                   style="Aurora.TButton").pack(side=tk.RIGHT, padx=4)

        # 打开对话框立即先做一次检测
        try:
            dialog.after(50, _detect)
        except Exception:
            pass

    # ============ ENV：环境诊断对话框（问题三 + 友好上手）============
    def show_environment_dialog(self, parent=None, *,
                                ob_details: dict | None = None,
                                psi4_details: dict | None = None) -> None:
        """
        综合环境诊断：
          - 显示 OpenBabel 状态（pybel / CLI 双接口）+ 安装指引
          - 显示 PSI4 是否可用
          - 提供一键「手动选择 obabel 路径」按钮（复用 show_obabel_path_dialog）
          - 不阻塞后台线程，用户可继续使用基础功能
        """
        app = self.app
        parent = parent or app
        dialog = tk.Toplevel(parent)
        dialog.title("环境诊断 · 分子管理器")
        dialog.transient(parent)
        try:
            dialog.geometry("880x620")
        except Exception:
            pass
        try:
            dialog.configure(bg="#EEF3FF")
        except Exception:
            pass

        F = getattr(app, "_fonts", {})
        BASE  = F.get("BASE",  ("Microsoft YaHei", 12))
        BOLD  = F.get("BOLD",  ("Microsoft YaHei", 12, "bold"))
        SMALL = F.get("SMALL", ("Microsoft YaHei", 11))
        H1    = F.get("H1",    ("Microsoft YaHei", 14, "bold"))
        BTN   = F.get("BTN",   ("Microsoft YaHei", 12, "bold"))

        main = tk.Frame(dialog, bg="#EEF3FF")
        main.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(main, text="🧪  环境诊断（依赖与建议）",
                 bg="#EEF3FF", fg="#1A2142",
                 font=H1).pack(anchor="w", pady=(0, 6))
        tk.Label(main, text="如果某项为红色，可直接点击对应「修复」按钮尝试解决。若仍不通过请参考下方安装指引。",
                 bg="#EEF3FF", fg="#6B7599",
                 font=SMALL, justify="left").pack(anchor="w", pady=(0, 14))

        # —— OB 区 ——
        ob_card = tk.Frame(main, bg="#FFFFFF", bd=0,
                           highlightbackground="#D7E2FF", highlightthickness=1)
        ob_card.pack(fill=tk.X, pady=(0, 10))
        hdr = tk.Frame(ob_card, bg="#FFFFFF")
        hdr.pack(fill=tk.X, padx=14, pady=(12, 4))
        tk.Label(hdr, text="OpenBabel 状态", bg="#FFFFFF", fg="#1A2142",
                 font=BOLD).pack(side=tk.LEFT)
        ob_status_var = tk.StringVar(value="检测中…")
        ob_status_lbl = tk.Label(hdr, textvariable=ob_status_var, bg="#FFFFFF", fg="#1A2142",
                                 font=BOLD, anchor="e")
        ob_status_lbl.pack(side=tk.RIGHT)
        ob_text_var = tk.StringVar(value="")
        tk.Label(ob_card, textvariable=ob_text_var, bg="#FFFFFF", fg="#1A2142",
                 font=BASE, justify="left", anchor="w",
                 wraplength=820).pack(fill=tk.X, padx=14, pady=(2, 6))
        ob_diag_text = scrolledtext.ScrolledText(
            ob_card, height=7, font=F.get("LOG", ("Consolas", 11)),
            bg="#F8FAFF", fg="#1A2142", wrap=tk.WORD, bd=1, relief=tk.SOLID,
            highlightbackground="#D7E2FF", highlightthickness=1,
        )
        ob_diag_text.pack(fill=tk.X, padx=14, pady=(2, 10))
        ob_btn_row = tk.Frame(ob_card, bg="#FFFFFF")
        ob_btn_row.pack(fill=tk.X, padx=14, pady=(0, 14))

        # —— PSI4 区 ——
        psi_card = tk.Frame(main, bg="#FFFFFF", bd=0,
                            highlightbackground="#D7E2FF", highlightthickness=1)
        psi_card.pack(fill=tk.X, pady=(0, 10))
        hdr2 = tk.Frame(psi_card, bg="#FFFFFF")
        hdr2.pack(fill=tk.X, padx=14, pady=(12, 4))
        tk.Label(hdr2, text="PSI4 状态", bg="#FFFFFF", fg="#1A2142",
                 font=BOLD).pack(side=tk.LEFT)
        psi_status_var = tk.StringVar(value="检测中…")
        tk.Label(hdr2, textvariable=psi_status_var, bg="#FFFFFF", fg="#1A2142",
                 font=BOLD, anchor="e").pack(side=tk.RIGHT)
        psi_text_var = tk.StringVar(value="")
        tk.Label(psi_card, textvariable=psi_text_var, bg="#FFFFFF", fg="#1A2142",
                 font=BASE, justify="left", anchor="w",
                 wraplength=820).pack(fill=tk.X, padx=14, pady=(2, 14))

        # —— 底部：安装指引 ——
        guide_card = tk.LabelFrame(main, text="  📘 OpenBabel 安装指引 / 故障排查  ",
                                   bg="#FFFFFF", fg="#1A2142", font=BOLD,
                                   relief=tk.GROOVE, bd=2)
        guide_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        guide_text = scrolledtext.ScrolledText(
            guide_card, height=12, font=F.get("LOG", ("Consolas", 11)),
            bg="#F8FAFF", fg="#1A2142", wrap=tk.WORD, bd=0,
        )
        guide_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        guide_text.configure(state="normal")
        guide_text.insert(tk.END, ob_utils.OB_INSTALL_GUIDE)
        guide_text.configure(state="disabled")

        # —— 按钮区 ——
        btns = tk.Frame(main, bg="#EEF3FF")
        btns.pack(fill=tk.X)
        def _rerun_all():
            _fill_ob()
            _fill_psi4()

        def _open_manual_path():
            try:
                self.show_obabel_path_dialog(parent=dialog, on_saved_callback=_fill_ob)
            except Exception as _e:
                messagebox.showerror("打开失败", f"无法打开 OpenBabel 路径设置对话框：{_e}")

        ttk.Button(btns, text="🔁 重新检测", command=_rerun_all,
                   style="Aurora.Primary.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="🧭 手动选择 obabel 路径…", command=_open_manual_path,
                   style="Aurora.BigAccent.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="关闭", command=dialog.destroy,
                   style="Aurora.TButton").pack(side=tk.RIGHT, padx=4)

        # —— 数据填充函数 ——
        def _fill_ob():
            try:
                ob_ok, ob_msg, det = ob_utils.check_openbabel()
                if ob_details is None:
                    # 用于外部 caller 想要详情的情况（本函数内部无需再赋值给外层引用）
                    pass
                ob_status_var.set(("✅ 可用" if ob_ok else "❌ 不可用"))
                try:
                    ob_status_lbl.configure(fg=("#0EA288" if ob_ok else "#E5484D"))
                except Exception:
                    pass
                parts = [ob_msg]
                if det.get("resolved_cli_path"):
                    parts.append(f"  CLI 路径：{det['resolved_cli_path']}"
                                 + ("  （手动指定）" if det.get("manual_path_used") else ""))
                if det.get("pybel_version"):
                    parts.append(f"  pybel 版本：{det['pybel_version']}")
                if det.get("cli_version"):
                    parts.append(f"  CLI 版本：{det['cli_version']}")
                if det.get("supported_format_count"):
                    parts.append(f"  支持格式数：约 {det['supported_format_count']} 种")
                ob_text_var.set("\n".join(parts))

                diags: list[str] = []
                for w in (det.get("warnings") or []):
                    diags.append(f"[WARN]  {w}")
                for d in (det.get("diagnosis") or []):
                    diags.append(f"[TIP]   {d}")
                if not diags:
                    diags.append("[OK]   未发现异常。")
                try:
                    ob_diag_text.configure(state="normal")
                    ob_diag_text.delete("1.0", tk.END)
                    ob_diag_text.insert(tk.END, "\n".join(diags))
                finally:
                    try:
                        ob_diag_text.configure(state="disabled")
                    except Exception:
                        pass
            except Exception as _oe:
                ob_status_var.set("⚠️ 检测失败")
                ob_text_var.set(str(_oe))

        def _fill_psi4():
            try:
                import psi4  # type: ignore
                v = getattr(psi4, "__version__", None)
                psi_status_var.set("✅ 可用" if v else "✅ 可导入")
                try:
                    psi_status_var.set  # noop 兼容
                except Exception:
                    pass
                psi_text_var.set(
                    f"Python 包 psi4 已导入（版本 {v or '未声明'}）。\n"
                    "如果运行任务失败，一般是内存不足、方法/基组不兼容或任务超时，可在右侧「🔬 计算与动画」页面的任务输出日志查看详情。"
                )
            except Exception as _pe:
                psi_status_var.set("⚠️ 未导入")
                psi_text_var.set(
                    "未检测到 Python 包 psi4（不影响文件整理/OpenBabel 工具）。\n"
                    "如需使用量化计算/刚性扫描/动画等能力，建议执行：\n"
                    "    conda install -c conda-forge psi4 resp gcp-correction dftd4"
                    f"\n详细错误：{_pe}"
                )
            # 同步外部引用（如果调用方给了）
            if isinstance(psi4_details, dict):
                try:
                    psi4_details.clear()
                    psi4_details["ok"] = (psi_status_var.get().startswith("✅"))
                    psi4_details["message"] = psi_text_var.get()
                except Exception:
                    pass

        # 对话框打开后先跑一次检测
        try:
            dialog.after(80, _fill_ob)
            dialog.after(140, _fill_psi4)
        except Exception:
            _fill_ob()
            _fill_psi4()

    # ============ FONT：字体大小设置（滑块 + 预览 + 保存）============
    def show_font_size_dialog(self, parent=None) -> None:
        """
        字体大小对话框：
          - 范围 8 ~ 22pt（滑块），右侧数字 Entry 亦可直接输
          - 实时预览 Label
          - 保存按钮：写入 config["font_size"] 并持久化，然后问用户是否立即重启生效
            （已创建的控件不会自动重绘；未重启前会尽力 update 几个常见样式）
        """
        app = self.app
        parent = parent or app
        dialog = tk.Toplevel(parent)
        dialog.title("字体大小设置")
        dialog.transient(parent)
        dialog.grab_set()
        try:
            dialog.geometry("680x360")
        except Exception:
            pass
        try:
            dialog.configure(bg="#EEF3FF")
        except Exception:
            pass

        F = getattr(app, "_fonts", {})
        BASE  = F.get("BASE",  ("Microsoft YaHei", 12))
        BOLD  = F.get("BOLD",  ("Microsoft YaHei", 12, "bold"))
        SMALL = F.get("SMALL", ("Microsoft YaHei", 11))
        H1    = F.get("H1",    ("Microsoft YaHei", 14, "bold"))

        # —— 读取当前值 & 配置 ——
        try:
            cfg = getattr(app, "config_data", None)
            if not isinstance(cfg, dict):
                cfg = {}
            cur = int(cfg.get("font_size", 14) or 14)
        except Exception:
            cur = 14
        if cur < 8:
            cur = 8
        if cur > 24:
            cur = 24

        main = tk.Frame(dialog, bg="#EEF3FF")
        main.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)

        tk.Label(main, text="🔤  界面字体大小",
                 bg="#EEF3FF", fg="#1A2142",
                 font=H1).pack(anchor="w", pady=(0, 2))
        tk.Label(main,
                 text="调整后会保存到配置文件。由于 Tkinter 已创建控件的字体不会被全局 option_add 自动刷新，\n"
                      "保存后建议按提示「立即重启」，即可让全部界面完整使用新字号。",
                 bg="#EEF3FF", fg="#6B7599", font=SMALL, justify="left"
                 ).pack(anchor="w", pady=(0, 14))

        # —— 滑块 + 数字显示 ——
        row = tk.Frame(main, bg="#EEF3FF")
        row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(row, text="字号（pt）：", bg="#EEF3FF", fg="#1A2142",
                 font=BOLD).pack(side=tk.LEFT)
        val_var = tk.IntVar(value=cur)
        # 数字输入框（可直接输入）
        spin = tk.Spinbox(row, from_=8, to=24, textvariable=val_var, width=4,
                          font=BOLD, justify="center", bd=2, relief=tk.SOLID,
                          bg="#FFFFFF", fg="#1A2142", buttonbackground="#DEE8FF")
        spin.pack(side=tk.LEFT, padx=(6, 0))

        # —— 滑块 ——
        slider_row = tk.Frame(main, bg="#EEF3FF")
        slider_row.pack(fill=tk.X, pady=(4, 10))
        scale = tk.Scale(slider_row, from_=8, to=24, orient=tk.HORIZONTAL,
                         variable=val_var, showvalue=False,
                         font=SMALL, bg="#EEF3FF", fg="#1A2142",
                         troughcolor="#D7E2FF", activebackground="#3B6EFF",
                         sliderlength=26, sliderrelief=tk.RAISED, borderwidth=1,
                         highlightthickness=0)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(slider_row, textvariable=val_var, bg="#EEF3FF", fg="#3B6EFF",
                 font=BOLD, width=3, anchor="center").pack(side=tk.LEFT, padx=(8, 0))

        # —— 预览卡片 ——
        prev = tk.LabelFrame(main, text="  🧿 实时预览（仅预览 Label/Button 字体）  ",
                             bg="#FFFFFF", fg="#1A2142", font=BOLD,
                             relief=tk.GROOVE, bd=2)
        prev.pack(fill=tk.BOTH, expand=True, pady=(4, 10))
        prev_inner = tk.Frame(prev, bg="#FFFFFF")
        prev_inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        preview_base_label = tk.Label(prev_inner,
                                      text="普通文字 Label：ABC 中文 English 123 （预览字号会随滑块实时变化）",
                                      bg="#FFFFFF", fg="#1A2142")
        preview_base_label.pack(anchor="w", pady=(0, 4))
        preview_bold_label = tk.Label(prev_inner,
                                      text="加粗文字 Label：粗体中文 / Bold English / 标题风格",
                                      bg="#FFFFFF", fg="#3B6EFF")
        preview_bold_label.pack(anchor="w", pady=(0, 6))
        preview_btn = tk.Button(prev_inner, text="示例按钮 Button", relief=tk.RAISED, bd=1,
                                bg="#DEE8FF", fg="#1A2142", activebackground="#C8D9FF",
                                cursor="hand2")
        preview_btn.pack(anchor="w", pady=(0, 4))
        preview_code = tk.Label(prev_inner,
                                text='Consolas 日志字体预览：log.info("hello world")  12345',
                                bg="#F8FAFF", fg="#1A2142", relief=tk.SUNKEN, bd=1,
                                justify="left", anchor="w", padx=8, pady=4)
        preview_code.pack(anchor="w", fill=tk.X, pady=(2, 0))

        def _apply_preview(*_a):
            try:
                pt = int(val_var.get())
            except Exception:
                return
            if pt < 8:
                pt = 8
            if pt > 24:
                pt = 24
            # 中文用 Microsoft YaHei，代码用 Consolas（不缩放过度）
            cn_face = "Microsoft YaHei"
            en_face = "Consolas"
            try:
                preview_base_label.configure(font=(cn_face, pt))
            except Exception:
                pass
            try:
                preview_bold_label.configure(font=(cn_face, pt, "bold"))
            except Exception:
                pass
            try:
                log_pt = max(9, pt - 1)
                preview_btn.configure(font=(cn_face, pt, "bold"))
                preview_code.configure(font=(en_face, log_pt))
            except Exception:
                pass

        _apply_preview()
        val_var.trace_add("write", lambda *_args: _apply_preview())

        # —— 保存按钮 ——
        btns = tk.Frame(main, bg="#EEF3FF")
        btns.pack(fill=tk.X, pady=(8, 0))

        def _save_and_maybe_restart():
            try:
                pt = int(val_var.get())
            except Exception:
                messagebox.showerror("错误", "请填写合法的整数字号（8~24）", parent=dialog)
                return
            if pt < 8 or pt > 24:
                messagebox.showwarning("范围超限", "字号建议在 8 到 24 之间，已自动修正。", parent=dialog)
                pt = max(8, min(24, pt))
                val_var.set(pt)
            # —— 写内存 ——
            try:
                cfg = getattr(app, "config_data", None)
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["font_size"] = pt
                app.config_data = cfg
            except Exception as _e1:
                logger.warning("写 font_size 到内存 config_data 失败：%s", _e1)
            # —— 写磁盘 ——
            try:
                from config import save_config
                save_config(app.config_data)
            except Exception as _e2:
                messagebox.showerror("保存失败", f"写入配置文件失败：\n{_e2}", parent=dialog)
                return
            # —— 立刻尽力刷新已有样式（对 ttk.Style 和 Text 等做一次 patch，不保证全部）——
            try:
                from ui_builder import resolve_font_specs
                resolve_font_specs(app, force_pt=pt)
            except Exception as _e3:
                logger.debug("resolve_font_specs 热更新失败：%s", _e3)
            try:
                new_f = getattr(app, "_fonts", {})
                new_base = new_f.get("BASE", ("Microsoft YaHei", pt))
                app.option_add("*Font", new_base)
            except Exception:
                pass
            # —— 问用户是否立即重启 ——
            if messagebox.askyesno(
                "已保存 · 建议重启",
                f"字号已成功保存为 {pt} pt。\n\n"
                "新字号会在「下次启动」时完整生效。是否立即重启本程序以立即看到完整效果？\n\n"
                "（未重启前：部分已创建控件可能仍沿用旧字号，属于 Tkinter 的正常现象。）",
                parent=dialog,
            ):
                try:
                    dialog.destroy()
                except Exception:
                    pass
                try:
                    self.app.after(120, self._restart_app)
                except Exception as _rest_e:
                    messagebox.showinfo(
                        "重启失败",
                        f"自动重启失败，请手动关闭后重新打开：{_rest_e}",
                        parent=parent,
                    )
            else:
                messagebox.showinfo(
                    "已保存",
                    f"字号已保存为 {pt} pt。下次启动即可完整生效。",
                    parent=dialog,
                )
                try:
                    dialog.destroy()
                except Exception:
                    pass

        def _reset_default():
            val_var.set(14)

        ttk.Button(btns, text="↺ 恢复默认 14pt", command=_reset_default,
                   style="Aurora.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="取消", command=dialog.destroy,
                   style="Aurora.TButton").pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="💾 保存并应用（建议重启）",
                   command=_save_and_maybe_restart,
                   style="Aurora.BigAccent.TButton").pack(side=tk.RIGHT, padx=4)

    def _restart_app(self) -> None:
        """
        用当前 Python 解释器重跑当前主脚本。
        仅在用户确认「立即重启」时调用。
        """
        try:
            import subprocess as _sp
            argv0 = sys.argv[0] if sys.argv else os.path.abspath("main.py")
            # 切换到当前 exe / py 文件所在目录（与首次启动一致）
            try:
                work_d = os.path.dirname(os.path.abspath(argv0)) or os.getcwd()
            except Exception:
                work_d = os.getcwd()
            _sp.Popen([sys.executable, argv0, *sys.argv[1:]],
                      cwd=work_d, close_fds=True)
        except Exception as _e:
            from tkinter import messagebox as _mb
            _mb.showerror("自动重启失败", f"请手动关闭后重新打开：\n{_e}")
            return
        # 退出当前进程：先优雅关窗口
        try:
            try:
                self.app.on_close()
            except Exception:
                pass
            try:
                self.app.destroy()
            except Exception:
                pass
        finally:
            try:
                os._exit(0)
            except Exception:
                sys.exit(0)



    # ============ O3：分子式 / 元素分析弹窗 ============
    def show_formula_dialog(self):
        sel = self.app.helpers.get_selected_filenames()
        if not sel:
            self.app.helpers.on_log("⚠️ 请先选择一个分子文件", "warning")
            return

        def _run(**_kw):
            import openbabel_utils as obu
            from pathlib import Path
            work = self.app.work_dir_var.get().strip()
            fp = str(Path(work) / sel[0]) if work and not os.path.isabs(sel[0]) else sel[0]
            return obu.analyze_formula(fp), os.path.basename(fp)

        def _on_done(r):
            try:
                (res, basename) = r
            except Exception:
                self.show_friendly(r or "分析失败"); return
            if not res.get("success"):
                self.show_friendly(res.get("message", "元素分析失败")); return
            dlg = tk.Toplevel(self.app)
            dlg.title(f"🧪 分子式 & 元素分析 — {basename}")
            dlg.geometry("620x520")
            dlg.transient(self.app)
            pad = ttk.Frame(dlg, padding=16); pad.pack(fill=tk.BOTH, expand=True)

            f = res.get("hill_formula") or res.get("formula") or ""
            mw = res.get("molecular_weight") or 0.0
            exact = res.get("exact_mass") or 0.0
            n_at = res.get("atoms_count") or 0
            ttk.Label(pad, text=f"分子式 (Hill 系统)：", font=('Microsoft YaHei', 10, "bold")).grid(row=0, column=0, sticky="w")
            ttk.Label(pad, text=f, font=('Microsoft YaHei', 14, "bold"), foreground="#1976d2").grid(row=0, column=1, sticky="w", padx=(6, 0))
            ttk.Label(pad, text=f"平均分子量：").grid(row=1, column=0, sticky="w", pady=(6, 0))
            ttk.Label(pad, text=f"{mw:.4f}  g/mol").grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
            ttk.Label(pad, text=f"精确分子量：").grid(row=2, column=0, sticky="w", pady=(4, 0))
            ttk.Label(pad, text=f"{exact:.6f}  g/mol").grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
            ttk.Label(pad, text=f"原子总数：").grid(row=3, column=0, sticky="w", pady=(4, 0))
            ttk.Label(pad, text=f"{n_at}  个").grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(4, 0))

            ttk.Separator(pad, orient=tk.HORIZONTAL).grid(row=4, column=0, columnspan=2, sticky="ew", pady=10)
            ttk.Label(pad, text="元素组成（质量百分比 %）：", font=('Microsoft YaHei', 10, "bold")).grid(row=5, column=0, columnspan=2, sticky="w")

            cols = ("元素", "个数", "质量百分比")
            tv = ttk.Treeview(pad, columns=cols, show="headings", height=8)
            for c, w in zip(cols, (80, 80, 200)):
                tv.heading(c, text=c); tv.column(c, width=w, anchor="center")
            tv.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=8)
            pad.grid_rowconfigure(6, weight=1); pad.grid_columnconfigure(1, weight=1)

            els = res.get("elements") or {}
            pct = res.get("elements_pct") or {}
            total = sum(els.values())
            for sym in sorted(els.keys(), key=lambda s: (-els[s], s)):
                cnt = els[sym]
                p = pct.get(sym, round(cnt / max(1, total) * 100, 2))
                bar_len = int(p * 1.8)
                bar = "█" * bar_len
                tv.insert("", tk.END, values=(sym, cnt, f"{p:.2f}%  {bar}"))

            btns = ttk.Frame(pad); btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(4, 0))

            def _copy_tsv():
                lines = ["元素\t个数\t质量百分比%"]
                for sym in sorted(els.keys(), key=lambda s: (-els[s], s)):
                    lines.append(f"{sym}\t{els[sym]}\t{pct.get(sym, 0.0)}")
                try:
                    self.app.clipboard_clear(); self.app.clipboard_append("\n".join(lines))
                    messagebox.showinfo("已复制", "元素表已复制为 TSV，直接粘贴到 Excel。", parent=dlg)
                except Exception as e:
                    self.show_friendly(e)
            ttk.Button(btns, text="📋 复制表格(TSV)", command=_copy_tsv).pack(side=tk.LEFT, padx=5)
            ttk.Button(btns, text="关闭", command=dlg.destroy).pack(side=tk.LEFT, padx=5)

        from task_manager import TaskManager
        TaskManager(self.app, self.controller).run_async(_run, on_done=_on_done)

    # ============ O6：导出几何参数 CSV ============
    def export_geometry_csv(self):
        sel = self.app.helpers.get_selected_filenames()
        if not sel:
            self.app.helpers.on_log("⚠️ 请先选择一个分子文件", "warning"); return
        work = self.app.work_dir_var.get().strip()
        from pathlib import Path
        src = str(Path(work) / sel[0]) if work and not os.path.isabs(sel[0]) else sel[0]
        base = Path(src).stem
        default_out = str(Path(src).parent / f"{base}_geometry.csv")
        from tkinter import filedialog
        target = filedialog.asksaveasfilename(
            title="导出几何参数为 CSV",
            defaultextension=".csv",
            initialdir=str(Path(src).parent),
            initialfile=f"{base}_geometry.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            parent=self.app,
        )
        if not target:
            return
        import openbabel_utils as obu
        r = obu.export_geometry_csv(src, target)
        if r.get("success"):
            self.app.helpers.on_log(
                f"✅ 几何参数导出完成：{r['n_atoms']} 原子, {r['n_bonds']} 键, {r['n_angles']} 角 → {target}",
                "success",
            )
            if messagebox.askyesno("导出成功",
                                   f"已写入:\n  {target}\n\n原子 {r['n_atoms']}  |  键 {r['n_bonds']}  |  角 {r['n_angles']}\n\n是否现在打开该 CSV？",
                                   parent=self.app):
                try:
                    self._safe_open_file(target)
                except Exception as e:
                    self.show_friendly(e)
        else:
            self.show_friendly(r.get("message", "导出失败"))

    def show_recent_dirs_dialog(self):
        from config import save_config

        dialog = tk.Toplevel(self.app)
        dialog.title("📂 最近工作目录")
        dialog.geometry("650x450")
        dialog.transient(self.app)
        dialog.grab_set()

        top_btn_frame = ttk.Frame(dialog)
        top_btn_frame.pack(fill=tk.X, padx=10, pady=10)

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        listbox = tk.Listbox(list_frame, height=12, font=('Consolas', 10))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def refresh_list():
            listbox.delete(0, tk.END)
            for d in self.controller.get_recent_work_dirs():
                listbox.insert(tk.END, d)

        def clear_history():
            self.app.config_data["recent_work_dirs"] = []
            save_config(self.app.config_data)
            refresh_list()

        ttk.Button(top_btn_frame, text="🔄 刷新列表", command=refresh_list).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_btn_frame, text="🗑️ 清空历史", command=clear_history).pack(side=tk.LEFT, padx=5)

        refresh_list()

        bottom_btn_frame = ttk.Frame(dialog)
        bottom_btn_frame.pack(fill=tk.X, padx=10, pady=15)

        def do_switch():
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            self.controller.switch_recent_work_dir(idx)
            dialog.destroy()

        listbox.bind("<Double-Button-1>", lambda e: do_switch())

        ttk.Button(bottom_btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_btn_frame, text="✅ 切换到此目录", command=do_switch).pack(side=tk.RIGHT, padx=5)

    def show_mapping_manager_dialog(self):
        model = self.app.controller.model
        dialog = tk.Toplevel(self.app)
        dialog.title("📋 映射表管理")
        dialog.geometry("560x380")
        dialog.transient(self.app)
        dialog.grab_set()

        info_label_var = tk.StringVar(value=f"当前映射条目：{len(model.mapping)}  |  缺失映射：{len(model.generate_missing_list())}")
        ttk.Label(dialog, textvariable=info_label_var, font=('Arial', 10, 'bold')).pack(pady=15)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)

        def refresh_info():
            info_label_var.set(f"当前映射条目：{len(model.mapping)}  |  缺失映射：{len(model.generate_missing_list())}")

        def export_missing():
            csv_path = filedialog.asksaveasfilename(
                initialdir=str(model.work_dir),
                initialfile="missing_mapping.csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                defaultextension=".csv"
            )
            if not csv_path:
                return
            try:
                count = model.export_missing_csv(str(Path(csv_path)))
                messagebox.showinfo("导出成功", f"已导出 {count} 条缺失映射记录到：\n{csv_path}", parent=dialog)
                self.app.helpers.on_log(f"💾 导出缺失映射表: {count} 条", 'success')
            except Exception as e:
                messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
                self.app.helpers.on_log(f"❌ 导出缺失映射表失败: {e}", 'error')

        def import_missing(overwrite=False):
            csv_path = filedialog.askopenfilename(
                initialdir=str(model.work_dir),
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                title="选择映射表 CSV 文件"
            )
            if not csv_path:
                return
            try:
                result = model.import_mapping_csv(str(Path(csv_path)), overwrite=overwrite)
                self.app.helpers.on_log(
                    f"📥 导入映射表: 新增 {result['added']} 条, 跳过 {result['skipped']} 条, "
                    f"错误 {result['errors']} 条, 总行数 {result['total_rows']}",
                    'success' if result['errors'] == 0 else 'warning'
                )
                refresh_info()
                if messagebox.askyesno(
                    "导入完成",
                    f"导入结果：\n  新增：{result['added']} 条\n  跳过：{result['skipped']} 条\n  错误：{result['errors']} 条\n\n是否刷新文件列表？",
                    parent=dialog
                ):
                    self.app.controller.scan_files()
            except Exception as e:
                messagebox.showerror("导入失败", f"导入失败：{e}", parent=dialog)
                self.app.helpers.on_log(f"❌ 导入映射表失败: {e}", 'error')

        def export_mapping():
            csv_path = filedialog.asksaveasfilename(
                initialdir=str(model.work_dir),
                initialfile="mapping_full.csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                defaultextension=".csv"
            )
            if not csv_path:
                return
            try:
                count = model.export_mapping_csv(str(Path(csv_path)))
                messagebox.showinfo("导出成功", f"已导出 {count} 条映射记录到：\n{csv_path}", parent=dialog)
                self.app.helpers.on_log(f"💾 导出完整映射表: {count} 条", 'success')
            except Exception as e:
                messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
                self.app.helpers.on_log(f"❌ 导出完整映射表失败: {e}", 'error')

        btn_export_missing = ttk.Button(btn_frame, text="💾 导出缺失表 (CSV)", command=export_missing, width=28)
        btn_export_missing.grid(row=0, column=0, padx=10, pady=8)

        btn_import_missing = ttk.Button(btn_frame, text="📥 导入缺失表 (CSV)", command=lambda: import_missing(overwrite=False), width=28)
        btn_import_missing.grid(row=0, column=1, padx=10, pady=8)

        btn_export_mapping = ttk.Button(btn_frame, text="📤 导出当前映射表", command=export_mapping, width=28)
        btn_export_mapping.grid(row=1, column=0, padx=10, pady=8)

        btn_import_overwrite = ttk.Button(btn_frame, text="🔄 覆盖式导入", command=lambda: import_missing(overwrite=True), width=28)
        btn_import_overwrite.grid(row=1, column=1, padx=10, pady=8)

        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        ttk.Button(dialog, text="关闭", command=dialog.destroy, width=20).pack(pady=20)

    def show_reaction_animation_dialog(self):
        import reaction_animation as ra
        import shutil as _shutil
        import subprocess as _sp

        dialog = tk.Toplevel(self.app)
        dialog.title("🎬 制作反应动画（含 IQmol 可播放轨迹 · 支持多反应物+多产物）")
        dialog.geometry("900x820")
        dialog.transient(self.app)
        try:
            dialog.grab_set()
        except Exception:
            pass

        pad = {"padx": 12, "pady": 4}

        def _browse_open(store_var, title, filters):
            init = str(self.controller.model.work_dir)
            f = filedialog.askopenfilename(parent=dialog, initialdir=init, title=title, filetypes=filters)
            if f:
                store_var.set(f)

        def _browse_open_multi(listbox):
            init = str(self.controller.model.work_dir)
            fs = filedialog.askopenfilenames(parent=dialog, initialdir=init,
                title="选择分子文件（可多选）",
                filetypes=[("分子文件", "*.xyz *.mol *.sdf *.mol2"), ("所有文件", "*.*")])
            if fs:
                for f in fs:
                    self._ra_add_unique_path(listbox, f)

        def _browse_save(store_var, title, ext, filters):
            init = str(self.controller.model.work_dir)
            f = filedialog.asksaveasfilename(parent=dialog, initialdir=init, title=title,
                defaultextension=ext, filetypes=filters)
            if f:
                store_var.set(f)

        csv_var = tk.StringVar()
        ffmpeg_var = tk.StringVar(value="ffmpeg")

        out_var = tk.StringVar(value=str(self.controller.model.work_dir / "reaction_animation.gif"))
        traj_var = tk.StringVar(value=str(self.controller.model.work_dir / "reaction_trajectory.xyz"))
        traj_fmt_var = tk.StringVar(value="xyz")
        iqmol_path_var = tk.StringVar(value="IQmol")
        auto_open_iqmol_var = tk.BooleanVar(value=False)
        gen_traj_var = tk.BooleanVar(value=True)

        header = ttk.Label(dialog,
            text="① 选择反应物/产物（**可多选**，多反应物 + 多产物自动沿 X 轴平移拼接）\n"
                 "  💡 新手快用：直接点下面 「常见反应模板」 按钮一键填好！\n"
                 "② 可视化：GIF / MP4 / PNG 帧目录（可 none）\n"
                 "③ IQmol：输出多帧 XYZ / SDF 轨迹，打开自动进入 Animation 播放（支持能量列）",
            foreground='#1f6feb', font=('Microsoft YaHei', 10, 'bold'), wraplength=860, justify='left')
        header.pack(padx=12, pady=(12, 6), anchor='w')

        # ============ ✨ UX-4：常见反应模板（新手一键填） ============
        tpl_frame = ttk.LabelFrame(dialog, text="✨ 常见反应模板（点一下自动填好反应物和产物）", padding=8)
        tpl_frame.pack(fill='x', padx=12, pady=(0, 4))
        tpl_hint = ttk.Label(tpl_frame, text="如果工作目录里有同名 .xyz 就用你的，没有就用内置分子坐标", foreground="#666666")
        tpl_hint.pack(anchor='w', padx=4, pady=(0, 4))
        tpl_btn_row = ttk.Frame(tpl_frame); tpl_btn_row.pack(fill='x')

        # 内置常见反应的 SMILES 式分子（OpenBabel 转 xyz；没有 OB 就手写最小 xyz）
        BUILTIN_XYZ: dict[str, tuple[int, list[str], list[list[float]]]] = {
            # CH4 甲烷：四面体
            "ch4": (5, ["C", "H", "H", "H", "H"], [
                [0.00000, 0.00000, 0.00000],
                [0.62912, 0.62912, 0.62912],
                [-0.62912, -0.62912, 0.62912],
                [-0.62912, 0.62912, -0.62912],
                [0.62912, -0.62912, -0.62912],
            ]),
            # Cl2 氯气
            "cl2": (2, ["Cl", "Cl"], [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
            # HCl 氯化氢
            "hcl": (2, ["H", "Cl"], [[0.0, 0.0, 0.0], [1.28, 0.0, 0.0]]),
            # CH3Cl 一氯甲烷
            "ch3cl": (5, ["C", "Cl", "H", "H", "H"], [
                [0.00000, 0.00000, 0.00000],
                [1.78000, 0.00000, 0.00000],
                [-0.35700, 0.95000, 0.35700],
                [-0.35700, -0.52000, 0.88600],
                [-0.35700, -0.52000, -0.88600],
            ]),
            # H2 氢气
            "h2": (2, ["H", "H"], [[0.0, 0.0, 0.0], [0.74, 0.0, 0.0]]),
            # H2O 水
            "h2o": (3, ["O", "H", "H"], [[0.0, 0.0, 0.0], [0.957, 0.0, 0.0], [-0.239, 0.927, 0.0]]),
            # O2 氧气
            "o2": (2, ["O", "O"], [[0.0, 0.0, 0.0], [1.21, 0.0, 0.0]]),
            # CO2 二氧化碳
            "co2": (3, ["C", "O", "O"], [[0.0, 0.0, 0.0], [1.16, 0.0, 0.0], [-1.16, 0.0, 0.0]]),
            # N2 氮气
            "n2": (2, ["N", "N"], [[0.0, 0.0, 0.0], [1.098, 0.0, 0.0]]),
            # NH3 氨
            "nh3": (4, ["N", "H", "H", "H"], [
                [0.00000, 0.00000, 0.00000],
                [0.93770, 0.00000, 0.36690],
                [-0.46890, 0.81200, 0.36690],
                [-0.46890, -0.81200, 0.36690],
            ]),
            # CH3OH 甲醇
            "ch3oh": (6, ["C", "O", "H", "H", "H", "H"], [
                [0.74410, 0.00000, 0.00000],
                [-0.68660, 0.00000, 0.00000],
                [1.10690, 0.96170, 0.34510],
                [1.10690, -0.45960, 0.89340],
                [1.10690, -0.50210, -0.92740],
                [-1.07800, 0.81090, 0.00000],
            ]),
            # C2H4 乙烯
            "c2h4": (6, ["C", "C", "H", "H", "H", "H"], [
                [0.66950, 0.00000, 0.00000],
                [-0.66950, 0.00000, 0.00000],
                [1.24000, 0.92890, 0.00000],
                [1.24000, -0.92890, 0.00000],
                [-1.24000, 0.92890, 0.00000],
                [-1.24000, -0.92890, 0.00000],
            ]),
            # C2H6 乙烷
            "c2h6": (8, ["C", "C", "H", "H", "H", "H", "H", "H"], [
                [0.76440, 0.00000, 0.00000],
                [-0.76440, 0.00000, 0.00000],
                [1.15590, 0.55840, 0.85880],
                [1.15590, 0.38120, -0.97300],
                [1.15590, -0.93960, 0.11420],
                [-1.15590, -0.55840, -0.85880],
                [-1.15590, -0.38120, 0.97300],
                [-1.15590, 0.93960, -0.11420],
            ]),
        }

        def _resolve_or_build(name: str, tmpdir: Path) -> str:
            """先在工作目录找同名 .xyz，找不到就写一份内置坐标到 tmpdir，返回路径"""
            workdir = Path(self.controller.model.work_dir)
            candidate = workdir / f"{name}.xyz"
            if candidate.exists():
                return str(candidate)
            # 尝试 .mol
            candidate2 = workdir / f"{name}.mol"
            if candidate2.exists():
                return str(candidate2)
            # 生成内置 xyz
            if name not in BUILTIN_XYZ:
                return str(candidate)  # 不存在会被下游正确报错
            n, syms, coords = BUILTIN_XYZ[name]
            lines: list[str] = [str(n), "", ]
            for s, (x, y, z) in zip(syms, coords):
                lines.append(f"{s:2s} {x:12.6f} {y:12.6f} {z:12.6f}")
            p = tmpdir / f"tpl_{name}.xyz"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(p)

        def _apply_template(r_names: list[str], p_names: list[str], def_solvent: str | None = None):
            import tempfile
            import shutil as _shu
            r_list.delete(0, tk.END); p_list.delete(0, tk.END)

            # M-1 修复：用集合记录本对话框整个生命周期内所有模板创建过的临时目录，
            # 不再只记录「最后一个」；WM_DELETE_WINDOW / <Destroy> 事件 / atexit 三条路径
            # 都会对整个集合一次性清理，避免用户疯狂切换模板时早期目录残留到 atexit 才清。
            tpl_dirs: set[Path] | None = getattr(dialog, "_ra_tpl_dirs", None)
            if tpl_dirs is None:
                tpl_dirs = set()
                dialog._ra_tpl_dirs = tpl_dirs  # type: ignore[attr-defined]

                def _cleanup_all_tpl_dirs() -> None:
                    """清理集合中所有仍存在的 ra_tpl_ 临时目录；幂等，重复调用无害。"""
                    # 从 dialog 上摘下集合，避免 <Destroy> 和 WM_DELETE_WINDOW 各自执行时重复。
                    ds: set[Path] = getattr(dialog, "_ra_tpl_dirs", None) or set()
                    try:
                        delattr(dialog, "_ra_tpl_dirs")
                    except Exception:
                        pass
                    for d_ in list(ds):
                        try:
                            p_ = Path(d_)
                            if p_.exists():
                                _shu.rmtree(str(p_), ignore_errors=True)
                            unregister_dialog_temp_dir(p_)
                        except Exception:
                            pass

                # (1) 窗口系统菜单 / 关闭按钮走 protocol
                dialog.protocol("WM_DELETE_WINDOW", lambda: (_cleanup_all_tpl_dirs(), dialog.destroy()))  # type: ignore[arg-type,return-value]

                # (2) 代码里调用 dialog.destroy()（比如 OK/Cancel 按钮）走 <Destroy> 事件；
                #     Tk 的 <Destroy> 对子控件也会触发，必须判断 widget 是顶层 dialog 自己。
                def _on_dialog_destroy(event):
                    if event.widget is not dialog:
                        return
                    _cleanup_all_tpl_dirs()
                dialog.bind("<Destroy>", _on_dialog_destroy)

            # 先清理上一次模板目录（切换模板时立即释放，不等到对话框关闭）
            _prev = getattr(dialog, "_ra_tpl_last", None)
            if _prev is not None:
                try:
                    _pp = Path(_prev)
                    if _pp.exists():
                        _shu.rmtree(str(_pp), ignore_errors=True)
                    unregister_dialog_temp_dir(_pp)
                    tpl_dirs.discard(_pp)
                except Exception:
                    pass
                dialog._ra_tpl_last = None  # type: ignore[attr-defined]

            td = Path(tempfile.mkdtemp(prefix="ra_tpl_"))
            register_dialog_temp_dir(td)
            tpl_dirs.add(td)
            dialog._ra_tpl_last = td  # type: ignore[attr-defined]  # 供下次切换模板时及时清理上一个

            for n in r_names:
                self._ra_add_unique_path(r_list, _resolve_or_build(n, td))
            for n in p_names:
                self._ra_add_unique_path(p_list, _resolve_or_build(n, td))
            if def_solvent is not None:
                for k in SOLVENT_CHOICES:
                    if k.startswith(def_solvent + " "):
                        solvent_var.set(k); break
            result_text.configure(state='normal')
            result_text.delete('1.0', tk.END)
            result_text.insert(tk.END, f"✅ 已加载模板：反应物={'+'.join(r_names)} → 产物={'+'.join(p_names)}\n")
            if def_solvent is not None:
                result_text.insert(tk.END, f"   溶剂自动设为：{def_solvent}\n")
            result_text.insert(tk.END, "   💡 直接点下方 「▶ 生成反应动画」 即可，其他参数默认就行\n")
            result_text.configure(state='disabled')

        tpl_btns = [
            ("🔥 CH4 氯代 CH4+Cl2→CH3Cl+HCl",   ["ch4", "cl2"],   ["ch3cl", "hcl"],   None),
            ("💧 氢气燃烧 2H2+O2→2H2O",          ["h2", "h2", "o2"], ["h2o", "h2o"],      "water"),
            ("⚗️ 乙烯加氢 C2H4+H2→C2H6",         ["c2h4", "h2"],   ["c2h6"],            None),
            ("🧪 甲醇合成 (演示：CH4+O2+H2→CH3OH+H2O)", ["ch4", "o2", "h2"], ["ch3oh", "h2o"], None),
            ("🌱 光合作用 (演示 CO2+H2O→有机物+O2)", ["co2", "h2o"], ["c2h6", "o2"], "water"),
            ("🔬 合成氨 N2+3H2→2NH3",            ["n2", "h2", "h2", "h2"], ["nh3", "nh3"], None),
        ]
        rows: list[ttk.Frame] = [tpl_btn_row]
        for i, (label, rs, ps, sol) in enumerate(tpl_btns):
            row_idx, _col = divmod(i, 3)
            while len(rows) <= row_idx:
                nr = ttk.Frame(tpl_frame); nr.pack(fill='x', pady=(4, 0)); rows.append(nr)
            b = ttk.Button(rows[row_idx], text=label,
                           command=lambda _rs=rs, _ps=ps, _s=sol: _apply_template(_rs, _ps, _s))
            b.pack(side='left', padx=4, pady=2, fill='x', expand=True)
            from ui_builder import add_tooltip as _tt
            _tt(b, f"示例反应：\n反应物: {' + '.join(rs)}\n产物:   {' + '.join(ps)}")
        # ============ 模板结束 ============

        mol_filters = [("分子文件", "*.xyz *.mol *.sdf *.mol2"), ("所有文件", "*.*")]
        r_frame = ttk.LabelFrame(dialog, text="反应物列表（可多选，按先后顺序沿 +X 拼接）")
        r_frame.pack(fill='x', padx=12, pady=4)
        r_list = tk.Listbox(r_frame, height=6, selectmode=tk.EXTENDED)
        r_sb = ttk.Scrollbar(r_frame, orient='vertical', command=r_list.yview)
        r_list.configure(yscrollcommand=r_sb.set)
        r_list.pack(side='left', fill='both', expand=True, padx=(8, 2), pady=6)
        r_sb.pack(side='left', fill='y', pady=6)
        r_btns = ttk.Frame(r_frame); r_btns.pack(side='left', fill='y', padx=6, pady=6)
        ttk.Button(r_btns, text="➕ 添加", width=10,
                   command=lambda: _browse_open_multi(r_list)).pack(pady=2)
        ttk.Button(r_btns, text="➖ 删除选中", width=10,
                   command=lambda: self._ra_delete_selected(r_list)).pack(pady=2)

        p_frame = ttk.LabelFrame(dialog, text="产物列表（可多选）")
        p_frame.pack(fill='x', padx=12, pady=4)
        p_list = tk.Listbox(p_frame, height=6, selectmode=tk.EXTENDED)
        p_sb = ttk.Scrollbar(p_frame, orient='vertical', command=p_list.yview)
        p_list.configure(yscrollcommand=p_sb.set)
        p_list.pack(side='left', fill='both', expand=True, padx=(8, 2), pady=6)
        p_sb.pack(side='left', fill='y', pady=6)
        p_btns = ttk.Frame(p_frame); p_btns.pack(side='left', fill='y', padx=6, pady=6)
        ttk.Button(p_btns, text="➕ 添加", width=10,
                   command=lambda: _browse_open_multi(p_list)).pack(pady=2)
        ttk.Button(p_btns, text="➖ 删除选中", width=10,
                   command=lambda: self._ra_delete_selected(p_list)).pack(pady=2)

        qm = ttk.LabelFrame(dialog, text="🧪 溶剂 & 能量（可选：一键跑 PSI4 线性扫描，自动写入每帧 E= 注释）")
        qm.pack(fill='x', padx=12, pady=(6, 4))
        rq1 = ttk.Frame(qm); rq1.pack(fill='x', **pad)
        SOLVENT_CHOICES = [
            "（不使用溶剂，气相）",
            "water (水)",
            "methanol (甲醇)",
            "ethanol (乙醇)",
            "acetonitrile (乙腈，CH3CN)",
            "dimethylsulfoxide (DMSO)",
            "chloroform (氯仿，CHCl3)",
            "dichloromethane (二氯甲烷，DCM)",
            "tetrahydrofuran (THF)",
            "toluene (甲苯)",
            "benzene (苯)",
            "acetone (丙酮)",
            "diethyl ether (乙醚)",
            "ethyl acetate (乙酸乙酯)",
            "hexane (正己烷)",
            "cyclohexane (环己烷)",
            "dimethylformamide (DMF，N,N-二甲基甲酰胺)",
        ]
        ttk.Label(rq1, text="隐式溶剂 (PCM/SMD):", width=22, anchor='w').pack(side='left')
        solvent_var = tk.StringVar(value=SOLVENT_CHOICES[0])
        solvent_cb = ttk.Combobox(rq1, textvariable=solvent_var, state="readonly",
                                  width=42, values=SOLVENT_CHOICES)
        solvent_cb.pack(side='left', padx=(0, 8))
        solvent_cb_var_to_key_map: dict[str, str | None] = {}
        for _it in SOLVENT_CHOICES:
            if _it.startswith("（不"):
                solvent_cb_var_to_key_map[_it] = None
            else:
                solvent_cb_var_to_key_map[_it] = _it.split(" ", 1)[0].strip()

        ttk.Label(rq1, text="  方法/基组:", width=10, anchor='w').pack(side='left')
        qm_method_var = tk.StringVar(value="b3lyp")
        ttk.Combobox(rq1, textvariable=qm_method_var, width=10, state="readonly",
                     values=["b3lyp", "hf", "wb97x-d", "wb97xd", "m06-2x", "m062x", "pbe0", "bp86", "mp2"]).pack(side='left')
        qm_basis_var = tk.StringVar(value="6-31g*")
        ttk.Combobox(rq1, textvariable=qm_basis_var, width=14,
                     values=["sto-3g", "3-21g", "6-31g", "6-31g*", "6-31g(d)", "6-311g**",
                             "6-311++g(d,p)", "def2-svp", "def2-svpd", "def2-tzvp", "def2-tzvpd",
                             "cc-pvdz", "cc-pvtz", "aug-cc-pvdz", "aug-cc-pvtz"]).pack(side='left', padx=6)
        qm_d3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rq1, text="D3 色散校正", variable=qm_d3_var).pack(side='left', padx=4)

        rq2 = ttk.Frame(qm); rq2.pack(fill='x', **pad)
        ttk.Label(rq2, text="扫描帧数 (每帧单点能):", width=22, anchor='w').pack(side='left')
        scan_steps_var = tk.IntVar(value=10)
        ttk.Spinbox(rq2, from_=3, to=100, width=7, textvariable=scan_steps_var).pack(side='left')
        ttk.Label(rq2, text="   电荷:", width=8, anchor='w').pack(side='left')
        qm_charge_var = tk.IntVar(value=0)
        ttk.Spinbox(rq2, from_=-10, to=10, width=5, textvariable=qm_charge_var).pack(side='left')
        ttk.Label(rq2, text="   多重度:", width=9, anchor='w').pack(side='left')
        qm_mult_var = tk.IntVar(value=1)
        ttk.Spinbox(rq2, from_=1, to=6, width=5, textvariable=qm_mult_var).pack(side='left')
        ttk.Label(rq2, text="   PSI4 内存:", width=11, anchor='w').pack(side='left')
        qm_mem_var = tk.StringVar(value="4 GB")
        ttk.Entry(rq2, textvariable=qm_mem_var, width=8).pack(side='left')

        rq3 = ttk.Frame(qm); rq3.pack(fill='x', **pad)
        preset_var = tk.StringVar(value="（无预设）")
        ttk.Label(rq3, text="预设:", width=6, anchor='w').pack(side='left')
        try:
            from constants import PSI4_PRESETS as _PP
            _presets_list = ["（无预设）"] + sorted(list(_PP.keys()))
        except Exception:
            _presets_list = ["（无预设）"]
        ttk.Combobox(rq3, textvariable=preset_var, width=28, state="readonly",
                     values=_presets_list).pack(side='left')
        scan_output_var = tk.StringVar(value=str(self.controller.model.work_dir / "scan_output"))
        ttk.Label(rq3, text="  扫描输出目录:", width=14, anchor='w').pack(side='left')
        ttk.Entry(rq3, textvariable=scan_output_var, width=30).pack(side='left', padx=(0, 4))
        ttk.Button(rq3, text="浏览...", width=8,
                   command=lambda: (scan_output_var.set(
                       filedialog.askdirectory(parent=dialog, initialdir=str(self.controller.model.work_dir),
                                               title="选择势能面扫描输出目录")
                       or scan_output_var.get()))).pack(side='left')
        run_scan_btn = ttk.Button(rq3, text="⚡ 运行 PSI4 线性扫描并自动填 CSV", width=40)
        run_scan_btn.pack(side='right', padx=4)

        def _run_scan_and_fill():
            reactants = [r_list.get(i) for i in range(r_list.size())]
            products = [p_list.get(i) for i in range(p_list.size())]
            if len(reactants) == 0 or len(products) == 0:
                messagebox.showwarning("提示", "请先添加反应物和产物文件（至少各 1 个）", parent=dialog)
                return
            try:
                spacing_val = float(spacing_var.get())
                scan_steps_val = max(2, int(scan_steps_var.get()))
                qm_method_val = str(qm_method_var.get()).strip() or "b3lyp"
                qm_basis_val = str(qm_basis_var.get()).strip() or "6-31g*"
                qm_output_dir_val = str(scan_output_var.get()).strip() or None
                solvent_val = solvent_cb_var_to_key_map.get(solvent_var.get())
                preset_val = None if preset_var.get().startswith("（无") else str(preset_var.get()).strip()
                qm_d3_val = bool(qm_d3_var.get())
                qm_charge_val = int(qm_charge_var.get())
                qm_mult_val = int(qm_mult_var.get())
                qm_mem_val = str(qm_mem_var.get()).strip() or "4 GB"
                if qm_output_dir_val:
                    try:
                        _p_out = Path(qm_output_dir_val).resolve()
                        import platform
                        if platform.system() == "Windows":
                            _root = _p_out.anchor
                            _sens = [
                                Path(_root) / "Windows",
                                Path(_root) / "Program Files",
                                Path(_root) / "Program Files (x86)",
                                Path(_root) / "ProgramData",
                                Path(_root) / "System Volume Information",
                            ]
                            for _s in _sens:
                                try:
                                    _p_out.relative_to(_s)
                                    if not messagebox.askyesno(
                                        "确认输出目录",
                                        f"检测到输出目录位于系统敏感目录 {_s} 下，\n继续写入可能需要管理员权限或失败。\n\n是否仍继续？",
                                        parent=dialog):
                                        scan_btn.configure(state="normal")
                                        return
                                    break
                                except ValueError:
                                    continue
                    except Exception:
                        pass
            except (ValueError, TypeError) as _e:
                messagebox.showwarning("提示", f"扫描参数格式错误: {_e}", parent=dialog)
                return
            dlg = dialog
            scan_btn = run_scan_btn
            scan_btn.configure(state="disabled")
            ui_updates_pending: dict[str, Any] = {}
            def _scan_task(**kw):
                cb = kw.get('_progress_callback')
                import tempfile as _tf
                from psi4_compute import _write_xyz, run_linear_scan
                with _tf.TemporaryDirectory(prefix="qm_scan_setup_") as _td:
                    _tdp = Path(_td)
                    try:
                        _nR, _aR, _cR = ra._concat_xyz_files(reactants, translate_spacing=spacing_val)
                        _nP, _aP, _cP = ra._concat_xyz_files(products, translate_spacing=spacing_val)
                        _aP2, _cP2 = ra._auto_reorder_atoms(_aR, _cR, _aP, _cP)
                    except Exception as _e:
                        return {"ok": False, "error": f"分子对齐失败: {_e}", "msgs": []}
                    _rx = _tdp / "R.xyz"; _px = _tdp / "P.xyz"
                    _rx.write_text(_write_xyz(_nR, _aR, _cR), encoding="utf-8")
                    _px.write_text(_write_xyz(_nP, _aP2, _cP2), encoding="utf-8")
                    try:
                        if qm_output_dir_val:
                            os.makedirs(qm_output_dir_val, exist_ok=True)
                    except Exception:
                        pass
                    res = run_linear_scan(
                        [str(_rx)], [str(_px)],
                        steps=scan_steps_val,
                        method=qm_method_val,
                        basis=qm_basis_val,
                        output_dir=qm_output_dir_val,
                        preset_name=preset_val,
                        solvent=solvent_val,
                        d3=qm_d3_val,
                        charge=qm_charge_val,
                        multiplicity=qm_mult_val,
                        memory=qm_mem_val,
                        _progress_callback=cb,
                    )
                if res.get("scan_csv"):
                    ui_updates_pending["csv"] = str(res["scan_csv"])
                return {"ok": bool(res.get("success")),
                        "error": res.get("error"),
                        "csv": res.get("scan_csv"),
                        "n_steps": len(res.get("energies") or [])}
            def _after_dialog_safe(result):
                if "csv" in ui_updates_pending:
                    try:
                        csv_var.set(ui_updates_pending["csv"])
                    except Exception:
                        pass
                try:
                    scan_btn.configure(state="normal")
                except Exception:
                    pass
                if not isinstance(result, dict):
                    try:
                        messagebox.showerror("失败", "扫描任务未返回结果", parent=dlg)
                    except Exception:
                        pass
                    return
                if result.get("ok"):
                    csv_path = result.get("csv")
                    body = (f"✅ PSI4 线性扫描完成（共 {result.get('n_steps')} 帧）\n"
                            f"   CSV: {csv_path}\n"
                            f"   溶剂: {solvent_cb_var_to_key_map.get(solvent_var.get()) or '(气相)'}\n\n"
                            f"现在生成动画/IQmol 轨迹时将自动读取每帧能量 E= 注释")
                    try:
                        messagebox.showinfo("扫描完成", body, parent=dlg)
                    except Exception:
                        pass
                else:
                    err = result.get("error") or "未知错误"
                    try:
                        messagebox.showerror("扫描失败", f"❌ {err}", parent=dlg)
                    except Exception:
                        pass
            dlg_update_ref = [dialog]
            def _submit():
                def runner(**kw):
                    res = _scan_task(**kw)
                    def _after_run():
                        try:
                            if dlg_update_ref and dlg_update_ref[0] and dlg_update_ref[0].winfo_exists():
                                _after_dialog_safe(res)
                        except Exception:
                            pass
                    try:
                        self.app.after(0, _after_run)
                    except Exception:
                        pass
                    return res
                self.app.helpers.run_task(runner)
            _submit()

        run_scan_btn.configure(command=_run_scan_and_fill)

        row_csv = ttk.Frame(dialog); row_csv.pack(fill='x', **pad)
        ttk.Label(row_csv, text="势能面能量 CSV:", width=18, anchor='w').pack(side='left')
        ttk.Entry(row_csv, textvariable=csv_var).pack(side='left', fill='x', expand=True, padx=(4, 4))
        ttk.Button(row_csv, text="浏览...", width=8,
                   command=lambda: _browse_open(csv_var, "选择线性扫描结果 CSV（可选）",
                                                 [("CSV 文件", "*.csv"), ("所有文件", "*.*")])).pack(side='left')

        opts = ttk.LabelFrame(dialog, text="插值 / 可视化参数"); opts.pack(fill='x', padx=12, pady=(6, 6))
        r1 = ttk.Frame(opts); r1.pack(fill='x', **pad)
        ttk.Label(r1, text="插值步数 (单程):", width=18, anchor='w').pack(side='left')
        steps_var = tk.IntVar(value=30)
        ttk.Spinbox(r1, from_=5, to=500, width=7, textvariable=steps_var).pack(side='left')

        ttk.Label(r1, text="   播放模式:", width=12, anchor='w').pack(side='left')
        mode_var = tk.StringVar(value="bounce")
        ttk.Combobox(r1, textvariable=mode_var, state="readonly", width=20,
                     values=["bounce (R→P→R 循环)", "forward (R→P 单程)"]).pack(side='left')

        ttk.Label(r1, text="   FPS:", width=6, anchor='w').pack(side='left')
        fps_var = tk.IntVar(value=15)
        ttk.Spinbox(r1, from_=1, to=120, width=6, textvariable=fps_var).pack(side='left')

        ttk.Label(r1, text="   分子间距 (Å):", width=16, anchor='w').pack(side='left')
        spacing_var = tk.DoubleVar(value=5.0)
        ttk.Spinbox(r1, from_=2.0, to=30.0, increment=0.5, width=6, textvariable=spacing_var).pack(side='left')

        r2 = ttk.Frame(opts); r2.pack(fill='x', **pad)
        smooth_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="cosine 缓动（首尾更平滑）", variable=smooth_var).pack(side='left')

        ttk.Label(r2, text="   分辨率:", width=10, anchor='w').pack(side='left')
        res_var = tk.StringVar(value="hd")
        ttk.Combobox(r2, textvariable=res_var, state="readonly", width=18,
                     values=["sd (640x480)", "hd (1280x720)", "fullhd (1920x1080)"]).pack(side='left')

        ttk.Label(r2, text="   输出格式:", width=10, anchor='w').pack(side='left')
        fmt_var = tk.StringVar(value="gif")
        ttk.Combobox(r2, textvariable=fmt_var, state="readonly", width=28,
                     values=["gif (GIF 动图，Pillow)", "mp4 (MP4 视频，ffmpeg)",
                             "png_dir (仅输出 PNG 帧目录)", "none (不生成可视化，只做 IQmol 轨迹)"]).pack(side='left')

        r3 = ttk.Frame(opts); r3.pack(fill='x', **pad)
        ttk.Label(r3, text="ffmpeg 路径:", width=18, anchor='w').pack(side='left')
        ttk.Entry(r3, textvariable=ffmpeg_var, width=32).pack(side='left')
        cap = "（仅 MP4 格式需要；默认 PATH 的 ffmpeg）"
        ttk.Label(r3, text=cap, foreground='#6a737d').pack(side='left', padx=(8, 0))

        if not ra.PIL_AVAILABLE:
            ttk.Label(dialog,
                text="⚠️  未检测到 Pillow，无法添加字幕条、无法合成 GIF（已自动回退为仅 PNG 目录）；建议 pip install pillow",
                foreground='#d73a49', wraplength=860, justify='left').pack(padx=12, pady=(4, 4), anchor='w')

        iq = ttk.LabelFrame(dialog, text="🧪 IQmol 可播放轨迹输出（推荐！支持多反应物/多产物）")
        iq.pack(fill='x', padx=12, pady=(4, 4))
        ttk.Checkbutton(iq, text="同时生成 IQmol 多帧轨迹文件（推荐 always on）",
                        variable=gen_traj_var).pack(padx=10, pady=(6, 2), anchor='w')
        row_t = ttk.Frame(iq); row_t.pack(fill='x', **pad)
        ttk.Label(row_t, text="IQmol 轨迹输出:", width=18, anchor='w').pack(side='left')
        ttk.Entry(row_t, textvariable=traj_var).pack(side='left', fill='x', expand=True, padx=(4, 4))
        ttk.Button(row_t, text="浏览...", width=8,
                   command=lambda: _browse_save(traj_var, "保存 IQmol 轨迹文件", ".xyz",
                                                [("IQmol 多帧 XYZ", "*.xyz"),
                                                 ("SDF 轨迹", "*.sdf"),
                                                 ("所有文件", "*.*")])).pack(side='left')

        rq = ttk.Frame(iq); rq.pack(fill='x', **pad)
        ttk.Label(rq, text="轨迹格式:", width=18, anchor='w').pack(side='left')
        ttk.Combobox(rq, textvariable=traj_fmt_var, state="readonly", width=26,
                     values=["xyz (Concatenated 多帧 XYZ，IQmol 直接播放)",
                             "sdf (SDF 多构象，带 >  <Energy> 字段)"]).pack(side='left')

        ttk.Label(rq, text="   IQmol 程序路径:", width=16, anchor='w').pack(side='left')
        ttk.Entry(rq, textvariable=iqmol_path_var, width=22).pack(side='left')
        ttk.Checkbutton(rq, text="生成后立即打开", variable=auto_open_iqmol_var).pack(side='left', padx=(10, 0))

        def _def_ext_for_format(fmt_tok: str, traj: bool) -> tuple[str, list[tuple[str, str]]]:
            if traj:
                if fmt_tok.startswith("sdf"):
                    return ".sdf", [("SDF 轨迹", "*.sdf"), ("XYZ 轨迹", "*.xyz"), ("所有文件", "*.*")]
                return ".xyz", [("IQmol 多帧 XYZ", "*.xyz"), ("SDF 轨迹", "*.sdf"), ("所有文件", "*.*")]
            if fmt_tok.startswith("mp4"):
                return ".mp4", [("MP4 视频", "*.mp4"), ("GIF", "*.gif"), ("所有文件", "*.*")]
            if fmt_tok.startswith("png_dir") or fmt_tok.startswith("none"):
                return "", [("PNG 目录 / 无", "*")]
            return ".gif", [("GIF 动图", "*.gif"), ("MP4", "*.mp4"), ("所有文件", "*.*")]

        def _on_change_fmt(_e=None):
            tok = fmt_var.get().strip().lower()
            default_ext, _ = _def_ext_for_format(tok, False)
            if tok.startswith("none"):
                out_var.set("")
            else:
                p_ = Path(out_var.get().strip() or str(self.controller.model.work_dir / "reaction_animation.gif"))
                if default_ext:
                    p_ = p_.with_suffix(default_ext)
                out_var.set(str(p_))
            ttok = traj_fmt_var.get().strip().lower()
            text, _ = _def_ext_for_format(ttok, True)
            tp = Path(traj_var.get().strip() or str(self.controller.model.work_dir / "reaction_trajectory.xyz"))
            if text:
                tp = tp.with_suffix(text)
            traj_var.set(str(tp))

        fmt_var.trace_add("write", lambda *_: _on_change_fmt())
        traj_fmt_var.trace_add("write", lambda *_: _on_change_fmt())

        row_out = ttk.Frame(dialog); row_out.pack(fill='x', **pad)
        ttk.Label(row_out, text="可视化输出:", width=18, anchor='w').pack(side='left')
        ttk.Entry(row_out, textvariable=out_var).pack(side='left', fill='x', expand=True, padx=(4, 4))
        ttk.Button(row_out, text="浏览...", width=8,
                   command=lambda: _browse_save(out_var, "选择可视化输出",
                                                *_def_ext_for_format(fmt_var.get().strip().lower(), False))).pack(side='left')

        selected_files = (self.app.helpers.get_selected_file_info()
                          if hasattr(self.app.helpers, 'get_selected_file_info') else [])
        if selected_files:
            work = self.controller.model.work_dir
            cands = [s for s in selected_files if Path(s['name']).suffix.lower() in ('.xyz', '.mol', '.sdf', '.mol2')]
            mid = len(cands) // 2
            for s in cands[:max(1, mid)]:
                try:
                    self._ra_add_unique_path(r_list, str(work / s['name']))
                except Exception:
                    pass
            for s in cands[max(1, mid):]:
                try:
                    self._ra_add_unique_path(p_list, str(work / s['name']))
                except Exception:
                    pass

        def _start():
            reactants = [r_list.get(i) for i in range(r_list.size())]
            products = [p_list.get(i) for i in range(p_list.size())]
            out = out_var.get().strip()
            traj = traj_var.get().strip() if gen_traj_var.get() else ""
            if len(reactants) == 0:
                messagebox.showwarning("提示", "请至少添加 1 个反应物文件", parent=dialog); return
            if len(products) == 0:
                messagebox.showwarning("提示", "请至少添加 1 个产物文件", parent=dialog); return
            for f in reactants + products:
                if not Path(f).exists():
                    messagebox.showwarning("提示", f"文件不存在: {f}", parent=dialog); return
            if not out and not traj:
                messagebox.showwarning("提示", "请至少选择：可视化输出 或 IQmol 轨迹输出", parent=dialog); return
            mode_s = mode_var.get().strip().lower()
            mode = "forward" if mode_s.startswith("forward") else "bounce"
            fmt_s = fmt_var.get().strip().lower()
            fmt = "mp4" if fmt_s.startswith("mp4") else (
                "png_dir" if fmt_s.startswith("png_dir") else (
                    "none" if fmt_s.startswith("none") else "gif"))
            res_s = res_var.get().strip().lower()
            resolution = "sd" if res_s.startswith("sd") else ("fullhd" if res_s.startswith("fullhd") else "hd")
            csv_path = csv_var.get().strip() or None
            traj_fmt = "sdf" if traj_fmt_var.get().strip().lower().startswith("sdf") else "xyz"
            spacing = float(spacing_var.get())

            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                msgs: list[str] = []
                viz_ok = traj_ok = False
                viz_out = traj_out = None

                if fmt != "none" and out:
                    if progress_cb:
                        progress_cb(0, "开始生成可视化动画")
                    if len(reactants) == 1 and len(products) == 1:
                        r = ra.generate_reaction_animation(
                            reactants[0], products[0], out,
                            steps=max(2, int(steps_var.get())),
                            mode=mode, smooth=bool(smooth_var.get()),
                            fmt=fmt, resolution=resolution,
                            energy_csv=csv_path,
                            ffmpeg_path=ffmpeg_var.get().strip() or "ffmpeg",
                            fps=max(1, int(fps_var.get())),
                            progress_callback=progress_cb,
                        )
                    else:
                        import tempfile as _tf
                        from psi4_compute import _write_xyz
                        with _tf.TemporaryDirectory(prefix="ms_viz_") as _td:
                            _tdp = Path(_td)
                            _nR, _aR, _cR = ra._concat_xyz_files(reactants, translate_spacing=spacing)
                            _nP, _aP, _cP = ra._concat_xyz_files(products, translate_spacing=spacing)
                            try:
                                _aP2, _cP2 = ra._auto_reorder_atoms(_aR, _cR, _aP, _cP)
                            except Exception as _e:
                                msgs.append("❌ 可视化（反应物/产物）原子对齐失败: " + str(_e))
                                r = {"success": False, "error": "原子对齐失败"}
                                _aP2, _cP2 = _aP, _cP
                            else:
                                _rx = _tdp / "R.xyz"; _px = _tdp / "P.xyz"
                                _rx.write_text(_write_xyz(_nR, _aR, _cR), encoding="utf-8")
                                _px.write_text(_write_xyz(_nP, _aP2, _cP2), encoding="utf-8")
                                r = ra.generate_reaction_animation(
                                    str(_rx), str(_px), out,
                                    steps=max(2, int(steps_var.get())),
                                    mode=mode, smooth=bool(smooth_var.get()),
                                    fmt=fmt, resolution=resolution,
                                    energy_csv=csv_path,
                                    ffmpeg_path=ffmpeg_var.get().strip() or "ffmpeg",
                                    fps=max(1, int(fps_var.get())),
                                    progress_callback=progress_cb,
                                )
                    viz_ok = bool(r.get("success"))
                    viz_out = r.get("output")
                    if viz_ok:
                        msgs.append(f"✅ 可视化: {viz_out} （{r.get('n_frames')} 帧）")
                    else:
                        msgs.append("❌ 可视化: " + (r.get("error") or "未知错误"))
                        if r.get("frames_dir"):
                            msgs.append("   帧目录已保留: " + r["frames_dir"])

                if traj:
                    if progress_cb:
                        progress_cb(0, "开始生成 IQmol 轨迹")
                    if len(reactants) == 1 and len(products) == 1:
                        rr = ra.generate_xyz_trajectory(
                            reactants[0], products[0], traj,
                            steps=max(2, int(steps_var.get())),
                            mode=mode, smooth=bool(smooth_var.get()),
                            trajectory_format=traj_fmt,
                            energy_csv=csv_path,
                            progress_callback=progress_cb,
                        )
                    else:
                        rr = ra.generate_reaction_multispecies(
                            reactants, products, traj,
                            steps=max(2, int(steps_var.get())),
                            mode=mode, smooth=bool(smooth_var.get()),
                            trajectory_format=traj_fmt,
                            energy_csv=csv_path,
                            translate_spacing=spacing,
                            progress_callback=progress_cb,
                        )
                    traj_ok = bool(rr.get("success"))
                    traj_out = rr.get("output")
                    if traj_ok:
                        tag = "（含每帧能量 E）" if rr.get("energies_written") else ""
                        msgs.append(f"✅ IQmol 轨迹: {traj_out} （{rr.get('n_frames')} 帧） {tag}")
                    else:
                        msgs.append("❌ IQmol 轨迹: " + (rr.get("error") or "未知错误"))

                def _after():
                    any_ok = viz_ok or traj_ok
                    body = "\n".join(msgs)
                    if auto_open_iqmol_var.get() and traj_ok and traj_out:
                        try:
                            exe = iqmol_path_var.get().strip() or "IQmol"
                            resolved = Dialogs._resolve_iqmol_exe(exe)
                            _sp.Popen([resolved, str(traj_out)])
                            body += "\n\n🚀 已用 IQmol 打开轨迹"
                        except Exception as e:
                            body += f"\n\n⚠️  未能打开 IQmol: {e}"
                    if any_ok:
                        messagebox.showinfo("完成", body, parent=dialog)
                        self.controller.scan_files()
                    else:
                        messagebox.showerror("失败", body or "未产生任何产出", parent=dialog)
                self.app.after(0, _after)

            dialog.withdraw()
            self.app.helpers.run_task(_task)

        btn_row = ttk.Frame(dialog); btn_row.pack(fill='x', padx=12, pady=(12, 12))
        ttk.Button(btn_row, text="🎬 开始生成动画 / 轨迹", command=_start).pack(side='right', padx=4)
        def _safe_close():
            _cb = getattr(dialog, "_orig_close_ra_tpl_", None)
            if callable(_cb):
                _cb()
            else:
                dialog.destroy()
        ttk.Button(btn_row, text="关闭", command=_safe_close).pack(side='right', padx=4)

    def _ra_add_unique_path(self, listbox, path):
        path = str(path)
        for i in range(listbox.size()):
            if listbox.get(i) == path:
                return
        listbox.insert(tk.END, path)

    def _ra_delete_selected(self, listbox):
        for i in reversed(list(listbox.curselection())):
            listbox.delete(i)

    def show_history_dialog(self):
        dialog = tk.Toplevel(self.app)
        dialog.title("📜 历史记录可视化面板")
        dialog.geometry("800x500")
        dialog.transient(self.app)
        dialog.grab_set()

        top_btn_frame = ttk.Frame(dialog)
        top_btn_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(top_btn_frame, text="🔄 刷新", command=lambda: self._refresh_history_lists(undo_listbox, redo_listbox)).pack(side=tk.LEFT)

        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_frame = ttk.LabelFrame(main_frame, text="↩️ 撤销栈 (Undo Stack)", padding="5")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        undo_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL)
        undo_listbox = tk.Listbox(left_frame, yscrollcommand=undo_scroll.set, font=('Consolas', 9))
        undo_scroll.config(command=undo_listbox.yview)
        undo_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        undo_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_frame = ttk.LabelFrame(main_frame, text="↪️ 重做栈 (Redo Stack)", padding="5")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        redo_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL)
        redo_listbox = tk.Listbox(right_frame, yscrollcommand=redo_scroll.set, font=('Consolas', 9))
        redo_scroll.config(command=redo_listbox.yview)
        redo_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        redo_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        bottom_btn_frame = ttk.Frame(dialog)
        bottom_btn_frame.pack(fill=tk.X, padx=10, pady=10)

        def do_undo_one():
            self.controller.undo_last()
            self._refresh_history_lists(undo_listbox, redo_listbox)

        def do_redo_one():
            self.controller.redo_last()
            self._refresh_history_lists(undo_listbox, redo_listbox)

        def do_undo_until_selected():
            sel = undo_listbox.curselection()
            if not sel:
                return
            target_idx = sel[0]
            result = self.controller.model.undo_until(target_idx)
            self.app.helpers.on_log(
                f"⏮️ 批量撤销完成: {result['steps']} 步，成功 {result['total_success']}，失败 {result['total_error']}",
                'info' if result['total_error'] == 0 else 'warning'
            )
            self.controller.scan_files()
            self._refresh_history_lists(undo_listbox, redo_listbox)

        ttk.Button(bottom_btn_frame, text="↩️ 撤销 1 步", command=do_undo_one).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_btn_frame, text="↪️ 重做 1 步", command=do_redo_one).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_btn_frame, text="⏮️ 回滚到选中项", command=do_undo_until_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        self._refresh_history_lists(undo_listbox, redo_listbox)

    def _refresh_history_lists(self, undo_listbox, redo_listbox):
        undo_listbox.delete(0, tk.END)
        redo_listbox.delete(0, tk.END)
        history_snap = self.controller.model.get_history_snapshot()
        redo_snap = self.controller.model.get_redo_snapshot()
        for item in history_snap:
            undo_listbox.insert(tk.END, f"[{item['idx']}] {item['description']} ({item['file_count']} 文件)")
        for item in redo_snap:
            redo_listbox.insert(tk.END, f"[{item['idx']}] {item['description']} ({item['file_count']} 文件)")

    def show_results_browser_dialog(self):
        dialog = tk.Toplevel(self.app)
        dialog.title("📊 计算结果浏览")
        dialog.geometry("900x620")
        dialog.transient(self.app)
        dialog.grab_set()

        all_rows = []
        current_columns = ["base", "task_type", "method", "basis", "energy_Ha", "success", "log", "fchk", "opt_xyz"]

        top_btn_frame = ttk.Frame(dialog)
        top_btn_frame.pack(fill=tk.X, padx=10, pady=8)

        def refresh_tree():
            nonlocal all_rows, current_columns
            for item in tree.get_children():
                tree.delete(item)
            rows = self.controller.model.collect_results()
            all_rows = rows
            extra_keys = set()
            for r in rows:
                for k in r.keys():
                    if k not in current_columns:
                        extra_keys.add(k)
            display_cols = list(current_columns)
            for ek in sorted(extra_keys):
                if ek not in display_cols:
                    display_cols.append(ek)
            current_columns = display_cols
            tree["columns"] = display_cols
            for col in display_cols:
                if col == "base":
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=140, anchor=tk.W, stretch=False)
                elif col == "task_type":
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=80, anchor=tk.W, stretch=False)
                elif col == "method":
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=80, anchor=tk.W, stretch=False)
                elif col == "basis":
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=80, anchor=tk.W, stretch=False)
                elif col == "energy_Ha":
                    tree.heading(col, text=col, anchor=tk.E)
                    tree.column(col, width=110, anchor=tk.E, stretch=False)
                elif col == "success":
                    tree.heading(col, text=col, anchor=tk.CENTER)
                    tree.column(col, width=60, anchor=tk.CENTER, stretch=False)
                elif col in ("log", "fchk", "opt_xyz", "summary"):
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=260, anchor=tk.W, stretch=False)
                else:
                    tree.heading(col, text=col, anchor=tk.W)
                    tree.column(col, width=120, anchor=tk.W, stretch=False)
            for r in rows:
                vals = []
                for col in display_cols:
                    v = r.get(col, "")
                    if col == "success":
                        vals.append("✅" if v else "❌")
                    elif col == "energy_Ha" and v is not None:
                        try:
                            vals.append(f"{float(v):.8f}")
                        except (TypeError, ValueError):
                            vals.append(str(v))
                    else:
                        vals.append(str(v) if v is not None else "")
                tree.insert("", tk.END, values=vals)

        btn_refresh = ttk.Button(top_btn_frame, text="🔄 刷新结果", command=refresh_tree)
        btn_refresh.pack(side=tk.LEFT, padx=5)

        def export_selected_csv():
            sel_ids = tree.selection()
            if not sel_ids:
                messagebox.showwarning("提示", "请先在表格中选中要导出的行", parent=dialog)
                return
            out_path = filedialog.asksaveasfilename(
                initialdir=str(self.controller.model.work_dir),
                initialfile="results_selected.csv",
                filetypes=[("CSV", "*.csv")],
                defaultextension=".csv",
                parent=dialog
            )
            if not out_path:
                return
            col_order = list(tree["columns"])
            try:
                with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(col_order)
                    for iid in sel_ids:
                        vals = tree.item(iid, "values")
                        writer.writerow(list(vals))
                self.app.helpers.on_log(f"💾 选中行 CSV 已导出: {os.path.basename(out_path)}（{len(sel_ids)} 行）", 'success')
                messagebox.showinfo("导出成功", f"已导出 {len(sel_ids)} 行到：\n{out_path}", parent=dialog)
            except Exception as e:
                self.app.helpers.on_log(f"❌ CSV 导出失败: {e}", 'error')
                messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)

        btn_export_csv = ttk.Button(top_btn_frame, text="💾 导出选中行 CSV", command=export_selected_csv)
        btn_export_csv.pack(side=tk.LEFT, padx=5)

        tree_frame = ttk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        tree = ttk.Treeview(
            tree_frame,
            columns=current_columns,
            show="headings",
            selectmode=tk.EXTENDED,
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set
        )
        v_scroll.config(command=tree.yview)
        h_scroll.config(command=tree.xview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for col in current_columns:
            if col == "base":
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=140, anchor=tk.W, stretch=False)
            elif col == "task_type":
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=80, anchor=tk.W, stretch=False)
            elif col == "method":
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=80, anchor=tk.W, stretch=False)
            elif col == "basis":
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=80, anchor=tk.W, stretch=False)
            elif col == "energy_Ha":
                tree.heading(col, text=col, anchor=tk.E)
                tree.column(col, width=110, anchor=tk.E, stretch=False)
            elif col == "success":
                tree.heading(col, text=col, anchor=tk.CENTER)
                tree.column(col, width=60, anchor=tk.CENTER, stretch=False)
            elif col in ("log", "fchk", "opt_xyz"):
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=260, anchor=tk.W, stretch=False)
            else:
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, width=120, anchor=tk.W, stretch=False)

        def on_double_click(event):
            item = tree.identify_row(event.y)
            if not item:
                return
            col_idx = tree.identify_column(event.x)
            try:
                col_num = int(col_idx.replace("#", "")) - 1
            except (ValueError, TypeError):
                return
            cols = list(tree["columns"])
            if col_num < 0 or col_num >= len(cols):
                return
            col_name = cols[col_num]
            vals = tree.item(item, "values")
            if col_name == "log":
                log_path = vals[col_num] if col_num < len(vals) else ""
            else:
                log_idx = None
                for i, c in enumerate(cols):
                    if c == "log":
                        log_idx = i
                        break
                log_path = vals[log_idx] if log_idx is not None and log_idx < len(vals) else ""
            if log_path and os.path.exists(log_path):
                try:
                    self._safe_open_file(log_path)
                except Exception as e:
                    messagebox.showerror("打开失败", f"无法打开文件：{e}", parent=dialog)

        tree.bind("<Double-Button-1>", on_double_click)

        delta_frame = ttk.LabelFrame(dialog, text="ΔE 差值计算", padding="8")
        delta_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        op_row = ttk.Frame(delta_frame)
        op_row.pack(fill=tk.X, pady=2)

        ttk.Label(op_row, text="运算模式:").pack(side=tk.LEFT, padx=5)
        op_var = tk.StringVar(value="A-B（单分子差）")
        op_combo = ttk.Combobox(
            op_row,
            textvariable=op_var,
            values=["A-B（单分子差）", "C - A - B（反应/结合能）"],
            state="readonly",
            width=28
        )
        op_combo.pack(side=tk.LEFT, padx=5)

        hint_label = ttk.Label(
            op_row,
            text="用鼠标在上方表格选中 2~3 行，再点下方按钮。C 为第 1 个选中项。",
            foreground="gray"
        )
        hint_label.pack(side=tk.LEFT, padx=15)

        btn_row = ttk.Frame(delta_frame)
        btn_row.pack(fill=tk.X, pady=4)

        delta_text = scrolledtext.ScrolledText(delta_frame, height=8, wrap=tk.WORD, font=('Consolas', 9))
        delta_text.pack(fill=tk.BOTH, expand=True, pady=2)

        last_deltas = []

        def get_selected_rows():
            sel_ids = tree.selection()
            if not sel_ids:
                return []
            result = []
            for iid in sel_ids:
                vals = list(tree.item(iid, "values"))
                cols = list(tree["columns"])
                row_dict = {}
                for i, c in enumerate(cols):
                    if i < len(vals):
                        if c == "energy_Ha":
                            try:
                                row_dict[c] = float(vals[i])
                            except (TypeError, ValueError):
                                row_dict[c] = None
                        else:
                            row_dict[c] = vals[i]
                result.append(row_dict)
            return result

        def calc_deltas():
            nonlocal last_deltas
            sel_rows = get_selected_rows()
            if len(sel_rows) < 2:
                messagebox.showwarning("提示", "请在上方表格中至少选中 2 行", parent=dialog)
                return
            op = op_var.get()
            if op == "C - A - B（反应/结合能）" and len(sel_rows) < 3:
                messagebox.showwarning("提示", "C - A - B 模式需要至少选中 3 行", parent=dialog)
                return
            deltas = self.controller.model.compute_deltas(sel_rows, op)
            last_deltas = deltas
            delta_text.delete(1.0, tk.END)
            if not deltas:
                delta_text.insert(tk.END, "（无结果）\n")
                return
            for d in deltas:
                delta_text.insert(tk.END, f"公式 = {d.get('label', '')}\n")
                comment = d.get('comment', '')
                if comment:
                    delta_text.insert(tk.END, f"  {comment}\n")
                delta_text.insert(tk.END, f"  Ha     = {d.get('delta_Ha', 0):.8f}\n")
                delta_text.insert(tk.END, f"  kJ/mol = {d.get('delta_kJ', 0):.4f}\n")
                delta_text.insert(tk.END, f"  kcal/mol = {d.get('delta_kcal', 0):.4f}\n")
                delta_text.insert(tk.END, "\n")

        def export_delta_csv():
            if not last_deltas:
                messagebox.showwarning("提示", "请先点击「📐 计算差值」生成结果", parent=dialog)
                return
            out_path = filedialog.asksaveasfilename(
                initialdir=str(self.controller.model.work_dir),
                initialfile="results_delta.csv",
                filetypes=[("CSV", "*.csv")],
                defaultextension=".csv",
                parent=dialog
            )
            if not out_path:
                return
            try:
                with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=["label", "delta_Ha", "delta_kJ", "delta_kcal", "comment"],
                        extrasaction="ignore"
                    )
                    writer.writeheader()
                    for d in last_deltas:
                        writer.writerow(d)
                self.app.helpers.on_log(f"💾 ΔE 差值 CSV 已导出: {os.path.basename(out_path)}（{len(last_deltas)} 条）", 'success')
                messagebox.showinfo("导出成功", f"已导出差值结果到：\n{out_path}", parent=dialog)
            except Exception as e:
                self.app.helpers.on_log(f"❌ 差值 CSV 导出失败: {e}", 'error')
                messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)

        btn_calc = ttk.Button(btn_row, text="📐 计算差值", command=calc_deltas)
        btn_calc.pack(side=tk.LEFT, padx=5)

        btn_delta_export = ttk.Button(btn_row, text="💾 导出差值 CSV", command=export_delta_csv)
        btn_delta_export.pack(side=tk.LEFT, padx=5)

        refresh_tree()

    def show_diff_sync_dialog(self):
        from datetime import datetime

        dialog = tk.Toplevel(self.app)
        dialog.title("⚖️ 两工作目录差异比较 + 一键同步")
        dialog.geometry("800x520")
        dialog.transient(self.app)
        dialog.grab_set()

        model = self.controller.model

        def _fmt_mtime(ns_val):
            try:
                sec = int(ns_val) // 1_000_000_000
                return datetime.fromtimestamp(sec).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return "-"

        def _sort_treeview_column(tv, col, idx, reverse):
            def _key(val):
                try:
                    return float(val)
                except Exception:
                    return val
            rows = [(tv.set(k, col), k) for k in tv.get_children("")]
            rows.sort(key=lambda r: _key(r[0]), reverse=reverse)
            for i, (_, k) in enumerate(rows):
                tv.move(k, "", i)
            tv.heading(col, command=lambda: _sort_treeview_column(tv, col, idx, not reverse))

        def _build_tree(parent, columns, diff_tab="only"):
            frame = ttk.Frame(parent)
            frame.pack(fill=tk.BOTH, expand=True)
            h_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL)
            v_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL)
            tv = ttk.Treeview(
                frame,
                columns=columns,
                show="headings",
                selectmode=tk.EXTENDED,
                yscrollcommand=v_scroll.set,
                xscrollcommand=h_scroll.set
            )
            v_scroll.config(command=tv.yview)
            h_scroll.config(command=tv.xview)
            v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
            tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            for i, col in enumerate(columns):
                tv.heading(col, text=col, command=lambda c=col, ii=i: _sort_treeview_column(tv, c, ii, False))
                if col in ("filename", "name"):
                    tv.column(col, width=260, anchor=tk.W, stretch=True)
                elif "size" in col.lower():
                    tv.column(col, width=110, anchor=tk.E, stretch=False)
                elif "mtime" in col.lower() or "time" in col.lower():
                    tv.column(col, width=160, anchor=tk.W, stretch=False)
                else:
                    tv.column(col, width=140, anchor=tk.W, stretch=True)
            return tv

        top_frame = ttk.Frame(dialog)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        default_wd = str(self.controller.model.work_dir)
        left_dir_var = tk.StringVar(value=default_wd)
        right_dir_var = tk.StringVar(value=default_wd)

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="📁 左目录：", width=10).pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=left_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row1, text="浏览", width=8, command=lambda: self.app.helpers.browse_dir(left_dir_var)).pack(side=tk.LEFT, padx=2)

        row2 = ttk.Frame(top_frame)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="📁 右目录：", width=10).pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=right_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row2, text="浏览", width=8, command=lambda: self.app.helpers.browse_dir(right_dir_var)).pack(side=tk.LEFT, padx=2)

        compare_btn = ttk.Button(top_frame, text="🔍 比较差异")
        compare_btn.pack(pady=8)

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        tab_left = ttk.Frame(notebook)
        tab_right = ttk.Frame(notebook)
        tab_diff = ttk.Frame(notebook)
        notebook.add(tab_left, text="仅在左")
        notebook.add(tab_right, text="仅在右")
        notebook.add(tab_diff, text="同名内容不同")

        tv_left = _build_tree(tab_left, ["filename", "size(bytes)", "mtime"])
        tv_right = _build_tree(tab_right, ["filename", "size(bytes)", "mtime"])
        tv_diff = _build_tree(tab_diff, ["filename", "左_size", "左_mtime", "右_size", "右_mtime"])

        def _fill_tree(tv, items, mode="only"):
            for item in tv.get_children():
                tv.delete(item)
            for row in items:
                if mode == "only":
                    vals = (row["name"], row["size"], _fmt_mtime(row["mtime"]))
                else:
                    vals = (
                        row["name"],
                        row["left_size"], _fmt_mtime(row["left_mtime"]),
                        row["right_size"], _fmt_mtime(row["right_mtime"])
                    )
                tv.insert("", tk.END, values=vals)

        def _do_compare():
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请先填写左右目录", parent=dialog)
                return
            result = model.compare_directories(left, right)
            _fill_tree(tv_left, result["only_left"], mode="only")
            _fill_tree(tv_right, result["only_right"], mode="only")
            _fill_tree(tv_diff, result["diff_content"], mode="diff")
            self.app.helpers.on_log(
                f"🔍 比较完成：仅左 {len(result['only_left'])}，仅右 {len(result['only_right'])}，差异 {len(result['diff_content'])}",
                'info'
            )

        compare_btn.config(command=_do_compare)

        def _get_selected_names(tv):
            names = []
            for iid in tv.selection():
                vals = tv.item(iid, "values")
                if vals:
                    names.append(vals[0])
            return names

        def _only_left_copy_right():
            names = _get_selected_names(tv_left)
            if not names:
                messagebox.showinfo("提示", "请先在「仅在左」Tab 选中要复制的项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_left_to_right(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        def _only_left_copy_from_right():
            names = _get_selected_names(tv_left)
            if not names:
                messagebox.showinfo("提示", "请先在「仅在左」Tab 选中项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_right_to_left(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        btn_left_frame = ttk.Frame(tab_left)
        btn_left_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_left_frame, text="➡️ 复制到对侧", command=_only_left_copy_right).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_left_frame, text="⬅️ 从对侧复制过来", command=_only_left_copy_from_right).pack(side=tk.LEFT, padx=8)

        def _only_right_copy_left():
            names = _get_selected_names(tv_right)
            if not names:
                messagebox.showinfo("提示", "请先在「仅在右」Tab 选中要复制的项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_right_to_left(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        def _only_right_copy_from_left():
            names = _get_selected_names(tv_right)
            if not names:
                messagebox.showinfo("提示", "请先在「仅在右」Tab 选中项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_left_to_right(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        btn_right_frame = ttk.Frame(tab_right)
        btn_right_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_right_frame, text="➡️ 复制到对侧", command=_only_right_copy_left).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_right_frame, text="⬅️ 从对侧复制过来", command=_only_right_copy_from_left).pack(side=tk.LEFT, padx=8)

        def _diff_copy_right():
            names = _get_selected_names(tv_diff)
            if not names:
                messagebox.showinfo("提示", "请先在「同名内容不同」Tab 选中项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_left_to_right(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        def _diff_copy_from_right():
            names = _get_selected_names(tv_diff)
            if not names:
                messagebox.showinfo("提示", "请先在「同名内容不同」Tab 选中项", parent=dialog)
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            if not left or not right:
                messagebox.showwarning("提示", "请填写左右目录", parent=dialog)
                return
            def task(**kwargs):
                model.copy_from_right_to_left(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        def _diff_overwrite_right():
            names = _get_selected_names(tv_diff)
            if not names:
                messagebox.showinfo("提示", "请先在「同名内容不同」Tab 选中项", parent=dialog)
                return
            if not messagebox.askyesno("确认覆盖", f"确定用左目录文件覆盖右目录中选中的 {len(names)} 个文件？", parent=dialog):
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            def task(**kwargs):
                model.sync_overwrite_left_to_right(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        def _diff_overwrite_left():
            names = _get_selected_names(tv_diff)
            if not names:
                messagebox.showinfo("提示", "请先在「同名内容不同」Tab 选中项", parent=dialog)
                return
            if not messagebox.askyesno("确认覆盖", f"确定用右目录文件覆盖左目录中选中的 {len(names)} 个文件？", parent=dialog):
                return
            left = left_dir_var.get().strip()
            right = right_dir_var.get().strip()
            def task(**kwargs):
                model.sync_overwrite_right_to_left(names, left, right)
                self.app.after(0, _do_compare)
                self.app.after(0, lambda: self.controller.scan_files())
            self.app.helpers.run_task(task)

        btn_diff_frame = ttk.Frame(tab_diff)
        btn_diff_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_diff_frame, text="➡️ 复制到对侧", command=_diff_copy_right).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_diff_frame, text="⬅️ 从对侧复制过来", command=_diff_copy_from_right).pack(side=tk.LEFT, padx=6)
        ttk.Separator(btn_diff_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(btn_diff_frame, text="🔁 用左覆盖右", command=_diff_overwrite_right).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_diff_frame, text="🔁 用右覆盖左", command=_diff_overwrite_left).pack(side=tk.LEFT, padx=6)

        bottom_frame = ttk.Frame(dialog)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        ttk.Button(bottom_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def show_mapping_editor_dialog(self):
        model = self.app.controller.model
        dialog = tk.Toplevel(self.app)
        dialog.title("📋 分子命名映射表编辑器")
        dialog.geometry("750x550")
        dialog.transient(self.app)
        dialog.grab_set()

        top_info = ttk.Label(
            dialog,
            text="提示：双击单元格即可编辑英文名 / 中文名；英文名不能为空。",
            foreground="blue"
        )
        top_info.pack(anchor=tk.W, padx=12, pady=(10, 4))

        btn_top = ttk.Frame(dialog)
        btn_top.pack(fill=tk.X, padx=10, pady=5)

        def _tv_sort_column(tv, col, reverse):
            rows = [(tv.set(k, col), k) for k in tv.get_children("")]
            rows.sort(key=lambda r: r[0], reverse=reverse)
            for i, (_, k) in enumerate(rows):
                tv.move(k, "", i)
            tv.heading(col, command=lambda: _tv_sort_column(tv, col, not reverse))

        def _add_row():
            new_iid = tv.insert("", tk.END, values=("NEW_ENGLISH_1", "中文名待填"))
            tv.selection_set(new_iid)
            tv.see(new_iid)

        def _del_selected():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("提示", "请先选中要删除的行", parent=dialog)
                return
            if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(sel)} 行映射？", parent=dialog):
                return
            for iid in reversed(sel):
                tv.delete(iid)

        def _on_double_click(event):
            rowid = tv.identify_row(event.y)
            col = tv.identify_column(event.x)
            if not rowid or not col:
                return
            try:
                col_idx = int(col.replace("#", "")) - 1
            except ValueError:
                return
            col_name = ("英文名", "中文名")[col_idx] if 0 <= col_idx < 2 else None
            if col_name is None:
                return
            current_vals = list(tv.item(rowid, "values"))
            if col_idx >= len(current_vals):
                current_vals.extend([""] * (col_idx + 1 - len(current_vals)))
            old_val = current_vals[col_idx]
            new_val = simpledialog.askstring(
                "编辑单元格",
                f"请输入新的{col_name}：",
                initialvalue=str(old_val),
                parent=dialog
            )
            if new_val is None:
                return
            new_val = new_val.strip()
            if col_idx == 0 and not new_val:
                messagebox.showwarning("提示", "英文名不能为空", parent=dialog)
                return
            current_vals[col_idx] = new_val
            tv.item(rowid, values=tuple(current_vals))

        def _save_mapping():
            rows = []
            invalid = False
            for iid in tv.get_children(""):
                vals = tv.item(iid, "values")
                if len(vals) < 2:
                    continue
                eng = str(vals[0]).strip()
                chn = str(vals[1]).strip() if len(vals) > 1 else ""
                if not eng:
                    invalid = True
                    continue
                rows.append((eng, chn))
            if invalid:
                messagebox.showwarning("提示", "存在英文名空的行，已自动跳过这些行", parent=dialog)
            new_dict = {}
            dup_eng = 0
            for eng, chn in rows:
                if eng in new_dict:
                    dup_eng += 1
                    continue
                new_dict[eng] = chn
            if not messagebox.askyesno(
                "确认保存",
                f"是否保存 {len(new_dict)} 条映射到工作目录下的「分子命名映射.json」？"
                + (f"\n（注意：有 {dup_eng} 条重复英文名已去重）" if dup_eng else ""),
                parent=dialog
            ):
                return
            out_path = Path(model.work_dir) / "分子命名映射.json"
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(new_dict, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败", f"写入文件失败：{e}", parent=dialog)
                self.app.helpers.on_log(f"❌ 保存映射表失败: {e}", "error")
                return
            model.set_mapping(new_dict)
            model.invalidate_scan_cache()
            self.app.helpers.on_log(f"💾 映射表已保存：{len(new_dict)} 条 → {out_path.name}", "success")
            messagebox.showinfo("保存成功", f"已保存 {len(new_dict)} 条映射到：\n{out_path}", parent=dialog)
            self.controller.scan_files()

        ttk.Button(btn_top, text="➕ 添加行", command=_add_row).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_top, text="🗑️ 删除选中行", command=_del_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_top, text="💾 保存到配置文件", command=_save_mapping).pack(side=tk.LEFT, padx=5)

        tree_frame = ttk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        tv = ttk.Treeview(
            tree_frame,
            columns=["英文名", "中文名"],
            show="headings",
            selectmode=tk.EXTENDED,
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set
        )
        v_scroll.config(command=tv.yview)
        h_scroll.config(command=tv.xview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tv.heading("英文名", text="英文名", command=lambda: _tv_sort_column(tv, "英文名", False))
        tv.heading("中文名", text="中文名", command=lambda: _tv_sort_column(tv, "中文名", False))
        tv.column("英文名", width=280, anchor=tk.W, stretch=True)
        tv.column("中文名", width=280, anchor=tk.W, stretch=True)
        tv.bind("<Double-Button-1>", _on_double_click)

        for eng in sorted(model.mapping.keys()):
            chn = model.mapping[eng]
            tv.insert("", tk.END, values=(eng, chn))

        bottom_frame = ttk.Frame(dialog)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 12))
        ttk.Button(bottom_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    # =========================================================
    # 🛠️ 高级工具箱（独立页面，集中所有高阶功能）
    # =========================================================
    def show_advanced_tools_dialog(self) -> None:
        app = self.app
        ctl = self.controller
        dialog = tk.Toplevel(app)
        dialog.title("🛠️  高级工具箱 / Advanced Tools")
        dialog.geometry("1080x760")
        dialog.transient(app)
        dialog.grab_set()

        # 顶部欢迎提示
        banner = tk.Frame(dialog, bg="#eef4ff", padx=12, pady=8)
        banner.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(
            banner,
            text="🛠️  高级工具箱｜Advanced Tools  （仅 PSI4 + OpenBabel 实现，无需额外依赖）",
            bg="#eef4ff", font=("Microsoft YaHei UI", 11, "bold"), fg="#19326a",
        ).pack(anchor=tk.W)
        tk.Label(
            banner,
            text="· 左侧选择文件（单选/多选），点击对应卡片按钮即可运行。\n"
                 "· 每个功能右上角都有 小问号「？」说明。",
            bg="#eef4ff", fg="#334", justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        nb = ttk.Notebook(dialog)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # ----- 通用组件：日志 Text + 进度条 -----
        bottom = tk.Frame(dialog)
        bottom.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
        progress_var = tk.DoubleVar(value=0.0)
        progress_bar = ttk.Progressbar(bottom, variable=progress_var, maximum=100)
        progress_bar.pack(fill=tk.X, pady=(0, 4))
        log_text = scrolledtext.ScrolledText(bottom, height=10, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                                              insertbackground="white", relief=tk.SOLID, borderwidth=1)
        log_text.pack(fill=tk.BOTH, expand=True)
        # 先把 3 种颜色 tag 一次性配置好（避免每次 _log 时重复 config）
        try:
            log_text.tag_configure("ok", foreground="#4ade80")
            log_text.tag_configure("warn", foreground="#fbbf24")
            log_text.tag_configure("err", foreground="#f87171")
        except Exception:
            pass

        # =====================================================================
        # L-5 修复：show_advanced_tools_dialog 日志出口统一化
        #
        # 原问题：高级工具自己用 `_log(msg)` 直接写 log_text，同时偶尔调用
        #         `logger.*` 输出到 default_logger；如果 default_logger 已挂
        #         GuiLogHandler，同一条消息会在「对话框 log_text」+「主窗口
        #         日志面板」都出现（视觉重复），也排障困难。
        #
        # 修复方案：
        #   1) 为高级工具对话框创建一个独立子 logger（adv_tools.<unique>），
        #      propagate=False：只被本对话框的 handler 处理，不会冒泡到
        #      default_logger，因此不会触发主窗口 GuiLogHandler 重复显示。
        #   2) 给这个子 logger 挂两个 handler：
        #        - 临时 TkTextHandler：写到对话框自己的 log_text（线程安全）
        #        - 默认继承 default logger 的 level/format（排障时文件或控制台仍能看到）
        #   3) 原 `_log(msg, tag)` 不再手动 insert 到 Text，而是直接调用
        #      logger.log()，真正「统一走 logger」。
        #   4) 对话框关闭时 detach 临时 handler，避免内存泄漏。
        # =====================================================================
        import logging as _logging
        import uuid as _uuid
        adv_logger_name = f"adv_tools.{_uuid.uuid4().hex[:8]}"
        adv_logger: _logging.Logger = _logging.getLogger(adv_logger_name)
        adv_logger.setLevel(_logging.DEBUG)
        adv_logger.propagate = False  # <—— 关键：不冒泡，避免双写主窗口

        # 子 logger 复用 default logger 已经挂好的「写文件 / 写 stderr」handler，
        # 保证排障能看到完整 log；不直接用 default_logger 本体避免 GuiLogHandler
        # 又给主窗口 log_text 塞一份。
        for _h in list(default_logger.handlers):
            try:
                handler_type_name = type(_h).__name__
            except Exception:
                handler_type_name = ""
            # 过滤掉 default_logger 上挂的 GUI handler（只写给主窗口的）
            if handler_type_name == "GuiLogHandler":
                continue
            try:
                adv_logger.addHandler(_h)
            except Exception:
                pass

        class _TkTextHandler(_logging.Handler):
            """把 adv_logger 的日志打到当前对话框自己的 log_text（非主窗口）。"""

            def __init__(self, app_ref, text_widget):
                super().__init__(_logging.DEBUG)
                try:
                    self._app_ref = weakref.ref(app_ref)
                except TypeError:
                    self._app_ref = lambda: app_ref
                self._text = text_widget

            def emit(self, record: _logging.LogRecord):
                msg: str = self.format(record)
                # 把 logging 级别翻译成对话框原来的 tag 名字（ok/warn/err/None）
                lv = record.levelno
                if lv >= _logging.ERROR:
                    tag = "err"
                elif lv >= _logging.WARNING:
                    tag = "warn"
                elif lv >= getattr(_logging, "SUCCESS", 25):
                    tag = "ok"
                else:
                    tag = None
                app_r = self._app_ref()
                if app_r is None:
                    return
                try:
                    if threading.current_thread() is threading.main_thread():
                        self._write(msg, tag)
                    else:
                        try:
                            app_r.after(0, lambda: self._write(msg, tag))
                        except Exception:
                            # app 已经 destroy / after 不可用：至少别吞掉
                            try:
                                print(msg)
                            except Exception:
                                pass
                except Exception:
                    pass

            def _write(self, text: str, tag: str | None) -> None:
                """只在主线程调用：真正写 Text、tag、滚动。"""
                try:
                    import datetime as _dt
                    ts = _dt.datetime.now().strftime("%H:%M:%S")
                except Exception:
                    ts = ""
                safe = text if text.endswith("\n") else text + "\n"
                block = f"[{ts}] {safe}"
                try:
                    if not self._text.winfo_exists():
                        return
                    state = self._text.cget("state")
                    was_disabled = str(state).lower() == "disabled"
                    if was_disabled:
                        self._text.configure(state="normal")
                    try:
                        if tag is None:
                            self._text.insert(tk.END, block)
                        else:
                            self._text.insert(tk.END, block, tag)
                        try:
                            if self._text.winfo_exists():
                                self._text.see(tk.END)
                        except Exception:
                            pass
                    finally:
                        try:
                            if self._text.winfo_exists() and was_disabled:
                                self._text.configure(state="disabled")
                        except Exception:
                            pass
                except Exception:
                    pass

        _text_handler = _TkTextHandler(app, log_text)
        try:
            # 保持和 default_logger 相同 formatter（通常带 level/module 但这里简单）
            from logger import default_logger as _dflt
            if _dflt.handlers:
                _fmt = getattr(_dflt.handlers[0], "formatter", None)
                if _fmt:
                    _text_handler.setFormatter(_fmt)
        except Exception:
            pass
        adv_logger.addHandler(_text_handler)

        # 关闭时一定卸 handler（子 logger 会 GC，但 handler 引用了 widget weakref 其实也 OK，多一道无坏处）
        def _cleanup_adv_logger_handlers() -> None:
            try:
                adv_logger.removeHandler(_text_handler)
                try:
                    _text_handler.close()
                except Exception:
                    pass
            except Exception:
                pass

        _old_dialog_destroy_func = dialog.destroy

        def _safe_dialog_destroy(*args, **kwargs):
            _cleanup_adv_logger_handlers()
            try:
                _old_dialog_destroy_func(*args, **kwargs)
            except Exception:
                pass

        dialog.destroy = _safe_dialog_destroy  # type: ignore[method-assign]
        dialog.protocol("WM_DELETE_WINDOW", _safe_dialog_destroy)  # type: ignore[arg-type]

        def _map_tag_to_level(tag: str | None) -> int:
            t = (tag or "").lower()
            if t in {"err", "error", "fail", "failed"}:
                return _logging.ERROR
            if t in {"warn", "warning", "skip"}:
                return _logging.WARNING
            if t in {"ok", "success", "done"}:
                # SUCCESS 是我们给 default logger 注册的自定义 level（25）
                return getattr(_logging, "SUCCESS", 25)
            if t in {"debug", "dbg"}:
                return _logging.DEBUG
            return _logging.INFO

        def _log(msg: str, tag: str | None = None) -> None:
            """
            L-5 修复后：任何线程都 OK。不再自己手写 Text 控件，**统一走 logger**。
            输出路径：adv_logger → _TkTextHandler → 对话框自己的 log_text（只这个框有）
                    → 同时复用 default_logger 的文件 / stderr handler。
            不会触发主窗口 GuiLogHandler（因为 propagate=False），避免重复显示。
            """
            try:
                level = _map_tag_to_level(tag)
                adv_logger.log(level, msg)
            except Exception:
                try:
                    print(f"[ADV_TOOLS] {msg}")
                except Exception:
                    pass

        def _do_set_progress_in_main(perc: float) -> None:
            try:
                progress_var.set(float(perc))
            except Exception:
                pass

        def _progress(perc: float, msg: str) -> None:
            """
            **线程安全** 的进度条写入。
              - progress_var.set() 一定 app.after(0) 回主线程。
              - msg 部分通过已安全的 _log() 写（_log 自己会判断线程）。
            """
            try:
                app.after(0, lambda p=float(perc): _do_set_progress_in_main(p))
            except Exception:
                pass
            if msg:
                # ⚠️ 用已安全的 _log()，而不是之前的 lambda: _log 再包一层 after（会重复调度，但也是安全）
                _log(f"⏳ {float(perc):>3.0f}%  {msg}")

        def _sel_path() -> str | None:
            files = app.helpers.get_selected_files()
            if not files:
                _log("⚠️ 请先在主界面左侧列表选中至少 1 个文件（按 Ctrl 多选）", "warn")
                return None
            return files[0]

        def _sel_paths() -> list[str]:
            files = app.helpers.get_selected_files()
            if not files:
                _log("⚠️ 请先在主界面左侧列表选中至少 1 个文件", "warn")
                return []
            return files

        def _open_dir_try(path: str | None) -> None:
            if not path or not os.path.exists(path):
                return
            p = path if os.path.isdir(path) else os.path.dirname(path)
            try:
                if os.name == "nt":
                    os.startfile(p)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", p])
                else:
                    subprocess.Popen(["xdg-open", p])
            except Exception as e:
                _log(f"打开目录失败：{e}", "warn")

        # ------------- 改用 TaskManager.run_async（共享全局线程池，不再手搓 Thread）-------------
        # 这个对话框内的所有 _work 函数都必须自己调用 _progress / _log 来写输出
        # （TaskManager 自带的 _progress_callback / _log 我们不直接对外暴露，
        #  因为高级工具已经有自己的专属 log_text 和 progress_var）
        from task_manager import TaskManager
        _tm = TaskManager(app, controller=None)

        def _submit_work(fn, *, on_done=None) -> None:
            """
            把 fn 提交到线程池。fn 本身是「0 参数」callable；如需参数，
            请在构造时用闭包/partial 绑定（和之前 _in_thread(fn, *args) 不同，这里避免传参歧义）。
            on_done(result) 会在主线程被调用。
            """
            def _on_ok(r) -> None:
                try:
                    if on_done is not None:
                        on_done(r)
                except Exception as _e_done:
                    _log(f"✖ 回调 on_done 异常：{_e_done}", "err")
                finally:
                    # 结束时统一把进度条重置到 0（在主线程）
                    try:
                        app.after(0, lambda: _do_set_progress_in_main(0.0))
                    except Exception:
                        pass

            def _on_err(err_msg: str) -> None:
                _log(f"✖ 后台任务失败：{err_msg}", "err")
                try:
                    app.after(0, lambda: _do_set_progress_in_main(0.0))
                except Exception:
                    pass

            _tm.run_async(
                _wrap_throwaway_task(fn),
                on_done=_on_ok,
                on_error=_on_err,
                on_progress=None,
            )

        def _wrap_throwaway_task(fn):
            """
            TaskManager.run_async 要求 func 必须能接收 _progress_callback / _log 两个关键字参数，
            但高级工具里的 _work 都是纯 0 参。这里包一层，把那两个 kwargs 丢掉，再调原 fn。
            """
            def _inner(*, _progress_callback=None, _log=None):
                return fn()
            return _inner

        def _help(title: str, body: str) -> None:
            messagebox.showinfo(title, body, parent=dialog)

        # ====================================================
        # Tab 1：🧪 OpenBabel 分子工具（纯 OB，无需 PSI4）
        # ====================================================
        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="🧪 分子工具（OB）")

        def _row(parent, title, desc, btn_text, help_text, cmd):
            frame = tk.LabelFrame(parent, text=title, padx=8, pady=6, font=("Microsoft YaHei UI", 10, "bold"),
                                  fg="#0f4c81")
            frame.pack(fill=tk.X, padx=6, pady=6)
            toprow = tk.Frame(frame)
            toprow.pack(fill=tk.X)
            tk.Label(toprow, text=desc, fg="#333", justify=tk.LEFT, wraplength=780).pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Button(toprow, text="？", width=2, relief=tk.GROOVE,
                      command=lambda: _help(title, help_text)).pack(side=tk.RIGHT, padx=4)
            tk.Button(frame, text=btn_text, width=28, bg="#2563eb", fg="white",
                      activebackground="#1d4ed8", command=cmd).pack(anchor=tk.W, pady=(4, 0))

        # A1-1: SMILES → 相似结构搜索（InChIKey 匹配主库）
        def _smiles_search():
            smi = simpledialog.askstring("SMILES 搜索",
                                         "输入要查找的 SMILES（例如 CC(=O)O）：",
                                         parent=dialog)
            if not smi:
                return
            _log(f"🔎 搜索 SMILES：{smi}")
            def _work():
                res = ob_utils.smiles_to_inchikey(smi)
                if not res.get("success"):
                    return ("err", res.get("message", "InChIKey 生成失败"))
                target_key = res["data"]["inchikey"]
                target_prefix = target_key.split("-")[0]
                _log(f"🧬 目标 InChIKey 前缀：{target_prefix}")
                # 批量算当前库所有 InChIKey
                files = app.helpers.get_all_files() or []
                _log(f"📚 计算 {len(files)} 个分子的 InChIKey...")
                batch = ob_utils.batch_inchikey([f["path"] for f in files])
                hits = []
                for entry in batch:
                    key = entry.get("inchikey") or ""
                    if not key:
                        continue
                    if key.startswith(target_prefix):
                        score = 2 if key == target_key else 1
                        hits.append((score, entry["path"], key))
                hits.sort(key=lambda x: (-x[0], x[1]))
                return ("ok", hits, target_key)
            def _done(r):
                if not r or r[0] == "err":
                    _log(f"✖ {r[1] if isinstance(r, tuple) else '失败'}", "err")
                    return
                _, hits, tkey = r
                if not hits:
                    _log(f"🙈 没有找到类似结构（目标：{tkey}）。")
                    return
                _log(f"🎯 命中 {len(hits)} 个相似结构（前 20 个）：", "ok")
                for i, (sc, p, k) in enumerate(hits[:20], 1):
                    tag = "ok" if sc == 2 else None
                    _log(f"   [{i}] {'精确' if sc == 2 else '前缀'}  {k}  {os.path.basename(p)}", tag)
            _submit_work(_work, on_done=_done)

        _row(tab1,
             "① SMILES 结构相似搜索",
             "输入一个 SMILES → 算出 InChIKey → 在当前分子库里按 InChIKey 前缀（构型前 14 位）命中相似结构。",
             "🔎 运行 SMILES 搜索",
             "用途：你只知道一个分子的名字，想在本地库里找同结构。\n"
             "算法：OBabel SMILES → InChIKey（27 位）。按前 14 位（骨架层）匹配 = 同连接性；完全一致 = 立体化学也相同。",
             _smiles_search)

        # A1-2: 手性标注 + 生成对映体
        def _chirality():
            fp = _sel_path()
            if not fp:
                return
            _log(f"🔍 手性分析：{os.path.basename(fp)}")
            def _work():
                res = ob_utils.analyze_chirality(fp)
                inv_out = os.path.join(os.path.dirname(fp),
                                        os.path.splitext(os.path.basename(fp))[0] + "_enantiomer.xyz")
                res2 = ob_utils.invert_enantiomer(fp, inv_out)
                return res, res2, inv_out
            def _done(r):
                chir_res, inv_res, inv_out = r
                if chir_res.get("success"):
                    d = chir_res["data"]
                    _log(f"   手性中心数：{d['n_chiral_centers']}")
                    for c in d["centers"]:
                        _log(f"     - 原子 idx {c['atom_idx']}  {c['symbol']}  构型: {c['rs_label']}")
                else:
                    _log(f"⚠ {chir_res.get('message','手性分析失败')}", "warn")
                if inv_res.get("success"):
                    _log(f"🧭 对映体已写入：{inv_out}  → 可在 IQmol 里直接对比", "ok")
                    ctl.scan_files()
                else:
                    _log(f"⚠ 对映体生成失败：{inv_res.get('message','')}", "warn")
            _submit_work(_work, on_done=_done)

        _row(tab1,
             "② 手性中心识别（R/S）+ 对映体生成",
             "自动列出所有手性中心并标注 R/S；一键生成镜像对映体，输出同目录下的 *_enantiomer.xyz。",
             "🔬 标注手性并生成对映体",
             "用途：你做出来的手性配体/催化剂要分清哪一个对映体，或想生成另一对映体结构跑过渡态。\n"
             "实现：OBabel OBStereoFacade 查 OBTetrahedralStereo → R/S 标记；OBMol Stereo 翻转为逆构型后输出 XYZ。",
             _chirality)

        # A1-3: pH 加氢
        def _ph_protonate():
            fp = _sel_path()
            if not fp:
                return
            ph_val = simpledialog.askfloat("pH 加氢",
                                           "请输入目标 pH（常用 7.4 生理 pH / 1.0 强酸 / 13.0 强碱）：",
                                           parent=dialog, minvalue=0.0, maxvalue=14.0, initialvalue=7.4)
            if ph_val is None:
                return
            out = os.path.join(os.path.dirname(fp),
                               os.path.splitext(os.path.basename(fp))[0] + f"_pH{ph_val:.1f}.xyz")
            _log(f"🧪 pH={ph_val:.1f} 加氢：{os.path.basename(fp)} → {os.path.basename(out)}")
            def _work():
                return ob_utils.protonate_ph(fp, out, ph=ph_val)
            def _done(r):
                if r.get("success"):
                    _log("✅ 完成。输出文件：" + r["data"]["output_path"], "ok")
                    ctl.scan_files()
                    try: _open_dir_try(r["data"]["output_path"])
                    except Exception: pass
                else:
                    _log("✖ 失败：" + r.get("message", ""), "err")
            _submit_work(_work, on_done=_done)

        _row(tab1,
             "③ pH 依赖质子化（-p）",
             "在指定 pH 下给分子加/去质子（例如 pH=7.4 生理条件、pH=1.0 强酸条件）。\n"
             "会正确把 COOH → COO⁻ / 胺 → 胺正离子 / 咪唑 → 质子化等。",
             "🧪 加氢到指定 pH",
             "用途：做 pKa / NMR / 反应预测时，初始结构要是「溶液里真实存在的质子化状态」，否则算出来不准。\n"
             "实现：OBabel -p <pH>（内置 pKa 规则库）。注意：对于金属配合物、特殊官能团需要人工核对。",
             _ph_protonate)

        # A1-4: SDF 拆分 + 合并
        def _split_sdf():
            files = _sel_paths()
            sdfs = [f for f in files if f.lower().endswith(".sdf")]
            if not sdfs:
                _log("⚠️ 请选中至少 1 个 .sdf 多分子文件", "warn")
                return
            out_all = []
            def _work():
                for s in sdfs:
                    outdir = os.path.join(os.path.dirname(s),
                                          os.path.splitext(os.path.basename(s))[0] + "_split")
                    r = ob_utils.split_multi_sdf(s, outdir)
                    out_all.append((s, r))
                return out_all
            def _done(rr):
                for s, r in rr:
                    if r.get("success"):
                        d = r["data"]
                        _log(f"✅ {os.path.basename(s)} → 拆分 {d['n_molecules']} 个文件到目录 {d['output_dir']}", "ok")
                        try: _open_dir_try(d["output_dir"])
                        except Exception: pass
                    else:
                        _log(f"✖ {os.path.basename(s)} 失败：{r.get('message','')}", "err")
                ctl.scan_files()
            _submit_work(_work, on_done=_done)

        def _merge_sdf():
            files = _sel_paths()
            if len(files) < 2:
                _log("⚠️ 请至少选中 2 个分子文件（可混合 xyz/mol/sdf 等）", "warn")
                return
            out = filedialog.asksaveasfilename(parent=dialog,
                defaultextension=".sdf", filetypes=[("SDF 多分子库", "*.sdf")],
                title="保存合并后的 SDF 到：",
                initialfile="library_merged.sdf")
            if not out:
                return
            _log(f"📚 合并 {len(files)} 个分子 → {os.path.basename(out)}")
            def _work():
                return ob_utils.merge_to_sdf(files, out)
            def _done(r):
                if r.get("success"):
                    _log(f"✅ 合并完成：共 {r['data']['n_molecules']} 个分子 → {r['data']['output_path']}", "ok")
                    try: _open_dir_try(r["data"]["output_path"])
                    except Exception: pass
                else:
                    _log("✖ 失败：" + r.get("message", ""), "err")
            _submit_work(_work, on_done=_done)

        frame_sdf = tk.LabelFrame(tab1, text="④ SDF 多分子文件 / 拆分 & 合并",
                                  padx=8, pady=6, font=("Microsoft YaHei UI", 10, "bold"), fg="#0f4c81")
        frame_sdf.pack(fill=tk.X, padx=6, pady=6)
        tk.Label(frame_sdf,
                 text="拆分：把一个大的 SDF（多构象 / 虚拟库 / ZINC 下载）拆成单个分子，方便逐一看。\n"
                      "合并：把多个 xyz / mol / sdf 合回一个 SDF，方便发文章或导入 KNIME。",
                 fg="#333", justify=tk.LEFT, wraplength=780).pack(anchor=tk.W)
        r_btns = tk.Frame(frame_sdf)
        r_btns.pack(anchor=tk.W, pady=(4, 0))
        tk.Button(r_btns, text="✂️ 拆分选中的 SDF", width=24, bg="#16a34a", fg="white",
                  command=_split_sdf).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(r_btns, text="📦 合并选中分子为 SDF 库", width=28, bg="#15803d", fg="white",
                  command=_merge_sdf).pack(side=tk.LEFT, padx=4)
        tk.Button(frame_sdf, text="？", relief=tk.GROOVE, width=2,
                  command=lambda: _help("SDF 拆分 / 合并",
                                        "拆分：逐分子写单个 xyz/sdf。\n合并：按选中顺序合并成一个多分子 SDF。\n"
                                        "OB 支持自动格式转换（xyz→sdf 是写入 OB mol block + 标题）。")
                  ).pack(anchor=tk.E)

        # A1-5: InChIKey 批量生成（把 MW/LogP/TPSA 之外再补一个 InChIKey 列）
        def _gen_inchikeys():
            files = _sel_paths()
            if not files:
                return
            _log(f"🔑 计算 {len(files)} 个 InChIKey...")
            def _work():
                return ob_utils.batch_inchikey(files)
            def _done(rr):
                n = 0
                for entry in rr:
                    k = entry.get("inchikey") or ""
                    if k:
                        p = entry["path"]
                        # 存进 model.mapping 的英文名做后缀？不需要，写个 CSV 更直观
                        n += 1
                csv_out = os.path.join(os.path.dirname(files[0]), "InChIKey_batch.csv")
                try:
                    with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
                        w = csv.writer(f)
                        w.writerow(["file", "inchikey", "smiles_if_avail", "formula_if_avail"])
                        for e in rr:
                            w.writerow([e.get("path",""), e.get("inchikey",""), e.get("smiles",""), e.get("formula","")])
                except Exception as e_csv:
                    _log(f"写 CSV 失败：{e_csv}", "warn")
                _log(f"✅ 已生成 InChIKey {n}/{len(files)}，CSV 已保存到 {os.path.basename(csv_out)}", "ok")
                try: _open_dir_try(csv_out)
                except Exception: pass
            _submit_work(_work, on_done=_done)

        _row(tab1,
             "⑤ 批量生成 InChIKey + CSV",
             "给所有选中分子生成 InChIKey（骨架层 + 立体层），并导出 CSV。",
             "🔑 批量算 InChIKey",
             "用途：多轮实验/不同电脑之间批量精确比对结构是否相同（比文件名靠谱得多）。",
             _gen_inchikeys)

        # ====================================================
        # Tab 2：🧮 波函数性质 + 构象 + 反应能扫描
        # ====================================================
        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="🧮 波函数 / 构象 / 扫描")

        # 常用 QM 参数小条（所有 QM 功能共用）
        def _qm_controls(parent_for_control):
            frm = tk.LabelFrame(parent_for_control, text="QM 参数（通用）", padx=6, pady=4, fg="#7c2d12")
            frm.pack(fill=tk.X, padx=6, pady=4)
            r1 = tk.Frame(frm); r1.pack(fill=tk.X)
            tk.Label(r1, text="泛函 Method：").pack(side=tk.LEFT)
            method_var = tk.StringVar(value="b3lyp")
            ttk.Combobox(r1, textvariable=method_var, width=14,
                         values=["b3lyp", "B3LYP-D3", "M06-2X", "M06-2X-D3", "wB97X-D", "PBE0", "HF", "MP2"]
                         ).pack(side=tk.LEFT, padx=4)
            tk.Label(r1, text="基组 Basis：").pack(side=tk.LEFT, padx=(8, 0))
            basis_var = tk.StringVar(value="6-31g*")
            ttk.Combobox(r1, textvariable=basis_var, width=14,
                         values=["6-31g*", "6-311g**", "def2-SVP", "def2-TZVP", "cc-pVDZ", "cc-pVTZ"]
                         ).pack(side=tk.LEFT, padx=4)
            tk.Label(r1, text="溶剂：").pack(side=tk.LEFT, padx=(8, 0))
            solv_var = tk.StringVar(value="（气相）")
            ttk.Combobox(r1, textvariable=solv_var, width=10,
                         values=["（气相）", "water", "methanol", "ethanol", "acetonitrile",
                                 "dichloromethane", "tetrahydrofuran", "toluene", "dimethyl sulfoxide"]
                         ).pack(side=tk.LEFT, padx=4)
            d3_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(r1, text="D3 色散校正", variable=d3_var).pack(side=tk.LEFT, padx=(8, 0))
            r2 = tk.Frame(frm); r2.pack(fill=tk.X, pady=(3, 0))
            tk.Label(r2, text="电荷：").pack(side=tk.LEFT)
            ch_var = tk.IntVar(value=0)
            ttk.Spinbox(r2, from_=-8, to=8, textvariable=ch_var, width=5).pack(side=tk.LEFT, padx=4)
            tk.Label(r2, text="多重度：").pack(side=tk.LEFT, padx=(8, 0))
            mul_var = tk.IntVar(value=1)
            ttk.Spinbox(r2, from_=1, to=11, textvariable=mul_var, width=5).pack(side=tk.LEFT, padx=4)
            tk.Label(r2, text="内存：").pack(side=tk.LEFT, padx=(8, 0))
            mem_var = tk.StringVar(value="4 GB")
            ttk.Combobox(r2, textvariable=mem_var, width=8,
                         values=["2 GB", "4 GB", "8 GB", "16 GB", "32 GB"]).pack(side=tk.LEFT, padx=4)
            return method_var, basis_var, solv_var, d3_var, ch_var, mul_var, mem_var

        mv, bv, sv, dv, chv, mulv, memv = _qm_controls(tab2)

        def _solvent_real() -> str | None:
            s = sv.get()
            if s and s != "（气相）":
                return s
            return None

        # P5: 构象搜索
        def _conf_search():
            import psi4_compute as _p4
            fp = _sel_path()
            if not fp:
                return
            n_tot = simpledialog.askinteger("构象搜索参数", "初始采样构象总数（默认 60）：",
                                            parent=dialog, minvalue=10, maxvalue=500, initialvalue=60)
            if n_tot is None: return
            top_n = simpledialog.askinteger("构象搜索参数", f"取 MMFF94 能量最低 Top-N 做 DFT 精修：",
                                            parent=dialog, minvalue=1, maxvalue=50, initialvalue=5)
            if top_n is None: return
            hp = messagebox.askyesno("继续精修？", f"Top {top_n} 个构象是否接着跑 PSI4 {mv.get()}/{bv.get()} 几何精修 + 能量排序？\n"
                                     "（选否 = 只出 MMFF 排序/很快；选是 = 精度高但慢）",
                                     parent=dialog)
            out_dir = os.path.join(os.path.dirname(fp),
                                   os.path.splitext(os.path.basename(fp))[0] + "_conformers")
            _log(f"🦿 构象搜索：采样 {n_tot} → Top {top_n}{' + PSI4 精修' if hp else ''}")
            def _work():
                return _p4.conformer_search_ensemble(
                    fp, output_dir=out_dir,
                    n_confs_total=n_tot, top_n=top_n,
                    psi4_method=mv.get(), psi4_basis=bv.get(),
                    solvent=_solvent_real(), d3=dv.get(),
                    charge=chv.get(), multiplicity=mulv.get(),
                    memory=memv.get(), psi4_high_precision=hp,
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    _log(f"✅ 完成 → 输出目录：{r['output_dir']}", "ok")
                    if r.get("summary_csv"):
                        _log(f"   · 汇总 CSV：{os.path.basename(r['summary_csv'])}")
                    if r.get("ensemble_energy_png"):
                        _log(f"   · 相对能量棒图 PNG：{os.path.basename(r['ensemble_energy_png'])}")
                    try: _open_dir_try(r["output_dir"])
                    except Exception: pass
                else:
                    _log(f"✖ 失败：{r.get('error','')}", "err")
            _submit_work(_work, on_done=_done)

        _row(tab2,
             "① 构象搜索（OB Confab + MMFF94）→ 可选 PSI4 批量精修",
             "用 OB Confab 做系统转子搜索，MMFF94 排序取 Top-N；\n"
             "接着可选：把这 N 个用你指定的 QM 方法做几何优化 + 能量重新排序，出一张相对能量棒图。",
             "🦿 运行构象搜索",
             "为什么要做：很多分子（如药物、配体、二肽、天然产物）有 3 ~ 20 个低能构象，\n"
             "直接拿 1 个最低构象跑 NMR / pKa / TS 会错。先构象搜索可以避免假阳性。\n"
             "小提示：如果分子 < 6 个可旋转键 → TopN=5 就够；> 15 个键 → 调大 n_tot 到 200。",
             _conf_search)

        # P4: 二面角 scan（选 4 个原子序号）
        def _scan_dihedral():
            import psi4_compute as _p4
            fp = _sel_path()
            if not fp: return
            xyz = _p4.read_xyz_content(fp)
            if xyz is None:
                _log(f"✖ 无法读取 XYZ：{fp}", "err"); return
            try:
                n, syms, coords = _p4._parse_xyz(xyz)
            except Exception:
                _log("✖ XYZ 解析失败", "err"); return
            # 给用户一个预览表（前 30 个原子），再让输入 4 个 1-based 序号
            preview = "\n".join(f"  {i+1:>3d} {syms[i]:<3s}  "
                                 f"{coords[i][0]: .4f}  {coords[i][1]: .4f}  {coords[i][2]: .4f}"
                                 for i in range(min(n, 40)))
            info = (f"分子共 {n} 个原子（下表只列前 40 个），请按下面格式依次给"
                    "「第 1 个原子 - 第 2 个 - 第 3 个 - 第 4 个」的 1-based 序号（逗号分隔）：\n"
                    "（注意：标准 IUPAC 二面角定义是绕中间 2-3 键旋转的 1-2-3-4 角。）\n\n"
                    f"   idx  sym      X         Y         Z\n{preview}")
            raw = simpledialog.askstring("定义二面角", info, parent=dialog)
            if not raw: return
            try:
                parts = [int(x.strip()) for x in raw.replace("，", ",").split(",") if x.strip()]
                if len(parts) != 4:
                    raise ValueError("必须是 4 个整数")
                atoms = [max(1, min(n, i)) for i in parts]
            except Exception as e:
                _log(f"✖ 输入格式错误：{e}", "err")
                return
            d1 = simpledialog.askfloat("扫描范围", "起点角度（度，-180 到 180）：",
                                       parent=dialog, initialvalue=-180.0, minvalue=-180.0, maxvalue=180.0)
            if d1 is None: return
            d2 = simpledialog.askfloat("扫描范围", "终点角度（度，-180 到 180）：",
                                       parent=dialog, initialvalue=180.0, minvalue=-180.0, maxvalue=180.0)
            if d2 is None: return
            nstep = simpledialog.askinteger("步数", "扫描点数（≥ 6，默认 13）：",
                                            parent=dialog, minvalue=6, maxvalue=73, initialvalue=13)
            if nstep is None: return
            _log(f"📐 扫二面角 {atoms[0]}-{atoms[1]}-{atoms[2]}-{atoms[3]}  "
                 f"{d1}° → {d2}°  共 {nstep} 点")
            def _work():
                return _p4.run_rigid_scan(
                    fp, scan_atoms=atoms,
                    distance_range=(d1, d2, nstep),
                    method=mv.get(), basis=bv.get(),
                    solvent=_solvent_real(), d3=dv.get(),
                    charge=chv.get(), multiplicity=mulv.get(),
                    memory=memv.get(),
                    mode="dihedral",
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    _log(f"✅ 完成。能垒 ΔE† = {r.get('barrier_kcal_mol','?')} kcal/mol")
                    if r.get("scan_csv"): _log("   · CSV：" + os.path.basename(r["scan_csv"]))
                    if r.get("scan_png"): _log("   · PNG：" + os.path.basename(r["scan_png"]), "ok")
                    try: _open_dir_try(r.get("output_dir"))
                    except Exception: pass
                else:
                    _log("✖ 失败：" + r.get("error", ""), "err")
            _submit_work(_work, on_done=_done)

        _row(tab2,
             "② 二面角扫描 / 转动能垒（输入 4 个原子序号 + 扫描范围）",
             "绕中间 2-3 键刚性扫描 1-2-3-4 二面角，输出势能面 CSV + PNG，给出旋转能垒。",
             "📐 定义并运行二面角扫描",
             "用法：\n"
             "1. 先选中要扫的分子\n"
             "2. 点按钮 → 弹出原子序号表 → 抄下 4 个原子 1-based 序号，如 1, 2, 3, 6（逗号分隔）\n"
             "3. 输入起点/终点角度与步数。\n"
             "常见应用：联苯邻位阻碍旋转 → 得阻转异构 ΔG‡。",
             _scan_dihedral)

        # 额外工具：一键批量做 "电子性质 CSV"（HOMO/LUMO/Dipole/偶极）
        def _batch_properties():
            import psi4_compute as _p4
            files = _sel_paths()
            if not files:
                return
            if not messagebox.askyesno("确认", f"将要对 {len(files)} 个分子跑 {mv.get()}/{bv.get()} "
                                                 f"单点能 + oeprop 属性提取，可能耗时较久。是否继续？",
                                       parent=dialog):
                return
            out_csv = os.path.join(os.path.dirname(files[0]), "batch_properties.csv")
            rows: list[dict] = []
            def _work():
                for i, fp in enumerate(files, 1):
                    _progress(int(100 * (i - 1) / len(files)),
                              f"[{i}/{len(files)}] {os.path.basename(fp)}")
                    r = _p4.run_psi4_task(fp, "energy", mv.get(), bv.get(),
                                          solvent=_solvent_real(), d3=dv.get(),
                                          charge=chv.get(), multiplicity=mulv.get(),
                                          memory=memv.get())
                    rows.append({"path": fp, "res": r})
                return rows
            def _done(rr):
                import csv as _csv
                headers = ["file", "energy_Hartree", "HOMO_eV", "LUMO_eV", "Gap_eV",
                           "Dipole_total_D"]
                with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
                    w = _csv.writer(f); w.writerow(headers)
                    for e in rr:
                        r = e["res"]
                        p = r.get("properties") or {}
                        w.writerow([os.path.basename(e["path"]),
                                    f"{r.get('energy', float('nan')):.8f}" if isinstance(r.get("energy"), (int, float)) else "",
                                    f"{p.get('homo_ev', float('nan')):.3f}" if isinstance(p.get("homo_ev"), (int, float)) else "",
                                    f"{p.get('lumo_ev', float('nan')):.3f}" if isinstance(p.get("lumo_ev"), (int, float)) else "",
                                    f"{p.get('gap_ev', float('nan')):.3f}" if isinstance(p.get("gap_ev"), (int, float)) else "",
                                    f"{(p.get('dipole') or {}).get('total_D', float('nan')):.3f}" if isinstance(((p.get('dipole') or {}).get('total_D')), (int, float)) else "",
                                    ])
                _log(f"✅ 批量属性完成 → {out_csv}", "ok")
                try: _open_dir_try(out_csv)
                except Exception: pass
            _submit_work(_work, on_done=_done)

        _row(tab2,
             "③ 批量电子性质 / 排序表（HOMO / LUMO / GAP / 偶极）",
             "批量跑单点能 + oeprop 波函数分析，把 HOMO、LUMO、能隙、偶极矩汇总成一个 CSV。\n"
             "做分子筛选时你可以直接拖进 Excel 排序。",
             "📊 跑批量 HOMO/LUMO/Dipole",
             "用途：催化剂设计时筛一批配体的 LUMO 能级 / 药物类似物筛 GAP / NLO 分子筛偶极矩。",
             _batch_properties)

        # ====================================================
        # Tab 3：⚡ 过渡态 + 动力学（TS / IRC / Eyring）
        # ====================================================
        tab3 = ttk.Frame(nb)
        nb.add(tab3, text="⚡ TS / 动力学")
        mv3, bv3, sv3, dv3, chv3, mulv3, memv3 = _qm_controls(tab3)
        def _sol3():
            s = sv3.get(); return s if s and s != "（气相）" else None

        # P9: IRC 轨迹
        def _irc_run():
            import psi4_compute as _p4
            fp = _sel_path()
            if not fp: return
            if not messagebox.askyesno("确认 IRC",
                "即将对所选结构（应为 TS）跑 freq → IRC（forward+backward）。\n"
                "如果结构不是 TS，IRC 可能直接崩溃或收敛不到稳定点。是否继续？",
                parent=dialog):
                return
            out_pre = os.path.join(os.path.dirname(fp),
                                   os.path.splitext(os.path.basename(fp))[0] + "_irc")
            _log(f"🌀 IRC：{os.path.basename(fp)}")
            def _work():
                return _p4.run_irc_task(
                    fp, direction="both", method=mv3.get(), basis=bv3.get(),
                    output_prefix=out_pre,
                    solvent=_sol3(), d3=dv3.get(),
                    charge=chv3.get(), multiplicity=mulv3.get(), memory=memv3.get(),
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    _log(f"✅ IRC 完成 → TS freq energy = {r.get('freq_task', {}).get('energy'):.6f} Hartree")
                    if r.get("combined_trajectory_xyz"):
                        _log(f"   · 轨迹（IQmol 可直接打开）：{os.path.basename(r['combined_trajectory_xyz'])}", "ok")
                    n_f = len(r.get("forward_xyz_frames") or [])
                    n_b = len(r.get("backward_xyz_frames") or [])
                    _log(f"   · 帧数：forward {n_f} 帧，backward {n_b} 帧（若 PSI4 未编译 IRC driver 可能各 1 帧 = TS 自身）")
                    try: _open_dir_try(r.get("combined_trajectory_xyz") or os.path.dirname(out_pre))
                    except Exception: pass
                else:
                    _log(f"✖ IRC 失败：{r.get('error','')}", "err")
                progress_var.set(0)
            _submit_work(_work, on_done=_done)

        _row(tab3,
             "① IRC 最小能量路径 + 轨迹导出",
             "先跑 TS freq（算 Hessian 用于 IRC），再沿 forward/backward 跑 IRC；\n"
             "输出一整条 R → TS → P trajectory.xyz，可在 IQmol 中直接动画播放。",
             "🌀 运行 IRC 轨迹",
             "注意事项：\n"
             "1. 作为起点的 TS 结构要先收敛（OPT:TS + 有一个虚频）\n"
             "2. 如果你的 PSI4 是旧版、编译时没打开 IRC driver，IRC 步骤会静默跳过，\n"
             "   但 TS freq 依然能给你下一步 Eyring 要用的 ΔG。\n"
             "3. 失败/只有 TS 末端时，可手工用线性扫描做动画作为兜底。",
             _irc_run)

        # X1: R→TS→P 能垒图（ΔG‡f ΔG‡r + Eyring t1/2）
        def _profile_run():
            import psi4_compute as _p4
            files = _sel_paths()
            if len(files) != 3:
                _log("⚠️ 请按这个顺序选中 3 个分子：1 反应物 R   2 过渡态 TS   3 产物 P", "warn")
                return
            r_file, ts_file, p_file = files
            use_freq = messagebox.askyesno("热力学？",
                "是否为每个点再跑 freq 得到 Gibbs 自由能？\n"
                "（选「是」= 更准，慢 3~10 倍；选「否」= 直接用电子能差，很快。）",
                parent=dialog)
            t_val = simpledialog.askfloat("温度", "Eyring 公式使用的温度 T (K)：",
                                          parent=dialog, initialvalue=298.15, minvalue=1.0, maxvalue=5000.0)
            if t_val is None: return
            out_pre = os.path.join(os.path.dirname(r_file), "reaction_profile")
            _log(f"📈 R+TS+P → ΔG‡ 能量图（thermo={use_freq}  T={t_val:.2f}K）")
            def _work():
                return _p4.run_reaction_energy_profile(
                    r_file, ts_file, p_file,
                    method=mv3.get(), basis=bv3.get(), output_prefix=out_pre,
                    solvent=_sol3(), d3=dv3.get(),
                    charge=chv3.get(), multiplicity=mulv3.get(), memory=memv3.get(),
                    include_frequency=use_freq, T_K=t_val,
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    b = r["barriers"]
                    _log(f"✅ 完成：ΔG‡_fwd = {b['forward_dG_double_dagger_kcal']:.2f} kcal/mol   "
                         f"ΔG‡_rev = {b['reverse_dG_double_dagger_kcal']:.2f}   "
                         f"ΔG_r  = {b['reaction_dG_r_kcal']:+.2f}", "ok")
                    kf = r.get("kinetics_forward", {})
                    _log(f"   · Eyring 正反应：k_r = {kf.get('k_r_s-1','?'):.3g} s⁻¹   t₁/₂ ≈ {kf.get('t_half_pretty','?')}")
                    kr = r.get("kinetics_reverse", {})
                    _log(f"   · Eyring 逆反应：k_r = {kr.get('k_r_s-1','?'):.3g} s⁻¹   t₁/₂ ≈ {kr.get('t_half_pretty','?')}")
                    if r.get("summary_csv"): _log("   · CSV：" + os.path.basename(r["summary_csv"]))
                    if r.get("profile_png"):  _log("   · 台阶图 PNG：" + os.path.basename(r["profile_png"]), "ok")
                    try: _open_dir_try(r.get("summary_csv"))
                    except Exception: pass
                else:
                    _log("✖ 失败：" + r.get("error", ""), "err")
                progress_var.set(0)
            _submit_work(_work, on_done=_done)

        _row(tab3,
             "② R → TS → P 一键能垒图 + Eyring k(T) / t½",
             "输入 3 个结构（按顺序 R、TS、P） → 自动跑 optimize + freq → 读 Gibbs → 画台阶图；\n"
             "并自动用 Eyring 给出 k_f / k_r（速率常数）和 t₁/₂（半衰期，自动选 s/min/hr/day/yr 最合适）。",
             "📈 生成能垒图（ΔG‡+Eyring）",
             "用法：在主界面按 Ctrl 依次点 R → TS → P 三个文件 → 点按钮即可。\n"
             "结果文件：反应目录下 reaction_profile_profile.png / reaction_profile_profile.csv。\n"
             "小贴士：要做图上的 ΔG‡ 单位是 kcal/mol，1 kcal/mol 大约差半衰期 8 倍（室温）。",
             _profile_run)

        # P10: 独立 Eyring 计算器（已有 ΔG‡ 数值直接用）
        def _eyring_calc():
            import psi4_compute as _p4
            dg = simpledialog.askfloat("Eyring 计算器",
                                       "输入 ΔG‡（kcal/mol）：",
                                       parent=dialog, initialvalue=20.0, minvalue=0.0, maxvalue=100.0)
            if dg is None: return
            T = simpledialog.askfloat("Eyring 计算器",
                                      "温度 T（K，默认 298.15）：",
                                      parent=dialog, initialvalue=298.15, minvalue=1.0, maxvalue=5000.0)
            if T is None: return
            r = _p4.eyring_kinetics(delta_G_double_dagger_kcal=dg, T_K=T)
            _log(f"🧮 Eyring（ΔG‡={dg:.2f} kcal/mol, T={T:.2f} K）：", "ok")
            _log(f"   k_r  = {r['k_r_s-1']:.4g} s⁻¹")
            _log(f"   t₁/₂ = {r['t_half_pretty']}  "
                 f"(= {r['t_half_by_unit']['s']:.3g} s = {r['t_half_by_unit']['min']:.3g} min "
                 f"= {r['t_half_by_unit']['hr']:.3g} hr = {r['t_half_by_unit']['day']:.3g} day "
                 f"= {r['t_half_by_unit']['yr']:.3g} yr)")
        _row(tab3,
             "③ Eyring 公式独立计算器（直接输 ΔG‡ → k & t₁/₂）",
             "直接输入实验或计算得到的 ΔG‡（kcal/mol）→ 得到 T 下的速率常数 k 和半衰期 t₁/₂。",
             "🧮 直接算 Eyring k & t₁/₂",
             "快速参考（298 K 经验）：\n"
             " ΔG‡=15 kcal/mol → t½ ≈ 秒级（很快）\n"
             " ΔG‡=20 kcal/mol → t½ ≈ 小时级\n"
             " ΔG‡=25 kcal/mol → t½ ≈ 天级\n"
             " ΔG‡=30 kcal/mol → t½ ≈ 年级",
             _eyring_calc)

        # ====================================================
        # Tab 4：🔬 pKa / NMR 等预测（SMD / CPHF）
        # ====================================================
        tab4 = ttk.Frame(nb)
        nb.add(tab4, text="🔬 pKa / NMR")
        mv4, bv4, sv4, dv4, chv4, mulv4, memv4 = _qm_controls(tab4)

        def _sol4():
            s = sv4.get(); return s if s and s != "（气相）" else None

        # X2: pKa SMD 热力学循环（M06-2X/def2-TZVP 推荐默认）
        def _pka_run():
            import psi4_compute as _p4
            # 默认给一个高一点的级别，用户可以在 QM 控件里改
            if mv4.get() == "b3lyp":
                if messagebox.askyesno("pKa 精度建议",
                    "建议 pKa 用 M06-2X/def2-TZVP + SMD water + D3。\n"
                    "是否现在自动把当前 Method 改成 M06-2X、Basis 改成 def2-TZVP、溶剂 water？",
                    parent=dialog):
                    mv4.set("M06-2X")
                    bv4.set("def2-TZVP")
                    sv4.set("water")
                    dv4.set(True)
            files = _sel_paths()
            ha_file = None; am_file = None
            if len(files) == 1:
                ha_file = files[0]
            elif len(files) >= 2:
                if messagebox.askyesno("两个文件",
                    f"你选了 {len(files)} 个文件。是否按「第一个 = HA（中性酸）、第二个 = A⁻（共轭碱）」？\n"
                    "（选否 = 只把第一个当 HA，剩下的 A⁻ 用 pH=12 自动猜）", parent=dialog):
                    ha_file, am_file = files[0], files[1]
                else:
                    ha_file = files[0]
            if not ha_file: return
            t_val = simpledialog.askfloat("温度", "T (K)：", parent=dialog,
                                          initialvalue=298.15, minvalue=1.0, maxvalue=5000.0)
            if t_val is None: return
            solv = _sol4() or "water"
            out_pre = os.path.join(os.path.dirname(ha_file),
                                   os.path.splitext(os.path.basename(ha_file))[0] + "_pka")
            _log(f"⚗️ pKa SMD：HA={os.path.basename(ha_file)}  "
                 f"{'A⁻=' + os.path.basename(am_file) if am_file else 'A⁻=自动'}  "
                 f"溶剂={solv}  T={t_val:.2f}K")
            def _work():
                return _p4.run_pka_prediction(
                    ha_file, a_minus_file=am_file,
                    method=mv4.get(), basis=bv4.get(),
                    output_prefix=out_pre,
                    solvent_name=solv,
                    d3=dv4.get(), memory=memv4.get(),
                    T_K=t_val,
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    _log(f"✅ 预测 pKa ≈ {r['pKa_estimate']:.2f}   （± 2 经验，同系物排序更可靠）", "ok")
                    _log(f"   · dE_gas  = {r['deltaE_gas_kcal_mol']:.2f} kcal/mol")
                    _log(f"   · ΔG_sol(HA) = {r['solvation_kcal_mol']['HA']:.2f}   "
                         f"ΔG_sol(A⁻) = {r['solvation_kcal_mol']['A_minus']:.2f}")
                    _log(f"   · 备注：{r.get('note','')}")
                    if r.get("auto_generated_Aminus"):
                        _log(f"   ⚠ A⁻ 结构是自动用 OB -p 12 猜的，复杂官能团建议人工核对 A⁻ 文件", "warn")
                else:
                    _log(f"✖ pKa 失败：{r.get('error','')}", "err")
                progress_var.set(0)
            _submit_work(_work, on_done=_done)

        _row(tab4,
             "① pKa（SMD 热力学循环）预测",
             "M06-2X/def2-TZVP/SMD（默认推荐）分别跑 HA(gas/aq) 与 A⁻(gas/aq) 四个单点，\n"
             "代入热力学循环 + H⁺(aq) 经验自由能（-265.9 kcal/mol）→ 给出 pKa 数值。\n"
             "如果你只选 1 个 HA 文件，A⁻ 会用 OB -p 12 自动去质子化猜一个。",
             "⚗️ 运行 pKa 预测（HA → A⁻ + H⁺）",
             "典型用法：\n"
             "• 苯甲酸 → 预期 pKa ≈ 4.2\n"
             "• 苯酚 → 预期 pKa ≈ 10\n"
             "• 脂肪胺（如 Et₃N） → 预测的是 「Et₃NH⁺ → Et₃N + H⁺」，所以你 HA 应该传质子化的阳离子结构！\n"
             "精度：同系物内部相对排序非常稳定。绝对值通常 ±2，需用已知类似物再线性校正。",
             _pka_run)

        # X3: NMR Boltzmann 构象加权 ¹H NMR 谱图
        def _nmr_run():
            import psi4_compute as _p4
            fp = _sel_path()
            if not fp: return
            t_val = simpledialog.askfloat("温度", "Boltzmann 权重温度 T (K)：",
                                          parent=dialog, initialvalue=298.15, minvalue=1.0, maxvalue=5000.0)
            if t_val is None: return
            nconf_total = simpledialog.askinteger("构象数",
                "初始构象探索数量（一般 40 足够小药物）：",
                parent=dialog, minvalue=10, maxvalue=300, initialvalue=40)
            if nconf_total is None: return
            topn = simpledialog.askinteger("TopN 参与加权",
                "最终 Boltzmann 加权使用的 MMFF 最低能量构象数：",
                parent=dialog, minvalue=1, maxvalue=20, initialvalue=3)
            if topn is None: return
            if mv4.get().lower() == "mp2":
                messagebox.showwarning("级别提示",
                    "MP2 做 NMR CPHF 很慢，推荐 B3LYP/6-31G* 或 B3LYP/pcSseg-1。",
                    parent=dialog)
            out_dir = os.path.join(os.path.dirname(fp),
                                   os.path.splitext(os.path.basename(fp))[0] + "_nmr")
            _log(f"🧲 ¹H NMR：OB 构象 {nconf_total} → Top {topn} Boltzmann 加权  "
                 f"{mv4.get()}/{bv4.get()}  CPHF NMR 屏蔽常数")
            def _work():
                return _p4.run_nmr_simulation(
                    fp, output_dir=out_dir,
                    method=mv4.get(), basis=bv4.get(),
                    solvent=_sol4(), d3=dv4.get(),
                    charge=chv4.get(), multiplicity=mulv4.get(), memory=memv4.get(),
                    T_K=t_val, n_confs_total=nconf_total, top_n_confs=topn,
                    _progress_callback=_progress,
                )
            def _done(r):
                if r.get("success"):
                    nH = len(r.get("H_shifts_delta_ppm") or [])
                    _log(f"✅ 完成 → 共 {nH} 个 ¹H。输出目录：{out_dir}", "ok")
                    cw = r.get("conformer_weights") or []
                    for c in cw:
                        _log(f"   · 构象 {c['rank']:>2}  MMFFΔE = {c['rel_kcal']:+.2f} kcal/mol   "
                             f"Boltzmann 权重 = {c['w']*100:.1f}%")
                    if r.get("nmr_png"): _log("   · NMR 谱图 PNG：" + os.path.basename(r["nmr_png"]), "ok")
                    if r.get("nmr_csv"): _log("   · 每个 H 的 δ CSV：" + os.path.basename(r["nmr_csv"]))
                    try: _open_dir_try(out_dir)
                    except Exception: pass
                    if not any(s.startswith("CPHF") for s in (log_text.get("1.0", tk.END) or "").split()):
                        _log("   ⚠ 当前 PSI4 CPHF NMR 未成功（编译选项缺 cphf_tasks 模块）→ "
                             "本次使用经验 δ 近似，谱图只做定性参考。", "warn")
                else:
                    _log("✖ NMR 失败：" + r.get("error", ""), "err")
                progress_var.set(0)
            _submit_work(_work, on_done=_done)

        _row(tab4,
             "② Boltzmann 加权 ¹H NMR 谱模拟（CPHF NMR + 构象系综）",
             "1. OB Confab 做系统构象搜索；\n"
             "2. 对能量最低 Top-N 每个构象跑 PSI4 CPHF NMR 屏蔽常数 σ；\n"
             "3. 用 ΔE 算 Boltzmann 权重 → 每个 ¹H 平均 δ = σ_TMS − <σ>；\n"
             "4. 洛伦兹展宽（FWHM = 0.05 ppm）→ 出一张 0–12 ppm 谱图 PNG + CSV。",
             "🧲 模拟 ¹H NMR 谱图",
             "说明：\n"
             "如果你的 PSI4 没编译 CPHF NMR 模块（pip 版通常没有），本功能会自动退化为「经验化学位移 + 构象加权」，\n"
             "谱图依然能出一张图用于教学/报告，但请不要和实验谱做精细对比。\n"
             "学术对比请用 conda 版 PSI4 带 PSI4_ENABLE_CPHF=ON 重编译或用 ORCA/NWChem。",
             _nmr_run)

        # ----- 底部关闭 -----
        final_frame = tk.Frame(dialog)
        final_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(final_frame, text="关闭工具箱", command=dialog.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(final_frame, text="清空日志",
                   command=lambda: log_text.delete("1.0", tk.END)).pack(side=tk.RIGHT, padx=4)