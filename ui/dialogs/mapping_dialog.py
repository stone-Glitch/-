#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
映射管理对话框 - 映射表导入/导出/编辑
"""
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path

from utils.logger import default_logger as logger
from utils.dialog_geom import fit_dialog_geometry


def show_mapping_manager_dialog(app, controller):
    model = controller.model
    dialog = tk.Toplevel(app)
    dialog.title("📋 映射表管理")
    dialog.geometry(fit_dialog_geometry(dialog, 560, 380))
    dialog.resizable(True, True)
    dialog.transient(app)
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
            app.helpers.on_log(f"💾 导出缺失映射表: {count} 条", 'success')
        except Exception as e:
            messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导出缺失映射表失败: {e}", 'error')

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
            app.helpers.on_log(
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
                controller.scan_files()
        except Exception as e:
            messagebox.showerror("导入失败", f"导入失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导入映射表失败: {e}", 'error')

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
            app.helpers.on_log(f"💾 导出完整映射表: {count} 条", 'success')
        except Exception as e:
            messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导出完整映射表失败: {e}", 'error')

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


def show_mapping_editor_dialog(app, controller):
    model = controller.model
    dialog = tk.Toplevel(app)
    dialog.title("📋 分子命名映射表编辑器")
    dialog.geometry(fit_dialog_geometry(dialog, 750, 550))
    dialog.resizable(True, True)
    dialog.transient(app)
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
            app.helpers.on_log(f"❌ 保存映射表失败: {e}", "error")
            return
        model.set_mapping(new_dict)
        model.invalidate_scan_cache()
        app.helpers.on_log(f"💾 映射表已保存：{len(new_dict)} 条 → {out_path.name}", "success")
        messagebox.showinfo("保存成功", f"已保存 {len(new_dict)} 条映射到：\n{out_path}", parent=dialog)
        controller.scan_files()

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
    import ui.ui_theme as _ut; _ut.bind_treeview_hover(tv)
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
