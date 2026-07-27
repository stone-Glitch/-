#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
应用辅助函数 - 日志、进度、树更新、任务提交、浏览等
线程安全版本

性能优化：
  1) on_log / update_progress 高频 after(0) UI 更新采用「批处理 + 节流」：
     - 短窗口内的多条日志合并为一次 insert
     - 进度条 10ms 内只提交一次实际 UI 更新（避免 1000+/秒 after 队列撑爆）
     - 所有调度仍保持 Tkinter 线程安全（最终操作都在 after 主线程）
"""

import csv
import logging
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from pathlib import Path
import threading

from logger import (
    default_logger as logger,
    get_gui_handler,
    get_context as get_log_context,
    LEVEL_SUCCESS,
)


_LOG_BATCH_WINDOW_MS = 20
_PROGRESS_THROTTLE_MS = 10
_APP_HELPERS_LOCK = threading.Lock()

_LEVEL_MAP = {
    "info":    logging.INFO,
    "success": LEVEL_SUCCESS,
    "warning": logging.WARNING,
    "error":   logging.ERROR,
    "debug":   logging.DEBUG,
}


class AppHelpers:
    def __init__(self, app):
        self.app = app

        self._prog_last: tuple[float, str] = (-1.0, "")
        self._prog_last_flush_ms: int = 0
        self._prog_pending: tuple[float, str] | None = None
        self._prog_flush_scheduled = False

    # ---------- 日志：现在统一走 logging + GuiLogHandler ----------
    def on_log(self, msg, level='info'):
        """记录日志（线程安全）：统一走 logger，GUI 显示由 GuiLogHandler 负责"""
        msg_str = str(msg)
        level_name = str(level).lower()
        if level_name == "success":
            logger.log(LEVEL_SUCCESS, "%s", msg_str)
        elif level_name == "debug":
            logger.debug("%s", msg_str)
        elif level_name == "warning":
            logger.warning("%s", msg_str)
        elif level_name == "error":
            logger.error("%s", msg_str)
        else:
            logger.info("%s", msg_str)

    def _toggle_log_level(self, key: str, var):
        """过滤芯片点击后：更新 GuiLogHandler 的 active 状态并重绘"""
        try:
            handler = get_gui_handler()
            if handler is None:
                return
            level_names = {
                "debug": "DEBUG", "info": "INFO", "success": "SUCCESS",
                "warning": "WARNING", "error": "ERROR",
            }
            lv = level_names.get(key, key.upper())
            handler.set_active(lv, bool(var.get()))
            handler.repaint_all()
        except Exception as e:
            logger.error("切换日志过滤失败: %s", e)

    def _export_log(self, fmt: str = "txt"):
        """导出全部日志为 TXT 或 CSV（包含全部级别，不受过滤影响）"""
        try:
            handler = get_gui_handler()
            if handler is None:
                messagebox.showinfo("导出日志", "日志系统尚未就绪")
                return
            records = handler.get_records_for_export()
            if not records:
                messagebox.showinfo("导出日志", "当前还没有任何日志可导出")
                return
            default_name = f"molmanager_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if fmt == "csv":
                path = filedialog.asksaveasfilename(
                    title="导出日志为 CSV",
                    defaultextension=".csv",
                    initialfile=default_name + ".csv",
                    filetypes=[("CSV 表格", "*.csv"), ("所有文件", "*.*")],
                )
                if not path:
                    return
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["时间", "级别", "级别值", "消息"])
                    for r in records:
                        writer.writerow([r["time"], r["level"], r["level_no"], r["message"]])
                messagebox.showinfo("导出成功", f"已导出 {len(records)} 条日志到：\n{path}")
                logger.success("日志 CSV 已导出 → %s", path)
            else:
                path = filedialog.asksaveasfilename(
                    title="导出日志为 TXT",
                    defaultextension=".txt",
                    initialfile=default_name + ".txt",
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                )
                if not path:
                    return
                with open(path, "w", encoding="utf-8") as f:
                    for r in records:
                        f.write(f"[{r['time']}] [{r['level']:^7s}] {r['message']}\n")
                messagebox.showinfo("导出成功", f"已导出 {len(records)} 条日志到：\n{path}")
                logger.success("日志 TXT 已导出 → %s", path)
        except PermissionError:
            messagebox.showerror("导出失败", "文件被占用或没有写入权限，请换个路径再试。")
        except Exception as e:
            logger.error("导出日志失败: %s", e)
            messagebox.showerror("导出失败", f"{e}")

    def _show_top_perf(self):
        """显示性能 Top10（来自 performance_timer 累计）"""
        try:
            ctx = get_log_context()
            top = ctx.top_perf(10)
            if not top:
                messagebox.showinfo(
                    "性能 Top 10",
                    "还没有性能记录。\n\n💡 提示：跑一次扫描、PSI4 计算或反应动画后再来这里看瓶颈。",
                )
                return
            lines = ["⚡ 当前会话最耗时的 10 个操作（毫秒）\n"]
            lines.append(f"{'排名':<5}{'耗时(ms)':>12}   {'操作名'}")
            lines.append("-" * 68)
            for i, r in enumerate(top, 1):
                meta = ""
                if r.get("meta"):
                    meta = f"  ·  {r['meta']}"
                lines.append(f"{i:<5}{r['ms']:>12,.2f}   {r['name']}{meta}")
            txt = "\n".join(lines)
            win = tk.Toplevel(self.app)
            win.title("⚡ 性能 Top 10")
            win.geometry("720x460")
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
            frm = tk.Frame(win, bg="#F5F7FF", padx=16, pady=16)
            frm.pack(fill=tk.BOTH, expand=True)
            from tkinter import scrolledtext as _st
            tv = _st.ScrolledText(
                frm, font=("Consolas", 11), bg="#FFFFFF", fg="#1A2142",
                relief="flat", bd=0, padx=14, pady=12, wrap="none",
            )
            tv.pack(fill=tk.BOTH, expand=True)
            tv.insert("1.0", txt)
            tv.configure(state="disabled")
            tk.Button(
                frm, text="关闭", command=win.destroy,
                bg="#3B6EFF", fg="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"),
                bd=0, relief="flat", padx=18, pady=6, cursor="hand2",
                activebackground="#2E58D6", activeforeground="#FFFFFF",
            ).pack(pady=(12, 0))
        except Exception as e:
            logger.error("显示性能 Top10 失败: %s", e)

    def clear_log(self):
        """清空日志面板 + 二次确认（防止误操作）"""
        try:
            yes = messagebox.askyesno(
                "确认清空日志",
                "确定要清空全部日志吗？\n\n（本地日志文件不会被删，只是清空显示面板）",
                icon="warning",
                parent=self.app,
            )
            if not yes:
                return
            handler = get_gui_handler()
            if handler is not None:
                handler.clear_all()
            else:
                try:
                    self.app.log_text.configure(state="normal")
                    self.app.log_text.delete("1.0", tk.END)
                    self.app.log_text.configure(state="disabled")
                except Exception:
                    pass
            logger.info("📋 日志面板已清空")
        except Exception as e:
            logger.error("清空日志失败: %s", e)

    # ---------- 进度（10ms 节流，避免每秒 1000 次 UI 刷新） ----------
    def update_progress(self, percent, message=""):
        try:
            p = float(percent)
        except (TypeError, ValueError):
            return
        p = 0.0 if p < 0 else (100.0 if p > 100 else p)
        m = "" if message is None else str(message)
        with _APP_HELPERS_LOCK:
            self._prog_pending = (p, m)
            if not self._prog_flush_scheduled:
                self._prog_flush_scheduled = True
                self.app.after(_PROGRESS_THROTTLE_MS, self._flush_progress)

    def _flush_progress(self):
        with _APP_HELPERS_LOCK:
            pending = self._prog_pending
            self._prog_pending = None
            self._prog_flush_scheduled = False
        if pending is None:
            return
        self._update_progress_ui(pending[0], pending[1])

    def _update_progress_ui(self, percent, message):
        self.app.progress_var.set(percent)
        if message:
            self.app.status_var.set(f"处理中... {message}")
        else:
            self.app.status_var.set(f"处理中... {percent:.0f}%")
        if percent >= 100:
            self.app.status_var.set("就绪")
            self.app.after(1000, lambda: self.app.progress_var.set(0))


    # ---------- 提交任务 ----------
    def run_task(self, func, *args, **kwargs):
        self.app.status_var.set("处理中...")
        self.app.progress_var.set(0)

        def progress_cb(percent, msg=""):
            self.update_progress(percent, msg)

        self.app.task_manager.submit(func, *args, progress_callback=progress_cb, **kwargs)

    # ---------- 文件浏览 ----------
    def browse_file(self, var):
        f = filedialog.askopenfilename(initialdir=str(self.app.controller.model.work_dir))
        if f:
            try:
                rel = os.path.relpath(f, str(self.app.controller.model.work_dir))
                var.set(rel)
            except ValueError:
                var.set(f)

    def browse_dir(self, var):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            var.set(d)

    # ---------- 更新文件树 ----------
    def render_files(self, entries: list):
        self.app.current_files = entries
        for item in self.app.tree.get_children():
            self.app.tree.delete(item)
        for f in entries:
            self.app.tree.insert("", tk.END, values=(f['name'], f['status'], f['eng'], f['chn']))
        total = len(self.app.last_scan_result)
        self.app.filter_count_var.set(f"共 {len(entries)} / {total} 个")

    def apply_filter(self):
        keyword = self.app.filter_keyword_var.get()
        status = self.app.filter_status_var.get()
        ext = self.app.filter_ext_var.get()
        filtered = self.app.controller.model.filter_files(
            self.app.last_scan_result, keyword, status, ext
        )
        self.render_files(filtered)

    def update_tree(self, files):
        self.render_files(files)

    def update_ext_display(self):
        current = self.app.ext_filter_var.get()
        if not current:
            self.app.ext_display_var.set("无")
        else:
            exts = [e.strip() for e in current.split(',') if e.strip()]
            if len(exts) <= 3:
                self.app.ext_display_var.set(", ".join(exts))
            else:
                self.app.ext_display_var.set(", ".join(exts[:2]) + f" ... (+{len(exts)-2})")

    # ---------- 获取选中文件 ----------
    def get_selected_filenames(self):
        selected = []
        for item in self.app.tree.selection():
            values = self.app.tree.item(item, 'values')
            if values:
                selected.append(values[0])
        return selected

    def get_selected_file_info(self):
        selected = self.get_selected_filenames()
        info = []
        for name in selected:
            base, ext = os.path.splitext(name)
            info.append({'name': name, 'base': base, 'ext': ext})
        return info

    # ---------- 预览 + 执行（Dry-run Diff） ----------
    def _is_preview_enabled(self) -> bool:
        try:
            return bool(self.app.config_data.get("preview_before_operation", True))
        except Exception:
            return True

    def show_preview_dialog(self, operation_label: str, changes: list[dict], on_confirm):
        """弹窗预览变更列表，changes 每项: {"from": .., "to": .., "action": "rename/move/delete/copy/convert"}"""
        if not changes:
            on_confirm()
            return
        try:
            from tkinter import ttk, messagebox as mb
        except Exception:
            mb = None
            ttk = None
        import tkinter as _tk
        top = _tk.Toplevel(self.app)
        top.title(f"⚠️ 操作预览 - {operation_label}")
        top.geometry("820x520")
        top.transient(self.app)
        try:
            top.grab_set()
        except Exception:
            pass

        header = ttk.Label(
            top,
            text=f"以下 {len(changes)} 项变更将被应用（操作前请确认，可勾选/取消每项）：",
            font=('Microsoft YaHei', 10, 'bold'),
            foreground='#1f6feb',
        )
        header.pack(padx=12, pady=(12, 6), anchor='w')

        frame = ttk.Frame(top)
        frame.pack(fill='both', expand=True, padx=12, pady=6)
        cols = ("idx", "action", "from", "to")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="none")
        widths = {"idx": 55, "action": 100, "from": 290, "to": 290}
        titles = {"idx": "#", "action": "操作", "from": "从（原）", "to": "到（新）"}
        for c in cols:
            tree.heading(c, text=titles[c])
            tree.column(c, width=widths[c], anchor='w', stretch=(c in ('from', 'to')))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        action_color = {
            "rename": "darkblue", "move": "#6f42c1", "delete": "#cb2431",
            "copy": "#22863a", "convert": "#005cc5", "修复": "darkgreen",
            "恢复": "#6f42c1",
        }
        checked = []
        for i, c in enumerate(changes, 1):
            tag = ("ok",)
            iid = f"r{i}"
            tree.insert("", _tk.END, iid=iid, values=(
                i, c.get("action", "操作"), c.get("from", ""), c.get("to", "")
            ), tags=tag)
            color = action_color.get(c.get("action", ""), "black")
            tree.tag_configure("ok", foreground=color)
            checked.append(iid)

        always_var = _tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(
            top, text="✅ 以后所有操作都直接执行，不再询问（可在顶部设置菜单改回）",
            variable=always_var,
        )
        chk.pack(padx=12, pady=(6, 4), anchor='w')

        def on_close(result: bool):
            if always_var.get():
                try:
                    self.app.config_data["preview_before_operation"] = False
                    from config import save_config as _save
                    _save(self.app.config_data)
                except Exception:
                    pass
            if not result:
                top.destroy()
                return
            included_idx = {int(tree.item(x, "values")[0]) - 1 for x in checked}
            filtered = [c for i, c in enumerate(changes) if i in included_idx]
            top.destroy()
            try:
                on_confirm(filtered)
            except Exception:
                on_confirm()

        btn_row = ttk.Frame(top)
        btn_row.pack(fill='x', padx=12, pady=12)
        ttk.Button(btn_row, text="✅ 应用全部", command=lambda: on_close(True)).pack(side='right', padx=4)
        ttk.Button(btn_row, text="❌ 取消", command=lambda: on_close(False)).pack(side='right', padx=4)
        self.app.wait_window(top)

    def preview_or_run(self, operation_label: str, dryrun_callable, real_callable):
        """dryrun_callable() -> list[dict] or tuple(list[dict], Any)
        real_callable(_filtered_changes: list[dict] | None, *extra) -> anything。
        第一个位置参数 _filtered_changes:
          - None = 没走预览，直接执行全部；
          - list = 预览后用户保留的变更（可能只是 changes 的子集）。空 list 表示用户全取消，real_callable 应返回。
        """
        try:
            dry_result = dryrun_callable()
        except Exception as e:
            self.on_log(f"❌ 预览阶段出错: {e}", 'error')
            return
        if isinstance(dry_result, tuple) and len(dry_result) >= 1:
            changes, extra = (dry_result[0], dry_result[1:])
        else:
            changes, extra = dry_result, ()
        if not isinstance(changes, list):
            changes = []

        def _do_confirm(_filtered=None):
            try:
                # _filtered is list or None; always pass as the first positional argument
                real_callable(_filtered, *extra)
            except TypeError as te:
                # 兼容老版 0 参数 real_callable
                msg = str(te)
                if "positional argument" in msg or "required positional" in msg:
                    try:
                        real_callable(*extra) if extra else real_callable()
                    except Exception as e:
                        self.on_log(f"❌ 执行失败: {e}", 'error')
                else:
                    self.on_log(f"❌ 执行失败: {te}", 'error')
            except Exception as e:
                self.on_log(f"❌ 执行失败: {e}", 'error')

        if self._is_preview_enabled() and changes:
            self.show_preview_dialog(operation_label, changes, _do_confirm)
        else:
            _do_confirm()

    # ---------- 任务回调 ----------
    def on_task_done(self, result):
        self.app.status_var.set("就绪")
        if self.app.progress_var.get() >= 100:
            self.app.after(1000, lambda: self.app.progress_var.set(0))

    def on_task_error(self, error):
        self.app.status_var.set("出错")
        self.on_log(f"❌ 后台任务出错: {error}", 'error')
        # 新手友好：把技术错误翻译成大白话弹框
        try:
            from dialogs import Dialogs
            title, body, hint = Dialogs.friendly_error(error)
            from tkinter import messagebox as _mb
            try:
                _mb.showerror(title, f"{body}\n\n{hint}", parent=self.app)
            except Exception:
                print(f"[{title}] {body}\n{hint}")
        except Exception:
            # 翻译链路失败，fallback 为最朴素 messagebox
            try:
                from tkinter import messagebox as _mb2
                _mb2.showerror("出错啦", f"后台任务出错：\n{error}\n\n可以把这段文字发给开发者。", parent=self.app)
            except Exception:
                pass