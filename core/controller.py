#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller - 协调 Model 和 View
修复：所有任务函数正确传递进度回调
"""
import logging
import os
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, Toplevel, Label, Entry, Button, StringVar, Frame, LEFT, RIGHT, BOTH, X, Y, END
from utils.logger import default_logger as logger, performance_timer
from core.model import MolManagerModel
from utils.constants import SUPPORTED_EXTS
import utils.config as config
from utils.config import save_config


class Controller:
    def __init__(self, app, helpers):
        self.app = app
        self.helpers = helpers
        self.model = MolManagerModel(work_dir=self.app.config_data.get("work_dir", "output"))
        self.model.set_log_callback(self.helpers.on_log)
        if not isinstance(self.app.config_data.get("recent_work_dirs"), list):
            self.app.config_data["recent_work_dirs"] = []
        self.push_recent_work_dir(str(self.model.work_dir))

    # ----- 配置 -----
    def push_recent_work_dir(self, path: str):
        path = str(path)
        if not path:
            return
        recent = self.app.config_data["recent_work_dirs"]
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.app.config_data["recent_work_dirs"] = recent[:config.MAX_RECENT_DIRS]
        save_config(self.app.config_data)

    def get_recent_work_dirs(self) -> list[str]:
        return list(self.app.config_data.get("recent_work_dirs", []))

    def switch_recent_work_dir(self, index: int):
        recent = self.app.config_data.get("recent_work_dirs", [])
        if index < 0 or index >= len(recent):
            return
        path = recent[index]
        if not Path(path).is_dir():
            return
        self.model.work_dir = Path(path)
        self.app.work_dir_var.set(path)
        self.push_recent_work_dir(path)
        self.scan_files()

    def show_recent_dirs_dialog(self):
        self.app.dialogs.show_recent_dirs_dialog()

    def browse_work_dir(self):
        d = filedialog.askdirectory(title="选择工作目录")
        if d:
            self.app.work_dir_var.set(d)
            self.model.work_dir = Path(d)
            self.push_recent_work_dir(d)
            self.scan_files()

    def browse_mapping(self):
        f = filedialog.askopenfilename(title="选择映射文件", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if f:
            self.app.mapping_file_var.set(f)

    def load_mapping_file(self):
        path = Path(self.app.mapping_file_var.get())
        if not path.exists():
            self.helpers.on_log("❌ 请先选择映射文件", 'error')
            return
        try:
            count, dup = self.model.load_mapping_file(path)
            self.app.mapping_count.set(str(count))
            if dup > 0:
                self.helpers.on_log(f"✅ 映射加载成功：{count} 个有效条目，自动跳过 {dup} 个重复项", 'success')
            else:
                self.helpers.on_log(f"✅ 映射加载成功，共 {count} 个条目", 'success')
            self.scan_files()
        except Exception as e:
            self.helpers.on_log(f"❌ 加载映射失败: {e}", 'error')

    # ----- 扫描 -----
    @performance_timer(name="Controller.scan_files", level=logging.DEBUG, min_ms=5.0)
    def scan_files(self):
        def _scan(**kwargs):
            try:
                ext_str = self.app.ext_filter_var.get().strip()
                if ext_str:
                    ext_list = [e.strip().lower() for e in ext_str.split(',') if e.strip()]
                    ext_list = [e if e.startswith('.') else '.' + e for e in ext_list]
                else:
                    ext_list = list(SUPPORTED_EXTS)
                files = self.model.scan_files(ext_filter=ext_list)
                def _after():
                    self.app.last_scan_result = list(files)
                    self.helpers.apply_filter()
                    self.helpers.on_log(f"📁 扫描完成，发现 {len(files)} 个文件", 'info')
                self.app.after(0, _after)
            except Exception as e:
                import traceback
                # 提前获取异常对象和堆栈字符串，避免 lambda 延迟绑定取到已被清理的 e
                err_obj = e
                err_tb = traceback.format_exc()
                self.app.after(0, lambda _e=err_obj, _tb=err_tb:
                               self.helpers.on_log(f"❌ 扫描失败: {_e}\n{_tb}", 'error'))
        self.helpers.run_task(_scan)

    # ----- 修复 -----
    def _collect_rename_preview_changes(self, dry_run_callable) -> list[dict]:
        changes = []
        orig_cb = getattr(self.model, 'log_callback', None)
        def _cap(msg, level='info'):
            if isinstance(msg, str) and ("->" in msg) and ("预览" in msg or "[预览]" in msg):
                try:
                    right = msg.split("]: ", 1)[-1] if "]:" in msg else msg
                    label_part, arrow_part = right.split("->", 1)
                    action_tok = label_part.strip().split(":", 1)[0].strip()
                    frm = label_part.split(":", 1)[1].strip() if ":" in label_part else label_part.strip()
                    to = arrow_part.strip()
                    changes.append({"action": action_tok or "rename", "from": frm, "to": to})
                except Exception:
                    pass
            if orig_cb:
                orig_cb(msg, level)
        self.model.set_log_callback(_cap)
        try:
            dry_run_callable()
        finally:
            self.model.set_log_callback(orig_cb)
        return changes

    def fix_all(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_all(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                results = self.model.fix_all(_filtered_changes=_filtered_changes)
                total = sum(r[0] for r in results.values())
                self.helpers.on_log(f"🎉 一键修复完成！共修复 {total} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("一键修复", _dryrun, _run)

    def rename_by_mapping(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.rename_by_mapping(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.rename_by_mapping(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 映射重命名完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("映射重命名", _dryrun, _run)

    def fix_chinese(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_chinese_names(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_chinese_names(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修复中文名完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修复中文名", _dryrun, _run)

    def fix_all_names(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_all_names(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_all_names(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修复命名错误完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修复命名错误", _dryrun, _run)

    def fix_incorrect_chinese(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_incorrect_chinese(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_incorrect_chinese(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修正中文内容完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修正中文内容", _dryrun, _run)

    # ----- 其他文件操作 -----
    def generate_missing(self):
        def _task(**kwargs):
            missing = self.model.generate_missing_list()
            if missing:
                self.helpers.on_log(f"📋 缺失列表已生成，共 {len(missing)} 个", 'info')
            else:
                self.helpers.on_log("🎉 所有 .mol/.xyz 文件均有映射", 'success')
        self.helpers.run_task(_task)

    def supplement_mol(self):
        def _dryrun() -> list[dict]:
            changes = []
            for entry in self.model.work_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() == '.xyz':
                    dst = self.model.work_dir / f"{entry.stem}.mol"
                    if not dst.exists():
                        changes.append({"action": "convert", "from": entry.name, "to": dst.name})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.supplement_mol(progress_callback=progress_cb)
                self.helpers.on_log(f"🎉 补全 .mol 完成，共 {count} 个", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("补全 mol 文件", _dryrun, _run)

    def organize_by_type(self):
        def _dryrun() -> list[dict]:
            ext_map = {'.mol': 'mol_files', '.xyz': 'xyz_files', '.fchk': 'fchk_files',
                       '.out': 'out_files', '.inp': 'inp_files'}
            changes = []
            for entry in self.model.work_dir.iterdir():
                if not entry.is_file():
                    continue
                ext = entry.suffix.lower()
                if ext not in ext_map:
                    continue
                changes.append({"action": "move", "from": entry.name, "to": f"{ext_map[ext]}/{entry.name}"})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.organize_by_type(
                    progress_callback=progress_cb, _filtered_changes=_filtered_changes
                )
                self.helpers.on_log(f"🎉 按类型整理完成，移动 {count} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("按类型整理", _dryrun, _run)

    def organize_by_basename(self):
        def _dryrun() -> list[dict]:
            changes = []
            for entry in self.model.work_dir.iterdir():
                if not entry.is_file():
                    continue
                changes.append({"action": "move", "from": entry.name, "to": f"{entry.stem}/{entry.name}"})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.organize_by_basename(
                    progress_callback=progress_cb, _filtered_changes=_filtered_changes
                )
                self.helpers.on_log(f"🎉 按文件名分组完成，移动 {count} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("按文件名分组", _dryrun, _run)


    def prefix_rename_dialog(self):
        file_info = self.helpers.get_selected_file_info()
        if not file_info:
            self.helpers.on_log("⚠️ 没有选中任何文件，请先在列表中勾选", 'warning')
            return

        dialog = Toplevel(self.app)
        dialog.title("前缀重命名")
        dialog.transient(self.app)
        dialog.grab_set()
        dialog.resizable(False, False)

        placeholder_text = "可用占位符：{stem} {ext} {mw} {logP} {tpsa} {hbd} {hba} {rotors} {rings} {atoms} {date}"
        Label(dialog, text=placeholder_text, fg="blue", wraplength=520, justify="left").pack(padx=12, pady=(12, 4), anchor="w")

        prefix_var = StringVar()
        entry_frame = Frame(dialog)
        entry_frame.pack(padx=12, pady=8, fill=X)
        Label(entry_frame, text="前缀模板：").pack(side=LEFT)
        entry = Entry(entry_frame, textvariable=prefix_var, width=50)
        entry.pack(side=LEFT, fill=X, expand=True, padx=(6, 0))
        entry.focus_set()

        btn_frame = Frame(dialog)
        btn_frame.pack(padx=12, pady=(4, 12))

        result = {"prefix": None}
        preview_captured = []

        def on_preview():
            prefix = prefix_var.get().strip()
            if not prefix:
                messagebox.showwarning("提示", "请先输入前缀模板", parent=dialog)
                return
            first = sorted(file_info, key=lambda x: x['name'])[0]
            try:
                preview_captured.clear()
                orig_cb = self.model.log_callback

                def capture_log(msg, level='info'):
                    preview_captured.append(msg)
                    if orig_cb:
                        orig_cb(msg, level)

                self.model.set_log_callback(capture_log)
                try:
                    self.model.prefix_rename(prefix, [first], dry_run=True)
                finally:
                    self.model.set_log_callback(orig_cb)

                if preview_captured:
                    details = "\n".join(preview_captured)
                    messagebox.showinfo("预览", details, parent=dialog)
                else:
                    messagebox.showwarning("预览", "未能生成预览结果", parent=dialog)
            except Exception as e:
                messagebox.showerror("预览失败", str(e), parent=dialog)

        def on_ok():
            prefix = prefix_var.get().strip()
            if not prefix:
                messagebox.showwarning("提示", "前缀不能为空", parent=dialog)
                return
            result["prefix"] = prefix
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        Button(btn_frame, text="预览", width=8, command=on_preview).pack(side=LEFT, padx=4)
        Button(btn_frame, text="OK", width=8, command=on_ok).pack(side=LEFT, padx=4)
        Button(btn_frame, text="取消", width=8, command=on_cancel).pack(side=LEFT, padx=4)

        dialog.bind('<Return>', lambda e: on_ok())
        dialog.bind('<Escape>', lambda e: on_cancel())
        self.app.wait_window(dialog)

        prefix = result["prefix"]
        if prefix is None:
            return

        def _task(**kwargs):
            count = self.model.prefix_rename(prefix, file_info)
            self.helpers.on_log(f"🎉 前缀重命名完成，共 {count} 个", 'success')
            self.scan_files()
        self.helpers.run_task(_task)

    def remove_duplicate_files(self):
        if not messagebox.askyesno("确认删除", "将扫描工作目录中的所有文件，删除内容完全相同的重复副本。\n\n是否继续？"):
            return

        def _task(**kwargs):
            progress_cb = kwargs.get('_progress_callback')
            deleted, errors = self.model.remove_duplicate_files(progress_callback=progress_cb)
            self.helpers.on_log(f"🗑️ 删除重复文件完成：共删除 {deleted} 个文件", 'success')
            if errors:
                self.helpers.on_log(f"⚠️ 出现 {len(errors)} 个错误: " + "; ".join(errors[:3]), 'warning')
            self.scan_files()
        self.helpers.run_task(_task)

    def undo_last(self):
        def _task(**kwargs):
            success = self.model.undo_last()
            self.scan_files()
            if not success:
                self.helpers.on_log("⚠️ 没有可撤销的操作或撤销失败", 'warning')
        self.helpers.run_task(_task)

    def redo_last(self):
        def _task(**kwargs):
            result = self.model.redo_last()
            self.helpers.on_log(f"重做完成: 成功 {result['success_count']}, 失败 {result['error_count']}",
                                'info' if result['error_count'] == 0 else 'warning')
            self.scan_files()
        self.helpers.run_task(_task)

    def get_undo_redo_state(self) -> dict:
        return {'undo_count': len(self.model.history), 'redo_count': len(self.model.redo_stack)}

    def delete_selected(self):
        selected = self.helpers.get_selected_filenames()
        if not selected:
            self.helpers.on_log("⚠️ 没有选中文件", 'warning')
            return
        if not messagebox.askyesno("确认删除", f"确定要删除选中的 {len(selected)} 个文件吗？\n注意：文件会被移到工作目录的 .trash_backup 文件夹，可通过「撤销」恢复。"):
            return

        def _task(**kwargs):
            deleted, errors = self.model.delete_files(selected)
            for err in errors:
                self.helpers.on_log(f"❌ {err}", 'error')
            self.helpers.on_log(f"删除完成，共删除 {deleted} 个（可撤销）", 'success')
            self.scan_files()
        self.helpers.run_task(_task)

    @performance_timer(name="Controller.run_fix_by_mode", level=logging.DEBUG, min_ms=5.0)
    def run_fix_by_mode(self):
        mode = self.app.fix_mode_var.get()
        if mode == "一键修复（推荐）":
            self.fix_all()
        elif mode == "映射重命名":
            self.rename_by_mapping()
        elif mode == "修复中文名":
            self.fix_chinese()
        elif mode == "修复命名错误":
            self.fix_all_names()
        elif mode == "修正中文内容":
            self.fix_incorrect_chinese()

    def show_context_menu(self, event):
        item = self.app.tree.identify_row(event.y)
        if item:
            self.app.tree.selection_set(item)
        self.app.context_menu.post(event.x_root, event.y_root)

    def preview_2d_structure(self):
        self.app.dialogs.preview_2d_structure()

    # ================ O1：批量计算 MW/LogP/TPSA 填入新 3 列 ================
    @performance_timer(name="Controller.batch_fill_descriptors", level=logging.DEBUG, min_ms=10.0)
    def batch_fill_descriptors(self, only_selected: bool = False):
        paths = self._get_paths_for_descriptor(only_selected=only_selected)
        if not paths:
            return
        from core.task_manager import TaskManager
        tm = TaskManager(self.app, self)

        def _task(_progress=None, _log=None, **_kw):
            import chem.openbabel_utils as obu
            from pathlib import Path
            total = len(paths)
            ok, fail = 0, 0
            for i, (iid, fpath) in enumerate(paths, 1):
                name = Path(fpath).name
                try:
                    res = obu.calculate_descriptors(fpath)
                    if res.get("success"):
                        d = res.get("descriptors") or {}
                        mw = d.get("molecular_weight")
                        lp = d.get("logP")
                        tp = d.get("tpsa")
                        vals = {}
                        if isinstance(mw, (int, float)) and mw:
                            vals["MW"] = f"{mw:.2f}"
                        if isinstance(lp, (int, float)):
                            vals["LogP"] = f"{lp:.2f}"
                        if isinstance(tp, (int, float)) and tp:
                            vals["TPSA"] = f"{tp:.2f}"
                        if vals:
                            self.app.after(0, lambda _iid=iid, _v=vals: _write_cols(_iid, _v))
                            ok += 1
                        else:
                            fail += 1
                            msg = f"描述符失败（无有效字段）{name}: {res.get('message') or 'descriptors 全部为空'}"
                            if _log:
                                _log(msg, level="warning")
                            logger.warning(msg)
                    else:
                        fail += 1
                        msg = f"描述符失败 {name}: {res.get('message') or '未知原因'}"
                        if _log:
                            _log(msg, level="warning")
                        logger.warning(msg)
                except Exception as e:
                    fail += 1
                    msg = f"描述符异常 {name}: {e}"
                    if _log:
                        _log(msg, level="warning")
                    logger.warning(msg, exc_info=True)
                if _progress:
                    _progress(i, total, f"描述符计算中 {i}/{total}（成功{ok}，失败{fail}）")
            return {"count": total, "ok": ok, "fail": fail}

        def _write_cols(iid, v):
            try:
                for c, vv in v.items():
                    self.app.tree.set(iid, c, vv)
            except Exception:
                pass

        def _on_done(r):
            def _do():
                total = r.get('count', 0)
                ok = r.get('ok', 0)
                fail = r.get('fail', 0)
                if fail == 0:
                    self.app.helpers.on_log(
                        f"✅ 批量描述符完成：共 {total} 个文件，成功 {ok} 个（MW/LogP/TPSA 已写入表格对应列）",
                        "success")
                else:
                    self.app.helpers.on_log(
                        f"⚠️ 批量描述符完成：共 {total} 个文件，成功 {ok} / 失败 {fail}（详情见 WARNING 日志）",
                        "warning")
            self.app.after(0, _do)

        tm.run_async(_task, on_done=_on_done)

    def _get_paths_for_descriptor(self, only_selected: bool = False) -> list[tuple[str, str]]:
        """返回 [(iid, absolute_path)] 列表用于批量算描述符"""
        from pathlib import Path
        work = Path(self.app.work_dir_var.get()).resolve() if self.app.work_dir_var.get() else None
        if work is None:
            return []
        if only_selected:
            items = list(self.app.tree.selection())
        else:
            items = list(self.app.tree.get_children())
        ret: list[tuple[str, str]] = []
        for iid in items:
            try:
                fname = str(self.app.tree.item(iid, "values")[0])
            except Exception:
                continue
            fp = work / fname
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in ('.mol', '.sdf', '.xyz', '.cml', '.inchi', '.smiles', '.smi'):
                continue
            ret.append((iid, str(fp)))
        return ret

    # ================ O3：分子式 / 元素分析弹窗 ================
    def show_formula_dialog(self):
        self.app.dialogs.show_formula_dialog()

    # ================ O6：导出几何参数 CSV ================
    def export_geometry_csv(self):
        self.app.dialogs.export_geometry_csv()

    # ----- 对话框调用 -----
    def show_psi4_dialog(self):
        self.app.dialogs.show_psi4_dialog()

    def show_openbabel_dialog(self):
        self.app.dialogs.show_openbabel_dialog()

    def show_ext_filter_dialog(self):
        self.app.dialogs.show_ext_filter_dialog()

    def show_mapping_manager_dialog(self):
        self.app.dialogs.show_mapping_manager_dialog()

    def show_history_dialog(self):
        self.app.dialogs.show_history_dialog()

    def show_results_browser_dialog(self):
        self.app.dialogs.show_results_browser_dialog()

    def show_diff_sync_dialog(self):
        self.app.dialogs.show_diff_sync_dialog()

    def show_mapping_editor_dialog(self):
        self.app.dialogs.show_mapping_editor_dialog()

    def show_reaction_animation_dialog(self):
        self.app.dialogs.show_reaction_animation_dialog()

    # ================ 🛠️ 高级工具箱 ================
    def show_advanced_tools_dialog(self):
        self.app.dialogs.show_advanced_tools_dialog()