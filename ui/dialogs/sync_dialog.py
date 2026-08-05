#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
目录同步对话框 - 两工作目录差异比较 + 一键同步
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

from utils.logger import default_logger as logger
from utils.dialog_geom import fit_dialog_geometry


def show_diff_sync_dialog(app, controller):
    from datetime import datetime

    dialog = tk.Toplevel(app)
    dialog.title("⚖️ 两工作目录差异比较 + 一键同步")
    dialog.geometry(fit_dialog_geometry(dialog, 800, 520))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    model = controller.model

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
        import ui.ui_theme as _ut; _ut.bind_treeview_hover(tv)
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

    default_wd = str(controller.model.work_dir)
    left_dir_var = tk.StringVar(value=default_wd)
    right_dir_var = tk.StringVar(value=default_wd)

    row1 = ttk.Frame(top_frame)
    row1.pack(fill=tk.X, pady=3)
    ttk.Label(row1, text="📁 左目录：", width=10).pack(side=tk.LEFT)
    ttk.Entry(row1, textvariable=left_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    ttk.Button(row1, text="浏览", width=8, command=lambda: _browse_dir(left_dir_var)).pack(side=tk.LEFT, padx=2)

    row2 = ttk.Frame(top_frame)
    row2.pack(fill=tk.X, pady=3)
    ttk.Label(row2, text="📁 右目录：", width=10).pack(side=tk.LEFT)
    ttk.Entry(row2, textvariable=right_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    ttk.Button(row2, text="浏览", width=8, command=lambda: _browse_dir(right_dir_var)).pack(side=tk.LEFT, padx=2)

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
        app.helpers.on_log(
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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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
            app.after(0, _do_compare)
            app.after(0, lambda: controller.scan_files())
        app.helpers.run_task(task)

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


def _browse_dir(var):
    from tkinter import filedialog
    d = filedialog.askdirectory(title="选择目录")
    if d:
        var.set(d)
