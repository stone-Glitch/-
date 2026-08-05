#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反应动画对话框 - 多反应物/多产物动画生成
"""
import os
import sys
import tempfile
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from pathlib import Path

from utils.logger import default_logger as logger
from utils.preset_manager import get_preset_manager
from .base import register_dialog_temp_dir, unregister_dialog_temp_dir
from .common import _resolve_iqmol_exe, _safe_open_file
from utils.dialog_geom import fit_dialog_geometry, make_scrollable_body


def show_reaction_animation_dialog(app, controller):
    import chem.reaction_animation as ra
    import shutil as _shutil
    import tempfile

    dialog = tk.Toplevel(app)
    dialog.title("🎬 制作反应动画（含 IQmol 可播放轨迹 · 支持多反应物+多产物）")
    dialog.geometry(fit_dialog_geometry(dialog, 950, 880))
    dialog.resizable(True, True)
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    # 可滚动主体：内容可能高于被屏幕钳制后的窗口，滚动条保证全部可见
    _canvas, body = make_scrollable_body(dialog)

    pad = {"padx": 12, "pady": 4}

    # 模式切换
    mode_frame = ttk.Frame(body)
    mode_frame.pack(fill=tk.X, padx=12, pady=6)

    mode_var = tk.StringVar(value=app.config_data.get("ui_mode", "simple"))
    simple_btn = ttk.Radiobutton(mode_frame, text="🌟 简单模式（推荐）",
                                  variable=mode_var, value="simple",
                                  command=lambda: _toggle_mode("simple"))
    adv_btn = ttk.Radiobutton(mode_frame, text="🔧 高级模式",
                               variable=mode_var, value="advanced",
                               command=lambda: _toggle_mode("advanced"))
    simple_btn.pack(side=tk.LEFT, padx=4)
    adv_btn.pack(side=tk.LEFT, padx=4)

    mode_tip = ttk.Label(mode_frame, text="💡 简单模式只显示核心参数，高级选项已隐藏",
                         foreground="#3B6EFF")
    mode_tip.pack(side=tk.LEFT, padx=10)

    # 预设管理
    pm = get_preset_manager()
    preset_frame = ttk.LabelFrame(body, text="📁 预设管理", padding="6")
    preset_frame.pack(fill=tk.X, padx=12, pady=(6, 4))

    preset_var = tk.StringVar()
    preset_combo = ttk.Combobox(preset_frame, textvariable=preset_var,
                                values=pm.list_presets(), state="readonly", width=20)
    preset_combo.pack(side=tk.LEFT, padx=4)

    def _load_preset():
        name = preset_var.get()
        if not name:
            return
        data = pm.get_preset(name)
        if not data:
            return
        try:
            if "fps" in data: fps_var.set(data["fps"])
            if "steps" in data: steps_var.set(data["steps"])
            if "mode" in data: mode_var.set(data["mode"])
            if "fmt" in data: fmt_var.set(data["fmt"])
            if "resolution" in data: res_var.set(data["resolution"])
            if "smooth" in data: smooth_var.set(data["smooth"])
            if "spacing" in data: spacing_var.set(data["spacing"])
            if "reactants" in data:
                r_list.delete(0, tk.END)
                for p in data["reactants"]:
                    if Path(p).exists():
                        r_list.insert(tk.END, p)
            if "products" in data:
                p_list.delete(0, tk.END)
                for p in data["products"]:
                    if Path(p).exists():
                        p_list.insert(tk.END, p)
            if "solvent" in data:
                solvent_var.set(data["solvent"])
            if "qm_method" in data:
                qm_method_var.set(data["qm_method"])
            if "qm_basis" in data:
                qm_basis_var.set(data["qm_basis"])
            if "qm_d3" in data:
                qm_d3_var.set(data["qm_d3"])
            if "qm_charge" in data:
                qm_charge_var.set(data["qm_charge"])
            if "qm_mult" in data:
                qm_mult_var.set(data["qm_mult"])
            if "qm_mem" in data:
                qm_mem_var.set(data["qm_mem"])
            if "scan_steps" in data:
                scan_steps_var.set(data["scan_steps"])
            if "scan_output" in data:
                scan_output_var.set(data["scan_output"])
            if "trajectory_format" in data:
                traj_fmt_var.set(data["trajectory_format"])
            if "iqmol_path" in data:
                iqmol_path_var.set(data["iqmol_path"])
            if "auto_open_iqmol" in data:
                auto_open_iqmol_var.set(data["auto_open_iqmol"])
            if "gen_traj" in data:
                gen_traj_var.set(data["gen_traj"])
            if "out_path" in data:
                out_var.set(data["out_path"])
            if "traj_path" in data:
                traj_var.set(data["traj_path"])
            messagebox.showinfo("加载成功", f"已加载预设：{name}", parent=dialog)
        except Exception as e:
            messagebox.showerror("加载失败", str(e), parent=dialog)

    def _save_preset():
        name = simpledialog.askstring("保存预设", "输入预设名称：", parent=dialog)
        if not name:
            return
        data = {
            "fps": fps_var.get(),
            "steps": steps_var.get(),
            "mode": mode_var.get(),
            "fmt": fmt_var.get(),
            "resolution": res_var.get(),
            "smooth": smooth_var.get(),
            "spacing": spacing_var.get(),
            "reactants": [r_list.get(i) for i in range(r_list.size())],
            "products": [p_list.get(i) for i in range(p_list.size())],
            "solvent": solvent_var.get(),
            "qm_method": qm_method_var.get(),
            "qm_basis": qm_basis_var.get(),
            "qm_d3": qm_d3_var.get(),
            "qm_charge": qm_charge_var.get(),
            "qm_mult": qm_mult_var.get(),
            "qm_mem": qm_mem_var.get(),
            "scan_steps": scan_steps_var.get(),
            "scan_output": scan_output_var.get(),
            "trajectory_format": traj_fmt_var.get(),
            "iqmol_path": iqmol_path_var.get(),
            "auto_open_iqmol": auto_open_iqmol_var.get(),
            "gen_traj": gen_traj_var.get(),
            "out_path": out_var.get(),
            "traj_path": traj_var.get(),
        }
        if pm.save_preset(name, data):
            preset_combo["values"] = pm.list_presets()
            preset_var.set(name)
            messagebox.showinfo("保存成功", f"预设 '{name}' 已保存", parent=dialog)
        else:
            messagebox.showerror("保存失败", "无法保存预设", parent=dialog)

    def _delete_preset():
        name = preset_var.get()
        if not name:
            return
        if messagebox.askyesno("确认删除", f"确定删除预设 '{name}' 吗？", parent=dialog):
            if pm.delete_preset(name):
                preset_combo["values"] = pm.list_presets()
                preset_var.set("")
                messagebox.showinfo("已删除", f"预设 '{name}' 已删除", parent=dialog)
            else:
                messagebox.showerror("删除失败", "删除预设失败", parent=dialog)

    def _export_preset():
        name = preset_var.get()
        if not name:
            return
        export_path = filedialog.asksaveasfilename(
            title="导出预设为 JSON",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=dialog
        )
        if export_path:
            if pm.export_preset(name, export_path):
                messagebox.showinfo("导出成功", f"预设已导出到：{export_path}", parent=dialog)
            else:
                messagebox.showerror("导出失败", "导出预设失败", parent=dialog)

    def _import_preset():
        import_path = filedialog.askopenfilename(
            title="导入预设 JSON 文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=dialog
        )
        if import_path:
            try:
                name, data = pm.import_preset(import_path)
                preset_combo["values"] = pm.list_presets()
                preset_var.set(name)
                messagebox.showinfo("导入成功", f"已导入预设：{name}", parent=dialog)
            except Exception as e:
                messagebox.showerror("导入失败", str(e), parent=dialog)

    ttk.Button(preset_frame, text="📥 加载", command=_load_preset).pack(side=tk.LEFT, padx=2)
    ttk.Button(preset_frame, text="💾 保存", command=_save_preset).pack(side=tk.LEFT, padx=2)
    ttk.Button(preset_frame, text="🗑️ 删除", command=_delete_preset).pack(side=tk.LEFT, padx=2)
    ttk.Button(preset_frame, text="📤 导出", command=_export_preset).pack(side=tk.LEFT, padx=2)
    ttk.Button(preset_frame, text="📥 导入", command=_import_preset).pack(side=tk.LEFT, padx=2)

    # 常见反应模板
    tpl_frame = ttk.LabelFrame(body, text="✨ 常见反应模板（点一下自动填好反应物和产物）", padding=8)
    tpl_frame.pack(fill='x', padx=12, pady=(0, 4))

    # 内置常见反应
    BUILTIN_XYZ = {
        "ch4": (5, ["C", "H", "H", "H", "H"], [
            [0.00000, 0.00000, 0.00000],
            [0.62912, 0.62912, 0.62912],
            [-0.62912, -0.62912, 0.62912],
            [-0.62912, 0.62912, -0.62912],
            [0.62912, -0.62912, -0.62912],
        ]),
        "cl2": (2, ["Cl", "Cl"], [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        "hcl": (2, ["H", "Cl"], [[0.0, 0.0, 0.0], [1.28, 0.0, 0.0]]),
        "ch3cl": (5, ["C", "Cl", "H", "H", "H"], [
            [0.00000, 0.00000, 0.00000],
            [1.78000, 0.00000, 0.00000],
            [-0.35700, 0.95000, 0.35700],
            [-0.35700, -0.52000, 0.88600],
            [-0.35700, -0.52000, -0.88600],
        ]),
        "h2": (2, ["H", "H"], [[0.0, 0.0, 0.0], [0.74, 0.0, 0.0]]),
        "h2o": (3, ["O", "H", "H"], [[0.0, 0.0, 0.0], [0.957, 0.0, 0.0], [-0.239, 0.927, 0.0]]),
        "o2": (2, ["O", "O"], [[0.0, 0.0, 0.0], [1.21, 0.0, 0.0]]),
        "co2": (3, ["C", "O", "O"], [[0.0, 0.0, 0.0], [1.16, 0.0, 0.0], [-1.16, 0.0, 0.0]]),
        "n2": (2, ["N", "N"], [[0.0, 0.0, 0.0], [1.098, 0.0, 0.0]]),
        "nh3": (4, ["N", "H", "H", "H"], [
            [0.00000, 0.00000, 0.00000],
            [0.93770, 0.00000, 0.36690],
            [-0.46890, 0.81200, 0.36690],
            [-0.46890, -0.81200, 0.36690],
        ]),
        "ch3oh": (6, ["C", "O", "H", "H", "H", "H"], [
            [0.74410, 0.00000, 0.00000],
            [-0.68660, 0.00000, 0.00000],
            [1.10690, 0.96170, 0.34510],
            [1.10690, -0.45960, 0.89340],
            [1.10690, -0.50210, -0.92740],
            [-1.07800, 0.81090, 0.00000],
        ]),
        "c2h4": (6, ["C", "C", "H", "H", "H", "H"], [
            [0.66950, 0.00000, 0.00000],
            [-0.66950, 0.00000, 0.00000],
            [1.24000, 0.92890, 0.00000],
            [1.24000, -0.92890, 0.00000],
            [-1.24000, 0.92890, 0.00000],
            [-1.24000, -0.92890, 0.00000],
        ]),
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

    def _resolve_or_build(name, tmpdir):
        workdir = Path(controller.model.work_dir)
        candidate = workdir / f"{name}.xyz"
        if candidate.exists():
            return str(candidate)
        candidate2 = workdir / f"{name}.mol"
        if candidate2.exists():
            return str(candidate2)
        if name not in BUILTIN_XYZ:
            return str(candidate)
        n, syms, coords = BUILTIN_XYZ[name]
        lines = [str(n), ""]
        for s, (x, y, z) in zip(syms, coords):
            lines.append(f"{s:2s} {x:12.6f} {y:12.6f} {z:12.6f}")
        p = tmpdir / f"tpl_{name}.xyz"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(p)

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
    solvent_cb_var_to_key_map = {}
    for _it in SOLVENT_CHOICES:
        if _it.startswith("（不"):
            solvent_cb_var_to_key_map[_it] = None
        else:
            solvent_cb_var_to_key_map[_it] = _it.split(" ", 1)[0].strip()

    def _apply_template(r_names, p_names, def_solvent=None):
        r_list.delete(0, tk.END)
        p_list.delete(0, tk.END)

        import tempfile
        import shutil as _shu

        tpl_dirs = getattr(dialog, "_ra_tpl_dirs", None)
        if tpl_dirs is None:
            tpl_dirs = set()
            dialog._ra_tpl_dirs = tpl_dirs

            def _cleanup_all_tpl_dirs():
                ds = getattr(dialog, "_ra_tpl_dirs", None) or set()
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
            dialog.protocol("WM_DELETE_WINDOW", lambda: (_cleanup_all_tpl_dirs(), dialog.destroy()))
            dialog.bind("<Destroy>", lambda event: _cleanup_all_tpl_dirs() if event.widget is dialog else None)

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
            dialog._ra_tpl_last = None

        td = Path(tempfile.mkdtemp(prefix="ra_tpl_"))
        register_dialog_temp_dir(td)
        tpl_dirs.add(td)
        dialog._ra_tpl_last = td

        for n in r_names:
            _ra_add_unique_path(r_list, _resolve_or_build(n, td))
        for n in p_names:
            _ra_add_unique_path(p_list, _resolve_or_build(n, td))
        if def_solvent is not None:
            for k in SOLVENT_CHOICES:
                if k.startswith(def_solvent + " "):
                    solvent_var.set(k)
                    break
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
    rows = []
    for i, (label, rs, ps, sol) in enumerate(tpl_btns):
        row_idx, _col = divmod(i, 3)
        while len(rows) <= row_idx:
            nr = ttk.Frame(tpl_frame)
            nr.pack(fill='x', pady=(4, 0))
            rows.append(nr)
        b = ttk.Button(rows[row_idx], text=label,
                       command=lambda _rs=rs, _ps=ps, _s=sol: _apply_template(_rs, _ps, _s))
        b.pack(side='left', padx=4, pady=2, fill='x', expand=True)

    # 主界面 - 反应物/产物列表
    r_frame = ttk.LabelFrame(body, text="反应物列表（可多选，按先后顺序沿 +X 拼接）")
    r_frame.pack(fill='x', padx=12, pady=4)
    r_list = tk.Listbox(r_frame, height=6, selectmode=tk.EXTENDED)
    r_sb = ttk.Scrollbar(r_frame, orient='vertical', command=r_list.yview)
    r_list.configure(yscrollcommand=r_sb.set)
    r_list.pack(side='left', fill='both', expand=True, padx=(8, 2), pady=6)
    r_sb.pack(side='left', fill='y', pady=6)
    r_btns = ttk.Frame(r_frame)
    r_btns.pack(side='left', fill='y', padx=6, pady=6)
    ttk.Button(r_btns, text="➕ 添加", width=10,
               command=lambda: _browse_open_multi(r_list, controller)).pack(pady=2)
    ttk.Button(r_btns, text="➖ 删除选中", width=10,
               command=lambda: _ra_delete_selected(r_list)).pack(pady=2)

    p_frame = ttk.LabelFrame(body, text="产物列表（可多选）")
    p_frame.pack(fill='x', padx=12, pady=4)
    p_list = tk.Listbox(p_frame, height=6, selectmode=tk.EXTENDED)
    p_sb = ttk.Scrollbar(p_frame, orient='vertical', command=p_list.yview)
    p_list.configure(yscrollcommand=p_sb.set)
    p_list.pack(side='left', fill='both', expand=True, padx=(8, 2), pady=6)
    p_sb.pack(side='left', fill='y', pady=6)
    p_btns = ttk.Frame(p_frame)
    p_btns.pack(side='left', fill='y', padx=6, pady=6)
    ttk.Button(p_btns, text="➕ 添加", width=10,
               command=lambda: _browse_open_multi(p_list, controller)).pack(pady=2)
    ttk.Button(p_btns, text="➖ 删除选中", width=10,
               command=lambda: _ra_delete_selected(p_list)).pack(pady=2)

    # 溶剂和 QM 参数
    qm = ttk.LabelFrame(body, text="🧪 溶剂 & 能量（可选：一键跑 PSI4 线性扫描，自动写入每帧 E= 注释）")
    qm.pack(fill='x', padx=12, pady=(6, 4))

    rq1 = ttk.Frame(qm)
    rq1.pack(fill='x', **pad)
    ttk.Label(rq1, text="隐式溶剂 (PCM/SMD):", width=22, anchor='w').pack(side='left')
    solvent_var = tk.StringVar(value=SOLVENT_CHOICES[0])
    solvent_cb = ttk.Combobox(rq1, textvariable=solvent_var, state="readonly",
                              width=42, values=SOLVENT_CHOICES)
    solvent_cb.pack(side='left', padx=(0, 8))

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

    rq2 = ttk.Frame(qm)
    rq2.pack(fill='x', **pad)
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

    rq3 = ttk.Frame(qm)
    rq3.pack(fill='x', **pad)
    preset_psi4_var = tk.StringVar(value="（无预设）")
    ttk.Label(rq3, text="PSI4 预设:", width=10, anchor='w').pack(side='left')
    try:
        from utils.constants import PSI4_PRESETS as _PP
        _presets_list = ["（无预设）"] + sorted(list(_PP.keys()))
    except Exception:
        _presets_list = ["（无预设）"]
    ttk.Combobox(rq3, textvariable=preset_psi4_var, width=28, state="readonly",
                 values=_presets_list).pack(side='left')
    scan_output_var = tk.StringVar(value=str(controller.model.work_dir / "scan_output"))
    ttk.Label(rq3, text="  扫描输出目录:", width=14, anchor='w').pack(side='left')
    ttk.Entry(rq3, textvariable=scan_output_var, width=30).pack(side='left', padx=(0, 4))
    ttk.Button(rq3, text="浏览...", width=8,
               command=lambda: (scan_output_var.set(
                   filedialog.askdirectory(parent=dialog, initialdir=str(controller.model.work_dir),
                                           title="选择势能面扫描输出目录")
                   or scan_output_var.get()))).pack(side='left')
    run_scan_btn = ttk.Button(rq3, text="⚡ 运行 PSI4 线性扫描并自动填 CSV", width=40)
    run_scan_btn.pack(side='right', padx=4)

    # 高级参数容器
    advanced_container = ttk.LabelFrame(body, text="⚙️ 高级参数（步数/模式/FFmpeg/轨迹等）", padding="8")
    # 暂不 pack，由 _toggle_mode 控制

    opts = advanced_container
    r1 = ttk.Frame(opts)
    r1.pack(fill='x', **pad)
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

    r2 = ttk.Frame(opts)
    r2.pack(fill='x', **pad)
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

    r3 = ttk.Frame(opts)
    r3.pack(fill='x', **pad)
    ttk.Label(r3, text="ffmpeg 路径:", width=18, anchor='w').pack(side='left')
    ffmpeg_var = tk.StringVar(value="ffmpeg")
    ttk.Entry(r3, textvariable=ffmpeg_var, width=32).pack(side='left')
    cap = "（仅 MP4 格式需要；默认 PATH 的 ffmpeg）"
    ttk.Label(r3, text=cap, foreground='#6a737d').pack(side='left', padx=(8, 0))

    # IQmol 轨迹输出
    iq = ttk.LabelFrame(advanced_container, text="🧪 IQmol 可播放轨迹输出（推荐！支持多反应物/多产物）")
    iq.pack(fill='x', padx=12, pady=(4, 4))
    gen_traj_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(iq, text="同时生成 IQmol 多帧轨迹文件（推荐 always on）",
                    variable=gen_traj_var).pack(padx=10, pady=(6, 2), anchor='w')
    row_t = ttk.Frame(iq)
    row_t.pack(fill='x', **pad)
    ttk.Label(row_t, text="IQmol 轨迹输出:", width=18, anchor='w').pack(side='left')
    traj_var = tk.StringVar(value=str(controller.model.work_dir / "reaction_trajectory.xyz"))
    ttk.Entry(row_t, textvariable=traj_var).pack(side='left', fill='x', expand=True, padx=(4, 4))
    ttk.Button(row_t, text="浏览...", width=8,
               command=lambda: _browse_save(traj_var, "保存 IQmol 轨迹文件", ".xyz",
                                             [("IQmol 多帧 XYZ", "*.xyz"),
                                              ("SDF 轨迹", "*.sdf"),
                                              ("所有文件", "*.*")])).pack(side='left')
    rq = ttk.Frame(iq)
    rq.pack(fill='x', **pad)
    ttk.Label(rq, text="轨迹格式:", width=18, anchor='w').pack(side='left')
    traj_fmt_var = tk.StringVar(value="xyz (Concatenated 多帧 XYZ，IQmol 直接播放)")
    ttk.Combobox(rq, textvariable=traj_fmt_var, state="readonly", width=26,
                 values=["xyz (Concatenated 多帧 XYZ，IQmol 直接播放)",
                         "sdf (SDF 多构象，带 >  <Energy> 字段)"]).pack(side='left')

    ttk.Label(rq, text="   IQmol 程序路径:", width=16, anchor='w').pack(side='left')
    iqmol_path_var = tk.StringVar(value="IQmol")
    ttk.Entry(rq, textvariable=iqmol_path_var, width=22).pack(side='left')
    auto_open_iqmol_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(rq, text="生成后立即打开", variable=auto_open_iqmol_var).pack(side='left', padx=(10, 0))

    # 可视化输出路径
    row_out = ttk.Frame(advanced_container)
    row_out.pack(fill='x', **pad)
    ttk.Label(row_out, text="可视化输出:", width=18, anchor='w').pack(side='left')
    out_var = tk.StringVar(value=str(controller.model.work_dir / "reaction_animation.gif"))
    ttk.Entry(row_out, textvariable=out_var).pack(side='left', fill='x', expand=True, padx=(4, 4))

    def _def_ext_for_format(fmt_tok, traj):
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
            p_ = Path(out_var.get().strip() or str(controller.model.work_dir / "reaction_animation.gif"))
            if default_ext:
                p_ = p_.with_suffix(default_ext)
            out_var.set(str(p_))
        ttok = traj_fmt_var.get().strip().lower()
        text, _ = _def_ext_for_format(ttok, True)
        tp = Path(traj_var.get().strip() or str(controller.model.work_dir / "reaction_trajectory.xyz"))
        if text:
            tp = tp.with_suffix(text)
        traj_var.set(str(tp))

    fmt_var.trace_add("write", lambda *_: _on_change_fmt())
    traj_fmt_var.trace_add("write", lambda *_: _on_change_fmt())

    ttk.Button(row_out, text="浏览...", width=8,
               command=lambda: _browse_save(out_var, "选择可视化输出",
                                            _def_ext_for_format(fmt_var.get().strip().lower(), False)[0],
                                            _def_ext_for_format(fmt_var.get().strip().lower(), False)[1])).pack(side='left')

    # 预览按钮和生成按钮
    preview_btn = ttk.Button(body, text="👁️ 预览第一帧（快速查看效果）",
                             command=lambda: _preview_frame(dialog, r_list, p_list, spacing_var, app))
    preview_btn.pack(pady=4)

    # 结果显示区
    result_text = scrolledtext.ScrolledText(body, height=4, wrap=tk.WORD, font=('Consolas', 9))
    result_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 4))
    result_text.configure(state='disabled')

    # 底部按钮
    btn_row = ttk.Frame(body)
    btn_row.pack(fill=tk.X, padx=12, pady=(8, 12))

    def _start():
        _start_animation(
            app, dialog, r_list, p_list, steps_var, mode_var, fps_var, spacing_var,
            smooth_var, res_var, fmt_var, ffmpeg_var, gen_traj_var, traj_var,
            traj_fmt_var, iqmol_path_var, auto_open_iqmol_var, out_var,
            solvent_var, qm_method_var, qm_basis_var, qm_d3_var, qm_charge_var,
            qm_mult_var, qm_mem_var, scan_steps_var, scan_output_var,
            preset_psi4_var, solvent_cb_var_to_key_map, result_text, controller
        )

    ttk.Button(btn_row, text="🎬 开始生成动画 / 轨迹", command=_start).pack(side='right', padx=4)
    ttk.Button(btn_row, text="关闭", command=dialog.destroy).pack(side='right', padx=4)

    # 辅助函数：切换模式
    def _toggle_mode(mode):
        if mode == "simple":
            advanced_container.pack_forget()
            mode_tip.config(text="💡 简单模式：只显示核心参数，高级选项已隐藏")
            try:
                app.config_data["ui_mode"] = "simple"
                from utils.config import save_config
                save_config(app.config_data)
            except Exception:
                pass
        else:
            advanced_container.pack(fill=tk.X, padx=12, pady=4, before=preview_btn)
            mode_tip.config(text="🔧 高级模式：全部参数可调")
            try:
                app.config_data["ui_mode"] = "advanced"
                from utils.config import save_config
                save_config(app.config_data)
            except Exception:
                pass

    # 初始显示
    _toggle_mode(mode_var.get())
    mode_var.trace_add("write", lambda *_: _toggle_mode(mode_var.get()))

    # 保存对话框引用供预览使用
    app._anim_dialog = dialog
    app._anim_r_list = r_list
    app._anim_p_list = p_list
    app._anim_spacing_var = spacing_var

    # 初始化时加载默认预设
    auto_load = app.config_data.get("preset_auto_load", "")
    if auto_load and auto_load in pm.list_presets():
        preset_var.set(auto_load)
        _load_preset()


def _preview_frame(dialog, r_list, p_list, spacing_var, app):
    import chem.reaction_animation as ra
    import tempfile

    if not dialog or not dialog.winfo_exists():
        return
    reactants = [r_list.get(i) for i in range(r_list.size())]
    products = [p_list.get(i) for i in range(p_list.size())]
    if not reactants or not products:
        messagebox.showwarning("提示", "请先添加反应物和产物", parent=dialog)
        return

    spacing = float(spacing_var.get()) if spacing_var else 5.0
    preview_path = Path(tempfile.gettempdir()) / "preview_frame.png"

    def _task(**kwargs):
        import chem.reaction_animation as ra
        import tempfile
        if len(reactants) == 1 and len(products) == 1:
            r = ra.preview_first_frame(reactants[0], products[0], preview_path,
                                       width=800, height=600)
        else:
            with tempfile.TemporaryDirectory(prefix="ms_preview_") as td:
                tdp = Path(td)
                nR, aR, cR = ra._concat_xyz_files(reactants, translate_spacing=spacing)
                nP, aP, cP = ra._concat_xyz_files(products, translate_spacing=spacing)
                aP2, cP2 = ra._auto_reorder_atoms(aR, cR, aP, cP)
                rx = tdp / "R.xyz"
                px = tdp / "P.xyz"
                rx.write_text(ra._write_xyz(nR, aR, cR), encoding="utf-8")
                px.write_text(ra._write_xyz(nP, aP2, cP2), encoding="utf-8")
                r = ra.preview_first_frame(str(rx), str(px), preview_path,
                                           width=800, height=600)
        def _show():
            if r.get("success"):
                try:
                    if sys.platform == "win32":
                        os.startfile(preview_path)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", preview_path])
                    else:
                        subprocess.Popen(["xdg-open", preview_path])
                except Exception:
                    messagebox.showinfo("预览已生成", f"预览图片保存在:\n{preview_path}", parent=dialog)
            else:
                messagebox.showerror("预览失败", r.get("error", "未知错误"), parent=dialog)
        app.after(0, _show)
    app.helpers.run_task(_task)


def _start_animation(app, dialog, r_list, p_list, steps_var, mode_var, fps_var,
                     spacing_var, smooth_var, res_var, fmt_var, ffmpeg_var,
                     gen_traj_var, traj_var, traj_fmt_var, iqmol_path_var,
                     auto_open_iqmol_var, out_var, solvent_var, qm_method_var,
                     qm_basis_var, qm_d3_var, qm_charge_var, qm_mult_var,
                     qm_mem_var, scan_steps_var, scan_output_var,
                     preset_psi4_var, solvent_cb_var_to_key_map, result_text, controller):
    import chem.reaction_animation as ra
    import subprocess as _sp
    import shutil as _shu

    reactants = [r_list.get(i) for i in range(r_list.size())]
    products = [p_list.get(i) for i in range(p_list.size())]
    out = out_var.get().strip()
    traj = traj_var.get().strip() if gen_traj_var.get() else ""
    if len(reactants) == 0:
        messagebox.showwarning("提示", "请至少添加 1 个反应物文件", parent=dialog)
        return
    if len(products) == 0:
        messagebox.showwarning("提示", "请至少添加 1 个产物文件", parent=dialog)
        return
    for f in reactants + products:
        if not Path(f).exists():
            messagebox.showwarning("提示", f"文件不存在: {f}", parent=dialog)
            return
    if not out and not traj:
        messagebox.showwarning("提示", "请至少选择：可视化输出 或 IQmol 轨迹输出", parent=dialog)
        return

    mode_s = mode_var.get().strip().lower()
    mode = "forward" if mode_s.startswith("forward") else "bounce"
    fmt_s = fmt_var.get().strip().lower()
    fmt = "mp4" if fmt_s.startswith("mp4") else ("png_dir" if fmt_s.startswith("png_dir") else ("none" if fmt_s.startswith("none") else "gif"))
    res_s = res_var.get().strip().lower()
    resolution = "sd" if res_s.startswith("sd") else ("fullhd" if res_s.startswith("fullhd") else "hd")
    traj_fmt = "sdf" if traj_fmt_var.get().strip().lower().startswith("sdf") else "xyz"
    spacing = float(spacing_var.get())

    def _task(**kwargs):
        progress_cb = kwargs.get('_progress_callback')
        msgs = []
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
                    ffmpeg_path=ffmpeg_var.get().strip() or "ffmpeg",
                    fps=max(1, int(fps_var.get())),
                    progress_callback=progress_cb,
                )
            else:
                import tempfile as _tf
                from chem.psi4.utils import _write_xyz
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
                        _rx = _tdp / "R.xyz"
                        _px = _tdp / "P.xyz"
                        _rx.write_text(_write_xyz(_nR, _aR, _cR), encoding="utf-8")
                        _px.write_text(_write_xyz(_nP, _aP2, _cP2), encoding="utf-8")
                        r = ra.generate_reaction_animation(
                            str(_rx), str(_px), out,
                            steps=max(2, int(steps_var.get())),
                            mode=mode, smooth=bool(smooth_var.get()),
                            fmt=fmt, resolution=resolution,
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
                    progress_callback=progress_cb,
                )
            else:
                rr = ra.generate_reaction_multispecies(
                    reactants, products, traj,
                    steps=max(2, int(steps_var.get())),
                    mode=mode, smooth=bool(smooth_var.get()),
                    trajectory_format=traj_fmt,
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
                    resolved = _resolve_iqmol_exe(exe)
                    _sp.Popen([resolved, str(traj_out)])
                    body += "\n\n🚀 已用 IQmol 打开轨迹"
                except Exception as e:
                    body += f"\n\n⚠️  未能打开 IQmol: {e}"
            result_text.configure(state='normal')
            result_text.delete('1.0', tk.END)
            result_text.insert(tk.END, body)
            result_text.configure(state='disabled')
            if any_ok:
                result_dialog = tk.Toplevel(dialog)
                result_dialog.title("✅ 生成完成")
                result_dialog.geometry(fit_dialog_geometry(result_dialog, 480, 350))
                result_dialog.resizable(True, True)
                result_dialog.transient(dialog)
                result_dialog.grab_set()
                tk.Label(result_dialog, text="🎉 反应动画生成完成！",
                         font=('Microsoft YaHei', 14, 'bold'), fg="#0EA288").pack(pady=(20, 10))
                tk.Label(result_dialog, text=body[:200] + ("..." if len(body) > 200 else ""),
                         wraplength=440, justify="left").pack(padx=20, pady=5)
                btn_frame = ttk.Frame(result_dialog)
                btn_frame.pack(pady=15)

                def _open_file():
                    if traj_out and Path(traj_out).exists():
                        _safe_open_file(traj_out)
                    elif viz_out and Path(viz_out).exists():
                        _safe_open_file(viz_out)

                def _open_folder():
                    path = traj_out or viz_out
                    if path:
                        _safe_open_file(str(Path(path).parent))

                ttk.Button(btn_frame, text="📂 打开文件", command=_open_file).pack(side=tk.LEFT, padx=5)
                ttk.Button(btn_frame, text="📁 打开所在文件夹", command=_open_folder).pack(side=tk.LEFT, padx=5)
                ttk.Button(btn_frame, text="关闭", command=result_dialog.destroy).pack(side=tk.LEFT, padx=5)
                try:
                    recent = app.config_data.get("recent_files", [])
                    for p in (traj_out, viz_out):
                        if p and Path(p).exists():
                            if p in recent:
                                recent.remove(p)
                            recent.insert(0, p)
                    app.config_data["recent_files"] = recent[:10]
                    from utils.config import save_config
                    save_config(app.config_data)
                except Exception:
                    pass
                controller.scan_files()
            else:
                messagebox.showerror("失败", body or "未产生任何产出", parent=dialog)
        app.after(0, _after)

    dialog.withdraw()
    app.helpers.run_task(_task)


def _browse_open_multi(listbox, controller):
    init = str(controller.model.work_dir)
    fs = filedialog.askopenfilenames(parent=None, initialdir=init,
        title="选择分子文件（可多选）",
        filetypes=[("分子文件", "*.xyz *.mol *.sdf *.mol2"), ("所有文件", "*.*")])
    if fs:
        for f in fs:
            _ra_add_unique_path(listbox, f)


def _ra_delete_selected(listbox):
    for i in reversed(list(listbox.curselection())):
        listbox.delete(i)


def _ra_add_unique_path(listbox, path):
    path = str(path)
    for i in range(listbox.size()):
        if listbox.get(i) == path:
            return
    listbox.insert(tk.END, path)


def _browse_save(store_var, title, ext, filters):
    init = str(Path(store_var.get()).parent) if store_var.get() else None
    f = filedialog.asksaveasfilename(initialdir=init, title=title,
        defaultextension=ext, filetypes=filters)
    if f:
        store_var.set(f)
