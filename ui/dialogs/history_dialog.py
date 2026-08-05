#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史记录对话框 - 查看和操作撤销/重做历史
"""
import tkinter as tk
from tkinter import ttk
from utils.dialog_geom import fit_dialog_geometry


def show_history_dialog(app, controller):
    dialog = tk.Toplevel(app)
    dialog.title("📜 历史记录可视化面板")
    dialog.geometry(fit_dialog_geometry(dialog, 800, 500))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    top_btn_frame = ttk.Frame(dialog)
    top_btn_frame.pack(fill=tk.X, padx=10, pady=5)
    ttk.Button(top_btn_frame, text="🔄 刷新", command=lambda: _refresh_history_lists(undo_listbox, redo_listbox, controller)).pack(side=tk.LEFT)

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
        controller.undo_last()
        _refresh_history_lists(undo_listbox, redo_listbox, controller)

    def do_redo_one():
        controller.redo_last()
        _refresh_history_lists(undo_listbox, redo_listbox, controller)

    def do_undo_until_selected():
        sel = undo_listbox.curselection()
        if not sel:
            return
        target_idx = sel[0]
        result = controller.model.undo_until(target_idx)
        app.helpers.on_log(
            f"⏮️ 批量撤销完成: {result['steps']} 步，成功 {result['total_success']}，失败 {result['total_error']}",
            'info' if result['total_error'] == 0 else 'warning'
        )
        controller.scan_files()
        _refresh_history_lists(undo_listbox, redo_listbox, controller)

    ttk.Button(bottom_btn_frame, text="↩️ 撤销 1 步", command=do_undo_one).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_btn_frame, text="↪️ 重做 1 步", command=do_redo_one).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_btn_frame, text="⏮️ 回滚到选中项", command=do_undo_until_selected).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    _refresh_history_lists(undo_listbox, redo_listbox, controller)


def _refresh_history_lists(undo_listbox, redo_listbox, controller):
    undo_listbox.delete(0, tk.END)
    redo_listbox.delete(0, tk.END)
    history_snap = controller.model.get_history_snapshot()
    redo_snap = controller.model.get_redo_snapshot()
    for item in history_snap:
        undo_listbox.insert(tk.END, f"[{item['idx']}] {item['description']} ({item['file_count']} 文件)")
    for item in redo_snap:
        redo_listbox.insert(tk.END, f"[{item['idx']}] {item['description']} ({item['file_count']} 文件)")
