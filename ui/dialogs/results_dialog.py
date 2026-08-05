#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结果浏览器对话框 - 浏览 PSI4 计算结果，支持 ΔE 差值计算
"""
import csv
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path

from utils.logger import default_logger as logger
from .common import _safe_open_file
from utils.dialog_geom import fit_dialog_geometry


def show_results_browser_dialog(app, controller):
    dialog = tk.Toplevel(app)
    dialog.title("📊 计算结果浏览")
    dialog.geometry(fit_dialog_geometry(dialog, 900, 620))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    all_rows = []
    current_columns = ["base", "task_type", "method", "basis", "energy_Ha", "success", "log", "fchk", "opt_xyz"]

    top_btn_frame = ttk.Frame(dialog)
    top_btn_frame.pack(fill=tk.X, padx=10, pady=8)

    def refresh_tree():
        nonlocal all_rows, current_columns
        for item in tree.get_children():
            tree.delete(item)
        rows = controller.model.collect_results()
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
            initialdir=str(controller.model.work_dir),
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
            app.helpers.on_log(f"💾 选中行 CSV 已导出: {os.path.basename(out_path)}（{len(sel_ids)} 行）", 'success')
            messagebox.showinfo("导出成功", f"已导出 {len(sel_ids)} 行到：\n{out_path}", parent=dialog)
        except Exception as e:
            app.helpers.on_log(f"❌ CSV 导出失败: {e}", 'error')
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
    import ui.ui_theme as _ut; _ut.bind_treeview_hover(tree)

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
                _safe_open_file(log_path)
            except Exception as e:
                messagebox.showerror("打开失败", f"无法打开文件：{e}", parent=dialog)

    tree.bind("<Double-Button-1>", on_double_click)

    # ΔE 差值计算
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
        deltas = controller.model.compute_deltas(sel_rows, op)
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
            initialdir=str(controller.model.work_dir),
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
            app.helpers.on_log(f"💾 ΔE 差值 CSV 已导出: {os.path.basename(out_path)}（{len(last_deltas)} 条）", 'success')
            messagebox.showinfo("导出成功", f"已导出差值结果到：\n{out_path}", parent=dialog)
        except Exception as e:
            app.helpers.on_log(f"❌ 差值 CSV 导出失败: {e}", 'error')
            messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)

    btn_calc = ttk.Button(btn_row, text="📐 计算差值", command=calc_deltas)
    btn_calc.pack(side=tk.LEFT, padx=5)

    btn_delta_export = ttk.Button(btn_row, text="💾 导出差值 CSV", command=export_delta_csv)
    btn_delta_export.pack(side=tk.LEFT, padx=5)

    refresh_tree()
