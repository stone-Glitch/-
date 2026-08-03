#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级工具箱 - 所有高级功能集中入口
包括：分子工具(SMILES搜索/手性/pH加氢/SDF拆分合并/InChIKey)、
波函数性质(HOMO/LUMO/偶极)、构象搜索、二面角扫描、批量属性、
IRC、反应能垒图、Eyring计算、pKa预测、NMR模拟
"""
import os
import sys
import csv
import json
import threading
import weakref
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from pathlib import Path

from logger import default_logger as logger
from task_manager import TaskManager
from .base import _append_text, _clear_text
import openbabel_utils as ob_utils


def show_advanced_tools_dialog(app, controller):
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

    # 底部日志 + 进度条
    bottom = tk.Frame(dialog)
    bottom.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
    progress_var = tk.DoubleVar(value=0.0)
    progress_bar = ttk.Progressbar(bottom, variable=progress_var, maximum=100)
    progress_bar.pack(fill=tk.X, pady=(0, 4))
    log_text = scrolledtext.ScrolledText(bottom, height=10, font=("Consolas", 9),
                                          bg="#1e1e1e", fg="#d4d4d4",
                                          insertbackground="white", relief=tk.SOLID, borderwidth=1)
    log_text.pack(fill=tk.BOTH, expand=True)
    log_text.tag_configure("ok", foreground="#4ade80")
    log_text.tag_configure("warn", foreground="#fbbf24")
    log_text.tag_configure("err", foreground="#f87171")

    # 日志处理器
    import logging as _logging
    import uuid as _uuid
    adv_logger_name = f"adv_tools.{_uuid.uuid4().hex[:8]}"
    adv_logger = _logging.getLogger(adv_logger_name)
    adv_logger.setLevel(_logging.DEBUG)
    adv_logger.propagate = False

    from logger import default_logger as _dflt
    for _h in list(_dflt.handlers):
        try:
            handler_type_name = type(_h).__name__
        except Exception:
            handler_type_name = ""
        if handler_type_name == "GuiLogHandler":
            continue
        try:
            adv_logger.addHandler(_h)
        except Exception:
            pass

    class _TkTextHandler(_logging.Handler):
        def __init__(self, app_ref, text_widget):
            super().__init__(_logging.DEBUG)
            try:
                self._app_ref = weakref.ref(app_ref)
            except TypeError:
                self._app_ref = lambda: app_ref
            self._text = text_widget

        def emit(self, record):
            msg = self.format(record)
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
                        try:
                            print(msg)
                        except Exception:
                            pass
            except Exception:
                pass

        def _write(self, text, tag):
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
        if _dflt.handlers:
            _fmt = getattr(_dflt.handlers[0], "formatter", None)
            if _fmt:
                _text_handler.setFormatter(_fmt)
    except Exception:
        pass
    adv_logger.addHandler(_text_handler)

    def _cleanup_adv_logger_handlers():
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

    dialog.destroy = _safe_dialog_destroy
    dialog.protocol("WM_DELETE_WINDOW", _safe_dialog_destroy)

    def _map_tag_to_level(tag):
        t = (tag or "").lower()
        if t in {"err", "error", "fail", "failed"}:
            return _logging.ERROR
        if t in {"warn", "warning", "skip"}:
            return _logging.WARNING
        if t in {"ok", "success", "done"}:
            return getattr(_logging, "SUCCESS", 25)
        if t in {"debug", "dbg"}:
            return _logging.DEBUG
        return _logging.INFO

    def _log(msg, tag=None):
        try:
            level = _map_tag_to_level(tag)
            adv_logger.log(level, msg)
        except Exception:
            try:
                print(f"[ADV_TOOLS] {msg}")
            except Exception:
                pass

    def _do_set_progress_in_main(perc):
        try:
            progress_var.set(float(perc))
        except Exception:
            pass

    def _progress(perc, msg):
        try:
            app.after(0, lambda p=float(perc): _do_set_progress_in_main(p))
        except Exception:
            pass
        if msg:
            _log(f"⏳ {float(perc):>3.0f}%  {msg}")

    def _sel_path():
        files = app.helpers.get_selected_files()
        if not files:
            _log("⚠️ 请先在主界面左侧列表选中至少 1 个文件（按 Ctrl 多选）", "warn")
            return None
        return files[0]

    def _sel_paths():
        files = app.helpers.get_selected_files()
        if not files:
            _log("⚠️ 请先在主界面左侧列表选中至少 1 个文件", "warn")
            return []
        return files

    def _open_dir_try(path):
        if not path or not os.path.exists(path):
            return
        p = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            if os.name == "nt":
                os.startfile(p)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", p])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            _log(f"打开目录失败：{e}", "warn")

    from task_manager import TaskManager
    _tm = TaskManager(app, controller=None)

    def _submit_work(fn, on_done=None):
        def _on_ok(r):
            try:
                if on_done is not None:
                    on_done(r)
            except Exception as _e_done:
                _log(f"✖ 回调 on_done 异常：{_e_done}", "err")
            finally:
                try:
                    app.after(0, lambda: _do_set_progress_in_main(0.0))
                except Exception:
                    pass

        def _on_err(err_msg):
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
        def _inner(*, _progress_callback=None, _log=None):
            return fn()
        return _inner

    def _help(title, body):
        messagebox.showinfo(title, body, parent=dialog)

    # ============================================================
    # Tab 1：🧪 分子工具（OB）
    # ============================================================
    tab1 = ttk.Frame(nb)
    nb.add(tab1, text="🧪 分子工具（OB）")

    def _row(parent, title, desc, btn_text, help_text, cmd):
        frame = tk.LabelFrame(parent, text=title, padx=8, pady=6,
                              font=("Microsoft YaHei UI", 10, "bold"), fg="#0f4c81")
        frame.pack(fill=tk.X, padx=6, pady=6)
        toprow = tk.Frame(frame)
        toprow.pack(fill=tk.X)
        tk.Label(toprow, text=desc, fg="#333", justify=tk.LEFT, wraplength=780).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(toprow, text="？", width=2, relief=tk.GROOVE,
                  command=lambda: _help(title, help_text)).pack(side=tk.RIGHT, padx=4)
        tk.Button(frame, text=btn_text, width=28, bg="#2563eb", fg="white",
                  activebackground="#1d4ed8", command=cmd).pack(anchor=tk.W, pady=(4, 0))

    # A1-1: SMILES → 相似结构搜索
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
            files = app.helpers.get_selected_files() or []
            _log(f"📚 计算 {len(files)} 个分子的 InChIKey...")
            batch = ob_utils.batch_inchikey([f for f in files])
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
                controller.scan_files()
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
                controller.scan_files()
                try:
                    _open_dir_try(r["data"]["output_path"])
                except Exception:
                    pass
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
                    try:
                        _open_dir_try(d["output_dir"])
                    except Exception:
                        pass
                else:
                    _log(f"✖ {os.path.basename(s)} 失败：{r.get('message','')}", "err")
            controller.scan_files()

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
                try:
                    _open_dir_try(r["data"]["output_path"])
                except Exception:
                    pass
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

    # A1-5: InChIKey 批量生成
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
                    n += 1
            csv_out = os.path.join(os.path.dirname(files[0]), "InChIKey_batch.csv")
            try:
                with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["file", "inchikey", "smiles_if_avail", "formula_if_avail"])
                    for e in rr:
                        w.writerow([e.get("path", ""), e.get("inchikey", ""),
                                    e.get("smiles", ""), e.get("formula", "")])
            except Exception as e_csv:
                _log(f"写 CSV 失败：{e_csv}", "warn")
            _log(f"✅ 已生成 InChIKey {n}/{len(files)}，CSV 已保存到 {os.path.basename(csv_out)}", "ok")
            try:
                _open_dir_try(csv_out)
            except Exception:
                pass

        _submit_work(_work, on_done=_done)

    _row(tab1,
         "⑤ 批量生成 InChIKey + CSV",
         "给所有选中分子生成 InChIKey（骨架层 + 立体层），并导出 CSV。",
         "🔑 批量算 InChIKey",
         "用途：多轮实验/不同电脑之间批量精确比对结构是否相同（比文件名靠谱得多）。",
         _gen_inchikeys)
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