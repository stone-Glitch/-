#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用对话框 - 文件类型选择、字体大小、环境诊断、OB路径设置、最近目录
"""
import csv
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, scrolledtext
from pathlib import Path

from logger import default_logger as logger
from constants import SUPPORTED_EXTS
from .base import _append_text, _clear_text, show_friendly_error, friendly_error
import openbabel_utils as ob_utils


# ===== 安全的外部工具路径解析 =====
def _resolve_iqmol_exe(name_or_path: str) -> str:
    """安全解析 IQmol 可执行文件绝对路径。"""
    import shutil as _shutil
    import tempfile as _tempfile

    def _safe_real(p: Path, *, display_name: str = "IQmol") -> Path:
        try:
            real = p.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"{display_name} 路径不存在或不可读: {p}") from exc
        if not real.is_file():
            raise RuntimeError(f"{display_name} 路径不是文件: {real}")
        unsafe_roots = []
        for _cand in (_tempfile.gettempdir(), os.getcwd(), os.path.expanduser("~")):
            try:
                unsafe_roots.append(Path(_cand).resolve(strict=False))
            except Exception:
                pass
        for root in unsafe_roots:
            try:
                real.relative_to(root)
                raise RuntimeError(
                    f"出于安全考虑，拒绝执行在可写目录下的 {display_name} 真实路径: {real}（父目录={root}）"
                )
            except ValueError:
                pass
        return real

    candidate = str(name_or_path).strip() or "IQmol"
    if os.sep in candidate or (os.altsep and os.altsep in candidate) or Path(candidate).is_absolute():
        abs_path = Path(candidate).expanduser()
        return str(_safe_real(abs_path, display_name="IQmol"))
    resolved = _shutil.which(candidate)
    if not resolved:
        raise RuntimeError(f"未在 PATH 中找到 IQmol（当前输入: {candidate!r}）")
    return str(_safe_real(Path(resolved), display_name="IQmol"))


def _safe_open_file(target: str) -> None:
    """用系统默认程序打开文件/文件夹。"""
    target_str = os.fspath(target)
    if sys.platform == "win32":
        os.startfile(target_str)
        return
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", target_str], check=False)
        return
    subprocess.run(["/usr/bin/xdg-open", target_str], check=False)


# ===== 文件类型选择 =====
def show_ext_filter_dialog(app, controller):
    dialog = tk.Toplevel(app)
    dialog.title("选择文件类型")
    dialog.geometry("350x300")
    dialog.transient(app)
    dialog.grab_set()

    all_exts = sorted(SUPPORTED_EXTS)
    current_exts = {e.strip() for e in app.ext_filter_var.get().split(',') if e.strip()}
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
            app.helpers.on_log("⚠️ 未选择任何文件类型，将显示所有支持的类型", 'warning')
            app.ext_filter_var.set("")
        else:
            app.ext_filter_var.set(",".join(selected))
        app.helpers.update_ext_display()
        controller.scan_files()
        dialog.destroy()

    def on_cancel():
        dialog.destroy()

    ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)


# ===== 最近工作目录 =====
def show_recent_dirs_dialog(app, controller):
    from config import save_config

    dialog = tk.Toplevel(app)
    dialog.title("📂 最近工作目录")
    dialog.geometry("650x450")
    dialog.transient(app)
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
        for d in controller.get_recent_work_dirs():
            listbox.insert(tk.END, d)

    def clear_history():
        app.config_data["recent_work_dirs"] = []
        save_config(app.config_data)
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
        controller.switch_recent_work_dir(idx)
        dialog.destroy()

    listbox.bind("<Double-Button-1>", lambda e: do_switch())

    ttk.Button(bottom_btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    ttk.Button(bottom_btn_frame, text="✅ 切换到此目录", command=do_switch).pack(side=tk.RIGHT, padx=5)


# ===== 字体大小设置 =====
def show_font_size_dialog(app, parent=None):
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
    BASE = F.get("BASE", ("Microsoft YaHei", 12))
    BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))
    SMALL = F.get("SMALL", ("Microsoft YaHei", 11))
    H1 = F.get("H1", ("Microsoft YaHei", 14, "bold"))

    try:
        cfg = getattr(app, "config_data", None)
        if not isinstance(cfg, dict):
            cfg = {}
        cur = int(cfg.get("font_size", 14) or 14)
    except Exception:
        cur = 14
    cur = max(8, min(24, cur))

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

    row = tk.Frame(main, bg="#EEF3FF")
    row.pack(fill=tk.X, pady=(0, 6))
    tk.Label(row, text="字号（pt）：", bg="#EEF3FF", fg="#1A2142",
             font=BOLD).pack(side=tk.LEFT)
    val_var = tk.IntVar(value=cur)
    spin = tk.Spinbox(row, from_=8, to=24, textvariable=val_var, width=4,
                      font=BOLD, justify="center", bd=2, relief=tk.SOLID,
                      bg="#FFFFFF", fg="#1A2142")
    spin.pack(side=tk.LEFT, padx=(6, 0))

    slider_row = tk.Frame(main, bg="#EEF3FF")
    slider_row.pack(fill=tk.X, pady=(4, 10))
    scale = tk.Scale(slider_row, from_=8, to=24, orient=tk.HORIZONTAL,
                     variable=val_var, showvalue=False,
                     font=SMALL, bg="#EEF3FF", fg="#1A2142")
    scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
    tk.Label(slider_row, textvariable=val_var, bg="#EEF3FF", fg="#3B6EFF",
             font=BOLD, width=3, anchor="center").pack(side=tk.LEFT, padx=(8, 0))

    prev = tk.LabelFrame(main, text="  🧿 实时预览（仅预览 Label/Button 字体）  ",
                         bg="#FFFFFF", fg="#1A2142", font=BOLD,
                         relief=tk.GROOVE, bd=2)
    prev.pack(fill=tk.BOTH, expand=True, pady=(4, 10))
    prev_inner = tk.Frame(prev, bg="#FFFFFF")
    prev_inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    preview_base_label = tk.Label(prev_inner,
                                  text="普通文字 Label：ABC 中文 English 123",
                                  bg="#FFFFFF", fg="#1A2142")
    preview_base_label.pack(anchor="w", pady=(0, 4))
    preview_bold_label = tk.Label(prev_inner,
                                  text="加粗文字 Label：粗体中文 / Bold English",
                                  bg="#FFFFFF", fg="#3B6EFF")
    preview_bold_label.pack(anchor="w", pady=(0, 6))
    preview_btn = tk.Button(prev_inner, text="示例按钮 Button", relief=tk.RAISED, bd=1,
                            bg="#DEE8FF", fg="#1A2142", cursor="hand2")
    preview_btn.pack(anchor="w", pady=(0, 4))
    preview_code = tk.Label(prev_inner,
                            text='Consolas 日志字体预览：log.info("hello world")',
                            bg="#F8FAFF", fg="#1A2142", relief=tk.SUNKEN, bd=1,
                            justify="left", anchor="w", padx=8, pady=4)
    preview_code.pack(anchor="w", fill=tk.X, pady=(2, 0))

    def _apply_preview(*_a):
        try:
            pt = int(val_var.get())
        except Exception:
            return
        pt = max(8, min(24, pt))
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

    btns = tk.Frame(main, bg="#EEF3FF")
    btns.pack(fill=tk.X, pady=(8, 0))

    def _save_and_maybe_restart():
        try:
            pt = int(val_var.get())
        except Exception:
            messagebox.showerror("错误", "请填写合法的整数字号（8~24）", parent=dialog)
            return
        pt = max(8, min(24, pt))
        val_var.set(pt)
        try:
            cfg = getattr(app, "config_data", None)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["font_size"] = pt
            app.config_data = cfg
        except Exception as _e1:
            logger.warning("写 font_size 到内存 config_data 失败：%s", _e1)
        try:
            from config import save_config
            save_config(app.config_data)
        except Exception as _e2:
            messagebox.showerror("保存失败", f"写入配置文件失败：\n{_e2}", parent=dialog)
            return
        try:
            from ui_builder import resolve_font_specs
            resolve_font_specs(app, force_pt=pt)
        except Exception as _e3:
            logger.debug("resolve_font_specs 热更新失败：%s", _e3)
        if messagebox.askyesno(
            "已保存 · 建议重启",
            f"字号已成功保存为 {pt} pt。\n\n新字号会在「下次启动」时完整生效。是否立即重启本程序？",
            parent=dialog,
        ):
            try:
                dialog.destroy()
            except Exception:
                pass
            try:
                _restart_app(app)
            except Exception as _rest_e:
                messagebox.showinfo("重启失败", f"自动重启失败，请手动关闭后重新打开：{_rest_e}", parent=parent)

    def _reset_default():
        val_var.set(14)

    ttk.Button(btns, text="↺ 恢复默认 14pt", command=_reset_default,
               style="Aurora.TButton").pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="取消", command=dialog.destroy,
               style="Aurora.TButton").pack(side=tk.RIGHT, padx=4)
    ttk.Button(btns, text="💾 保存并应用（建议重启）",
               command=_save_and_maybe_restart,
               style="Aurora.BigAccent.TButton").pack(side=tk.RIGHT, padx=4)


def _restart_app(app) -> None:
    """用当前 Python 解释器重跑当前主脚本。"""
    try:
        import subprocess as _sp
        argv0 = sys.argv[0] if sys.argv else os.path.abspath("main.py")
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
    try:
        try:
            app.on_close()
        except Exception:
            pass
        try:
            app.destroy()
        except Exception:
            pass
    finally:
        try:
            os._exit(0)
        except Exception:
            sys.exit(0)


# ===== 环境诊断 =====
def show_environment_dialog(app, parent=None, ob_details=None, psi4_details=None):
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
    BASE = F.get("BASE", ("Microsoft YaHei", 12))
    BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))
    SMALL = F.get("SMALL", ("Microsoft YaHei", 11))
    H1 = F.get("H1", ("Microsoft YaHei", 14, "bold"))

    main = tk.Frame(dialog, bg="#EEF3FF")
    main.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

    tk.Label(main, text="🧪  环境诊断（依赖与建议）",
             bg="#EEF3FF", fg="#1A2142",
             font=H1).pack(anchor="w", pady=(0, 6))
    tk.Label(main, text="如果某项为红色，可直接点击对应「修复」按钮尝试解决。",
             bg="#EEF3FF", fg="#6B7599",
             font=SMALL, justify="left").pack(anchor="w", pady=(0, 14))

    # OB 区
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
        bg="#F8FAFF", fg="#1A2142", wrap=tk.WORD, bd=1, relief=tk.SOLID
    )
    ob_diag_text.pack(fill=tk.X, padx=14, pady=(2, 10))
    ob_btn_row = tk.Frame(ob_card, bg="#FFFFFF")
    ob_btn_row.pack(fill=tk.X, padx=14, pady=(0, 14))

    # PSI4 区
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

    # 安装指引
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

    btns = tk.Frame(main, bg="#EEF3FF")
    btns.pack(fill=tk.X)

    def _fill_ob():
        try:
            ob_ok, ob_msg, det = ob_utils.check_openbabel()
            ob_status_var.set(("✅ 可用" if ob_ok else "❌ 不可用"))
            try:
                ob_status_lbl.configure(fg=("#0EA288" if ob_ok else "#E5484D"))
            except Exception:
                pass
            parts = [ob_msg]
            if det.get("resolved_cli_path"):
                parts.append(f"  CLI 路径：{det['resolved_cli_path']}")
            if det.get("pybel_version"):
                parts.append(f"  pybel 版本：{det['pybel_version']}")
            if det.get("cli_version"):
                parts.append(f"  CLI 版本：{det['cli_version']}")
            ob_text_var.set("\n".join(parts))
            diags = []
            for w in (det.get("warnings") or []):
                diags.append(f"[WARN]  {w}")
            for d in (det.get("diagnosis") or []):
                diags.append(f"[TIP]   {d}")
            if not diags:
                diags.append("[OK]   未发现异常。")
            ob_diag_text.configure(state="normal")
            ob_diag_text.delete("1.0", tk.END)
            ob_diag_text.insert(tk.END, "\n".join(diags))
            ob_diag_text.configure(state="disabled")
        except Exception as _oe:
            ob_status_var.set("⚠️ 检测失败")
            ob_text_var.set(str(_oe))

    def _fill_psi4():
        try:
            import psi4
            v = getattr(psi4, "__version__", None)
            psi_status_var.set("✅ 可用" if v else "✅ 可导入")
            psi_text_var.set(
                f"Python 包 psi4 已导入（版本 {v or '未声明'}）。\n"
                "如果运行任务失败，一般是内存不足、方法/基组不兼容或任务超时。"
            )
        except Exception as _pe:
            psi_status_var.set("⚠️ 未导入")
            psi_text_var.set(
                "未检测到 Python 包 psi4（不影响文件整理/OpenBabel 工具）。\n"
                "如需使用量化计算/刚性扫描/动画等能力，建议执行：\n"
                "    conda install -c conda-forge psi4 resp gcp-correction dftd4"
                f"\n详细错误：{_pe}"
            )

    def _open_manual_path():
        try:
            show_obabel_path_dialog(app, parent=dialog, on_saved_callback=_fill_ob)
        except Exception as _e:
            messagebox.showerror("打开失败", f"无法打开 OpenBabel 路径设置对话框：{_e}")

    ttk.Button(btns, text="🔁 重新检测", command=lambda: (_fill_ob(), _fill_psi4()),
               style="Aurora.Primary.TButton").pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="🧭 手动选择 obabel 路径…", command=_open_manual_path,
               style="Aurora.BigAccent.TButton").pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="关闭", command=dialog.destroy,
               style="Aurora.TButton").pack(side=tk.RIGHT, padx=4)

    try:
        dialog.after(80, _fill_ob)
        dialog.after(140, _fill_psi4)
    except Exception:
        _fill_ob()
        _fill_psi4()


# ===== OpenBabel 路径设置 =====
def show_obabel_path_dialog(app, parent=None, on_saved_callback=None):
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

    F = getattr(app, "_fonts", {})
    BASE = F.get("BASE", ("Microsoft YaHei", 12))
    BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))
    SMALL = F.get("SMALL", ("Microsoft YaHei", 11))

    main = tk.Frame(dialog, bg="#EEF3FF")
    main.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

    tk.Label(main, text="🧭  OpenBabel 可执行文件路径设置",
             bg="#EEF3FF", fg="#1A2142",
             font=F.get("H1", ("Microsoft YaHei", 14, "bold"))
             ).pack(anchor="w", pady=(0, 10))

    tip = ("如果自动找不到 obabel 命令行，可在这里手动选择它的可执行文件\n"
           "  Windows：obabel.exe（一般在 C:\\Program Files\\OpenBabel-3.1.1\\）\n"
           "  Linux/macOS：一般在 /usr/bin/obabel、~/anaconda3/bin/obabel")
    tk.Label(main, text=tip, bg="#EEF3FF", fg="#6B7599",
             font=SMALL, justify="left").pack(anchor="w", pady=(0, 12))

    row_cur = tk.Frame(main, bg="#EEF3FF")
    row_cur.pack(fill=tk.X, pady=(0, 8))
    tk.Label(row_cur, text="当前解析到的路径：", bg="#EEF3FF", fg="#1A2142",
             font=BOLD).pack(side=tk.LEFT)
    cur_var = tk.StringVar(value="(请先点「重新检测」)")
    cur_label = tk.Label(row_cur, textvariable=cur_var, bg="#FFFFFF", fg="#3B6EFF",
                         font=SMALL, relief=tk.SUNKEN, padx=8, pady=4, justify="left")
    cur_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

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

    ttk.Button(row_path, text="浏览…", command=_browse).pack(side=tk.LEFT, padx=(0, 2))

    def _auto():
        path_var.set("")
        _detect()

    ttk.Button(row_path, text="使用自动查找", command=_auto).pack(side=tk.LEFT, padx=2)

    result_var = tk.StringVar(value="")
    res_label = tk.Label(main, textvariable=result_var, bg="#EEF3FF", fg="#1A2142",
                         font=BASE, justify="left", anchor="w")
    res_label.pack(fill=tk.X, pady=(4, 8))

    def _detect():
        v = path_var.get().strip()
        if v:
            ob_utils.set_manual_obabel_path(v)
        else:
            ob_utils.set_manual_obabel_path(None)
        ok, msg, det = ob_utils.check_openbabel()
        cur_var.set(str(det.get("resolved_cli_path") or "(未解析到)")
                    + ("   （手动路径）" if det.get("manual_path_used") else "   （自动）"))
        result_var.set(("✅ " + msg) if ok else ("❌ " + msg))
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
        ob_utils.set_manual_obabel_path(v)
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
    ttk.Button(btns, text="🔍 重新检测", command=_detect).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="🧪 测试可用性", command=_test).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="💾 保存到配置", command=_save).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=4)

    try:
        dialog.after(50, _detect)
    except Exception:
        pass