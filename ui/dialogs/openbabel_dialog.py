#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBabel 工具对话框 - 格式转换、SMILES生成、结构优化、描述符、分子叠加、2D预览
"""
import os
import csv
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from pathlib import Path

from utils.logger import default_logger as logger
from .base import _append_text, _clear_text, show_friendly_error
from .common import _safe_open_file
import chem.openbabel_utils as ob_utils
from utils.dialog_geom import fit_dialog_geometry


def show_openbabel_dialog(app, controller):
    available, msg, det = ob_utils.check_openbabel()
    if not available:
        app.helpers.on_log(f"❌ Open Babel 不可用: {msg}", 'error')
        return
    for _w in det.get("warnings", []):
        app.helpers.on_log(f"⚠️ OB: {_w}", 'warning')
    app.helpers.on_log(f"✅ OB: {msg}", 'info')

    dialog = tk.Toplevel(app)
    dialog.title("🔬 Open Babel 工具")
    dialog.geometry(fit_dialog_geometry(dialog, 700, 600))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    notebook = ttk.Notebook(dialog)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Tab1: 格式转换
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

    selected = app.helpers.get_selected_filenames()
    for s in selected:
        convert_listbox.insert(tk.END, s)

    convert_btn_col = ttk.Frame(tab_convert)
    convert_btn_col.grid(row=1, column=2, padx=5, sticky="nw")

    def add_convert_files():
        _add_unique_to_listbox(convert_listbox, _ask_ob_files(controller))

    def del_convert_selected():
        _delete_selected_from_listbox(convert_listbox)

    ttk.Button(convert_btn_col, text="添加...", command=add_convert_files).pack(side=tk.TOP, pady=2, fill=tk.X)
    ttk.Button(convert_btn_col, text="删除选中", command=del_convert_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

    ttk.Label(tab_convert, text="输出格式:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
    formats = ob_utils.get_supported_formats()
    convert_fmt_var = tk.StringVar(value="xyz" if "xyz" in formats else (formats[0] if formats else ""))
    ttk.Combobox(tab_convert, textvariable=convert_fmt_var, values=formats, state="readonly", width=15).grid(row=2, column=1, sticky=tk.W, padx=5, pady=(10, 0))
    ttk.Label(tab_convert, text="💡 例如: .mol → .xyz", foreground="gray").grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=5)

    ttk.Button(tab_convert, text="🔄 立即转换", command=lambda: _run_convert_batch(app, convert_listbox, convert_fmt_var.get(), dialog, controller)).grid(row=4, column=1, pady=10)

    # Tab2: SMILES
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
            app.helpers.on_log("❌ 请输入 SMILES", 'error')
            return
        prefix = prefix_entry.get().strip() or "mol_from_smiles"
        result = controller.model.generate_from_smiles(smiles, prefix, generate_3d=gen3d_var.get(), optimize=opt_var.get())
        if result.get("error"):
            app.helpers.on_log(f"❌ SMILES 生成失败: {result['error']}", 'error')
        else:
            app.helpers.on_log(f"✅ 生成成功: {os.path.basename(result['mol'])}", 'success')
            controller.scan_files()
            dialog.destroy()

    ttk.Button(tab_smiles, text="🧬 生成分子", command=do_smiles_generate).grid(row=5, column=1, pady=10)

    batch_frame = ttk.LabelFrame(tab_smiles, text="批量 SMILES 导入", padding=8)
    batch_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(10, 0))

    ttk.Label(batch_frame, text="每行一条，支持 \"SMILES 名称\" 空格分隔（名称可选）:").grid(row=0, column=0, sticky=tk.W)
    batch_text = scrolledtext.ScrolledText(batch_frame, height=6, wrap=tk.WORD, font=('Consolas', 9))
    batch_text.grid(row=1, column=0, sticky="nsew", pady=5)
    batch_frame.grid_rowconfigure(1, weight=1)
    batch_frame.grid_columnconfigure(0, weight=1)

    ttk.Button(batch_frame, text="🧬 批量生成", command=lambda: _run_smiles_batch(app, batch_text, gen3d_var.get(), opt_var.get(), dialog, controller)).grid(row=2, column=0, pady=5)

    tab_smiles.grid_rowconfigure(6, weight=1)
    tab_smiles.grid_columnconfigure(1, weight=1)

    # Tab3: 结构优化
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
        _add_unique_to_listbox(opt_listbox, _ask_ob_files(controller))

    def del_opt_selected():
        _delete_selected_from_listbox(opt_listbox)

    ttk.Button(opt_btn_col, text="添加...", command=add_opt_files).pack(side=tk.TOP, pady=2, fill=tk.X)
    ttk.Button(opt_btn_col, text="删除选中", command=del_opt_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

    ttk.Label(tab_opt, text="力场:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
    forcefield_var = tk.StringVar(value="mmff94")
    ttk.Combobox(tab_opt, textvariable=forcefield_var, values=["mmff94", "uff"], state="readonly", width=15).grid(row=2, column=1, sticky=tk.W, padx=5, pady=(10, 0))

    ttk.Button(tab_opt, text="⚡ 开始优化", command=lambda: _run_optimize_batch(app, opt_listbox, forcefield_var.get(), dialog, controller)).grid(row=4, column=1, pady=10)

    # Tab4: 描述符
    tab_desc = ttk.Frame(notebook, padding=10)
    notebook.add(tab_desc, text="📊 描述符")

    work_dir = controller.model.work_dir

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
        _delete_selected_from_listbox(desc_listbox)

    ttk.Button(desc_btn_col, text="添加...", command=add_desc_files).pack(side=tk.TOP, pady=2, fill=tk.X)
    ttk.Button(desc_btn_col, text="删除选中", command=del_desc_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

    desc_result = scrolledtext.ScrolledText(tab_desc, height=10, wrap=tk.WORD, font=('Consolas', 9))
    desc_result.grid(row=2, column=0, columnspan=4, pady=10, sticky="nsew")
    tab_desc.grid_rowconfigure(2, weight=1)
    tab_desc.grid_columnconfigure(1, weight=1)

    def load_from_main():
        names = app.helpers.get_selected_filenames()
        for name in names:
            full = Path(work_dir) / name
            if full.exists() and name not in desc_listbox.get(0, tk.END):
                desc_listbox.insert(tk.END, name)
        app.helpers.on_log(f"📄 已从主界面加载 {len(names)} 个文件到列表", 'info')

    def do_descriptors():
        items = list(desc_listbox.get(0, tk.END))
        if not items:
            app.helpers.on_log("❌ 列表中没有文件", 'error')
            return
        sel = desc_listbox.curselection()
        if sel:
            fname = items[sel[0]]
        else:
            fname = items[0]

        def task_process(**kwargs):
            path = Path(work_dir) / fname
            desc = controller.model.calculate_descriptors(str(path))
            def update_ui():
                _clear_text(app, desc_result)
                if "error" in desc:
                    _append_text(app, desc_result, f"❌ 错误: {desc['error']}")
                else:
                    _append_text(app, desc_result, f"📋 {fname} 计算结果:\n")
                    for key, val in desc.items():
                        _append_text(app, desc_result, f"{key}: {val}\n", see_end=False)
            app.after(0, update_ui)
        app.helpers.run_task(task_process)

    def do_batch_csv():
        items = list(desc_listbox.get(0, tk.END))
        if not items:
            app.helpers.on_log("❌ 列表中没有文件可批量计算", 'error')
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
                    desc = controller.model.calculate_descriptors(str(path))
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
                    app.helpers.on_log(f"💾 CSV 已导出: {os.path.basename(out_path)}（共 {len(rows)} 条）", 'success')
                    controller.scan_files()
                app.after(0, done)
            except Exception as e:
                def fail():
                    app.helpers.on_log(f"❌ CSV 写出失败: {e}", 'error')
                app.after(0, fail)
        app.helpers.run_task(task_process)

    desc_btn_row = ttk.Frame(tab_desc)
    desc_btn_row.grid(row=3, column=0, columnspan=4, pady=5)
    ttk.Button(desc_btn_row, text="📄 从主界面选中的分子加载", command=load_from_main).pack(side=tk.LEFT, padx=5)
    ttk.Button(desc_btn_row, text="📊 计算描述符", command=do_descriptors).pack(side=tk.LEFT, padx=5)
    ttk.Button(desc_btn_row, text="💾 批量计算并导出 CSV", command=do_batch_csv).pack(side=tk.LEFT, padx=5)

    # Tab5: 分子叠加
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
        _add_unique_to_listbox(ref_listbox, _ask_ob_files(controller))

    def del_ref_selected():
        _delete_selected_from_listbox(ref_listbox)

    ttk.Button(ref_btn_col, text="添加...", command=add_ref_files).pack(side=tk.TOP, pady=2, fill=tk.X)
    ttk.Button(ref_btn_col, text="删除选中", command=del_ref_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

    ttk.Label(tab_align, text="移动分子（多选）:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))

    mob_list_frame = ttk.Frame(tab_align)
    mob_list_frame.grid(row=2, column=1, padx=5, sticky="nsew", pady=(10, 0))
    mob_listbox = tk.Listbox(mob_list_frame, height=8, selectmode=tk.EXTENDED, width=45)
    mob_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    mob_scroll = ttk.Scrollbar(mob_list_frame, orient=tk.VERTICAL, command=mob_listbox.yview)
    mob_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    mob_listbox.configure(yscrollcommand=mb_scroll.set)

    for s in selected[1:] if len(selected) > 1 else []:
        mob_listbox.insert(tk.END, s)

    mob_btn_col = ttk.Frame(tab_align)
    mob_btn_col.grid(row=2, column=2, padx=5, sticky="nw", pady=(10, 0))

    def add_mob_files():
        _add_unique_to_listbox(mob_listbox, _ask_ob_files(controller))

    def del_mob_selected():
        _delete_selected_from_listbox(mob_listbox)

    ttk.Button(mob_btn_col, text="添加...", command=add_mob_files).pack(side=tk.TOP, pady=2, fill=tk.X)
    ttk.Button(mob_btn_col, text="删除选中", command=del_mob_selected).pack(side=tk.TOP, pady=2, fill=tk.X)

    ttk.Button(tab_align, text="🔗 执行叠加", command=lambda: _run_align_batch(app, ref_listbox, mob_listbox, dialog, controller)).grid(row=3, column=1, pady=10)

    ttk.Label(dialog, text="所有操作在后台运行，请查看日志", foreground="blue").pack(pady=5)

    # 2D预览独立按钮
    preview_btn = ttk.Button(dialog, text="🖼️ 2D结构预览", command=lambda: preview_2d_structure(app, controller))
    preview_btn.pack(pady=5)


def _ask_ob_files(controller):
    return filedialog.askopenfilenames(
        initialdir=str(controller.model.work_dir),
        filetypes=[("分子文件", "*.mol *.xyz *.sdf *.mol2 *.fchk *.out"), ("全部", "*.*")]
    )


def _add_unique_to_listbox(listbox, files):
    for f in files:
        name = os.path.basename(f)
        if name not in listbox.get(0, tk.END):
            listbox.insert(tk.END, name)


def _delete_selected_from_listbox(listbox):
    for idx in reversed(listbox.curselection()):
        listbox.delete(idx)


def _run_convert_batch(app, listbox, out_fmt, dialog, controller):
    items = list(listbox.get(0, tk.END))
    if not items:
        app.helpers.on_log("❌ 列表中没有文件", 'error')
        return
    if not out_fmt:
        app.helpers.on_log("❌ 请选择输出格式", 'error')
        return
    dialog.destroy()

    work_dir = controller.model.work_dir

    def task_process(**kwargs):
        all_ok = True
        for name in items:
            input_path = Path(work_dir) / name
            base = input_path.stem
            output_path = work_dir / f"{base}.{out_fmt}"
            try:
                res = controller.model.convert_file(str(input_path), str(output_path), out_fmt)
                success = res.get("success", False)
                msg = res.get("message", "")
                app.helpers.on_log(f"{'✅' if success else '❌'} 转换 {name}: {msg}", 'success' if success else 'error')
                if not success:
                    all_ok = False
            except Exception as e:
                app.helpers.on_log(f"❌ 转换 {name} 异常: {e}", 'error')
                all_ok = False
        if all_ok:
            controller.scan_files()
    app.helpers.run_task(task_process)


def _run_optimize_batch(app, listbox, forcefield, dialog, controller):
    items = list(listbox.get(0, tk.END))
    if not items:
        app.helpers.on_log("❌ 列表中没有文件", 'error')
        return
    dialog.destroy()

    work_dir = controller.model.work_dir

    def task_process(**kwargs):
        all_ok = True
        for name in items:
            input_path = Path(work_dir) / name
            base = input_path.stem
            ext = input_path.suffix
            output_path = work_dir / f"{base}_opt{ext}"
            try:
                res = controller.model.optimize_geometry(str(input_path), str(output_path), forcefield)
                success = res.get("success", False)
                msg = res.get("message", "")
                app.helpers.on_log(f"{'✅' if success else '❌'} 优化 {name}: {msg}", 'success' if success else 'error')
                if not success:
                    all_ok = False
            except Exception as e:
                app.helpers.on_log(f"❌ 优化 {name} 异常: {e}", 'error')
                all_ok = False
        if all_ok:
            controller.scan_files()
    app.helpers.run_task(task_process)


def _run_smiles_batch(app, text_widget, gen3d, opt, dialog, controller):
    raw = text_widget.get(1.0, tk.END).strip()
    if not raw:
        app.helpers.on_log("❌ 请输入 SMILES 内容", 'error')
        return
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        app.helpers.on_log("❌ 没有有效 SMILES 行", 'error')
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
                res = controller.model.generate_from_smiles(smiles, name, generate_3d=gen3d, optimize=opt)
                if res.get("error"):
                    app.helpers.on_log(f"❌ SMILES 生成失败 {name}: {res['error']}", 'error')
                    all_ok = False
                else:
                    app.helpers.on_log(f"✅ 生成成功 {name}: {os.path.basename(res['mol'])}", 'success')
            except Exception as e:
                app.helpers.on_log(f"❌ SMILES 生成异常 {name}: {e}", 'error')
                all_ok = False
        if all_ok:
            controller.scan_files()
    app.helpers.run_task(task_process)


def _run_align_batch(app, ref_listbox, mob_listbox, dialog, controller):
    ref_sel = ref_listbox.curselection()
    if not ref_sel:
        app.helpers.on_log("❌ 请选择参考分子", 'error')
        return
    ref_name = ref_listbox.get(ref_sel[0])
    mob_items = list(mob_listbox.get(0, tk.END))
    if not mob_items:
        app.helpers.on_log("❌ 移动分子列表为空", 'error')
        return
    dialog.destroy()

    work_dir = controller.model.work_dir
    ref_path = Path(work_dir) / ref_name
    ref_stem = ref_path.stem

    def task_process(**kwargs):
        all_ok = True
        for mob_name in mob_items:
            mob_path = Path(work_dir) / mob_name
            mob_stem = mob_path.stem
            out_path = work_dir / f"{mob_stem}_aligned_to_{ref_stem}.xyz"
            try:
                res = controller.model.align_molecules(str(ref_path), str(mob_path), str(out_path))
                success = res.get("success", False)
                msg = res.get("message", "")
                app.helpers.on_log(f"{'✅' if success else '❌'} 叠加 {mob_name}: {msg}", 'success' if success else 'error')
                if not success:
                    all_ok = False
            except Exception as e:
                app.helpers.on_log(f"❌ 叠加 {mob_name} 异常: {e}", 'error')
                all_ok = False
        if all_ok:
            controller.scan_files()
    app.helpers.run_task(task_process)


def preview_2d_structure(app, controller):
    selected = app.helpers.get_selected_filenames()
    if not selected:
        app.helpers.on_log("⚠️ 请先选择一个文件", 'warning')
        return
    fname = selected[0]
    ext = Path(fname).suffix.lower()
    mol_exts = ('.mol', '.xyz', '.sdf', '.mol2')
    if ext not in mol_exts:
        app.helpers.on_log(f"⚠️ 不支持的文件类型 {ext}，仅支持 {', '.join(mol_exts)}", 'warning')
        messagebox.showwarning("不支持", f"仅支持以下分子文件类型:\n{', '.join(mol_exts)}")
        return

    def task_process(**kwargs):
        res = controller.model.render_png_2d(fname)
        success = res.get("success", False)
        msg = res.get("message", "")
        png_path = res.get("output_path")

        def done():
            if not success or not png_path or not os.path.exists(png_path):
                app.helpers.on_log(f"❌ 2D 预览失败: {msg}", 'error')
                messagebox.showerror("预览失败", f"2D 结构渲染失败:\n{msg}")
                return
            app.helpers.on_log(f"✅ 2D 预览: {msg}", 'success')
            _show_png_preview(app, png_path, fname)
        app.after(0, done)
    app.helpers.run_task(task_process)


def _show_png_preview(app, png_path: str, fname: str):
    try:
        from PIL import Image, ImageTk
        has_pil = True
    except ImportError:
        has_pil = False

    dialog = tk.Toplevel(app)
    dialog.title(f"🖼️ 2D 结构预览 - {os.path.basename(fname)}")
    dialog.geometry(fit_dialog_geometry(dialog, 900, 700))
    dialog.resizable(True, True)
    dialog.transient(app)

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
                        _safe_open_file(png_path)
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
    ttk.Button(btn_frame, text="📂 打开文件位置", command=lambda: _open_png_folder(png_path)).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.LEFT, padx=5)


def _open_png_folder(png_path: str):
    try:
        folder = os.path.dirname(os.path.abspath(png_path))
        _safe_open_file(folder)
    except Exception as e:
        messagebox.showerror("打开失败", f"无法打开文件夹:\n{e}")
