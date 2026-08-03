#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分子分析对话框 - 分子式/元素分析、几何参数导出
"""
import os
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from logger import default_logger as logger
from task_manager import TaskManager
from .base import show_friendly_error
from .common import _safe_open_file
import openbabel_utils as ob_utils


def show_formula_dialog(app, controller):
    """分子式 & 元素分析弹窗"""
    sel = app.helpers.get_selected_filenames()
    if not sel:
        app.helpers.on_log("⚠️ 请先选择一个分子文件", "warning")
        return

    def _run(**_kw):
        import openbabel_utils as obu
        from pathlib import Path
        work = app.work_dir_var.get().strip()
        fp = str(Path(work) / sel[0]) if work and not os.path.isabs(sel[0]) else sel[0]
        return obu.analyze_formula(fp), os.path.basename(fp)

    def _on_done(r):
        try:
            (res, basename) = r
        except Exception:
            show_friendly_error(app, "分析失败")
            return
        if not res.get("success"):
            show_friendly_error(app, res.get("message", "元素分析失败"))
            return
        dlg = tk.Toplevel(app)
        dlg.title(f"🧪 分子式 & 元素分析 — {basename}")
        dlg.geometry("620x520")
        dlg.transient(app)
        pad = ttk.Frame(dlg, padding=16)
        pad.pack(fill=tk.BOTH, expand=True)

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
            tv.heading(c, text=c)
            tv.column(c, width=w, anchor="center")
        tv.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=8)
        pad.grid_rowconfigure(6, weight=1)
        pad.grid_columnconfigure(1, weight=1)

        els = res.get("elements") or {}
        pct = res.get("elements_pct") or {}
        total = sum(els.values())
        for sym in sorted(els.keys(), key=lambda s: (-els[s], s)):
            cnt = els[sym]
            p = pct.get(sym, round(cnt / max(1, total) * 100, 2))
            bar_len = int(p * 1.8)
            bar = "█" * bar_len
            tv.insert("", tk.END, values=(sym, cnt, f"{p:.2f}%  {bar}"))

        btns = ttk.Frame(pad)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(4, 0))

        def _copy_tsv():
            lines = ["元素\t个数\t质量百分比%"]
            for sym in sorted(els.keys(), key=lambda s: (-els[s], s)):
                lines.append(f"{sym}\t{els[sym]}\t{pct.get(sym, 0.0)}")
            try:
                app.clipboard_clear()
                app.clipboard_append("\n".join(lines))
                messagebox.showinfo("已复制", "元素表已复制为 TSV，直接粘贴到 Excel。", parent=dlg)
            except Exception as e:
                show_friendly_error(app, e)

        ttk.Button(btns, text="📋 复制表格(TSV)", command=_copy_tsv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="关闭", command=dlg.destroy).pack(side=tk.LEFT, padx=5)

    from task_manager import TaskManager
    TaskManager(app, controller).run_async(_run, on_done=_on_done)


def export_geometry_csv(app, controller):
    """导出几何参数 CSV（键长、键角）"""
    sel = app.helpers.get_selected_filenames()
    if not sel:
        app.helpers.on_log("⚠️ 请先选择一个分子文件", "warning")
        return
    work = app.work_dir_var.get().strip()
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
        parent=app,
    )
    if not target:
        return
    import openbabel_utils as obu
    r = obu.export_geometry_csv(src, target)
    if r.get("success"):
        app.helpers.on_log(
            f"✅ 几何参数导出完成：{r['n_atoms']} 原子, {r['n_bonds']} 键, {r['n_angles']} 角 → {target}",
            "success",
        )
        if messagebox.askyesno("导出成功",
                               f"已写入:\n  {target}\n\n原子 {r['n_atoms']}  |  键 {r['n_bonds']}  |  角 {r['n_angles']}\n\n是否现在打开该 CSV？",
                               parent=app):
            try:
                _safe_open_file(target)
            except Exception as e:
                show_friendly_error(app, e)
    else:
        show_friendly_error(app, r.get("message", "导出失败"))