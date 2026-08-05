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

from utils.logger import (
    default_logger as logger,
    get_gui_handler,
    get_context as get_log_context,
    LEVEL_SUCCESS,
)
from utils.dialog_geom import fit_dialog_geometry


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
            win.geometry(fit_dialog_geometry(win, 720, 460))
            win.resizable(True, True)
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
            frm = tk.Frame(win, bg="#161B22", padx=16, pady=16)
            frm.pack(fill=tk.BOTH, expand=True)
            from tkinter import scrolledtext as _st
            tv = _st.ScrolledText(
                frm, font=("Consolas", 11), bg="#0D1117", fg="#E6EDF3",
                relief="flat", bd=0, padx=14, pady=12, wrap="none",
            )
            tv.pack(fill=tk.BOTH, expand=True)
            tv.insert("1.0", txt)
            tv.configure(state="disabled")
            tk.Button(
                frm, text="关闭", command=win.destroy,
                bg="#2DD4BF", fg="#0F1419", font=("Microsoft YaHei UI", 10, "bold"),
                bd=0, relief="flat", padx=18, pady=6, cursor="hand2",
                activebackground="#5EEAD4", activeforeground="#0F1419",
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
                except Exception as _cle:
                    logger.debug("日志面板直接清空失败: %s", _cle)
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
            # 1 秒后把进度条归 0：用默认参数把 progress_var 绑定住，避免后续回调里 self/app 指向变化
            self.app.after(1000, lambda pv=self.app.progress_var: pv.set(0))


    # ---------- 提交任务 ----------
    def run_task(self, func, *args, **kwargs):
        self.app.status_var.set("处理中...")
        self.app.progress_var.set(0)
        # 提交前清掉残留的取消标志
        try:
            self.app.task_manager.clear_cancel()
        except Exception:
            pass

        def progress_cb(percent, msg=""):
            # 协作式取消：用户点了「取消」后，下一次进度上报即抛 InterruptedError，
            # 由 TaskManager worker 捕获并判为 cancelled（任务安全中止）。
            try:
                if self.app.task_manager.is_cancelled():
                    raise InterruptedError("用户取消任务")
            except InterruptedError:
                raise
            except Exception:
                pass
            self.update_progress(percent, msg)

        # 任务进行中 → 显示取消按钮
        try:
            self.app.set_cancel_visible(True)
        except Exception:
            pass
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
    # 问题4修复：Treeview 分批插入（每批 200 行 + after_idle 让出 UI 事件循环），
    # 避免一次性 insert 几千条导致 GUI 卡死。
    _RENDER_BATCH_SIZE = 200

    def render_files(self, entries: list):
        self.app.current_files = entries
        # 一次性清空（delete 对几万条仍是 O(N)，但比逐条 insert 快得多）
        children = self.app.tree.get_children()
        if children:
            self.app.tree.delete(*children)
        total = len(self.app.last_scan_result)
        self.app.filter_count_var.set(f"共 {len(entries)} / {total} 个")
        if not entries:
            return
        # 条目很少（<= 一批）：直接插入，省掉 after 调度开销
        if len(entries) <= self._RENDER_BATCH_SIZE:
            for f in entries:
                self.app.tree.insert("", tk.END,
                                     values=(f['name'], f['status'], f['eng'], f['chn']))
            return
        # 分批：通过 after_idle 调度，每批插入后让出一次主线程 event loop，保持 UI 响应
        tree = self.app.tree
        END = tk.END

        def _insert_batch(start_i: int):
            end_i = min(start_i + self._RENDER_BATCH_SIZE, len(entries))
            for idx in range(start_i, end_i):
                f = entries[idx]
                tree.insert("", END, values=(f['name'], f['status'], f['eng'], f['chn']))
            if end_i < len(entries):
                self.app.after_idle(_insert_batch, end_i)

        self.app.after_idle(_insert_batch, 0)

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

    def get_selected_files(self) -> list[str]:
        """返回选中文件的完整路径列表（绝对/工作目录下的规范化路径）。"""
        work_dir = self.app.controller.model.work_dir
        names = self.get_selected_filenames()
        # 用 _strict_basename 先过一遍路径校验，避免 UI 上意外出现越界路径
        safe: list[str] = []
        for n in names:
            try:
                # 允许子目录（扫描可能选中子目录中的条目）
                sanitized = self.app.controller.model._strict_basename(n, allow_subdir=True)
            except Exception:
                # 极少见：name 不是合法 basename；但 tree 里的值通常来自 scan_files 已经过滤了，这里兜底跳过
                continue
            safe.append(str(Path(work_dir) / sanitized))
        return safe

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
        top.geometry(fit_dialog_geometry(top, 820, 520))
        top.resizable(True, True)
        top.transient(self.app)
        try:
            top.grab_set()
        except Exception:
            pass

        header = ttk.Label(
            top,
            text=f"以下 {len(changes)} 项变更将被应用。点击行首的方框可以取消某一项：",
            font=('Microsoft YaHei', 10, 'bold'),
            foreground='#1f6feb',
        )
        header.pack(padx=12, pady=(12, 6), anchor='w')

        frame = ttk.Frame(top)
        frame.pack(fill='both', expand=True, padx=12, pady=6)
        # "sel" 列就是勾选框：Treeview 没有原生 checkbox，用 ☑/☐ 字符 + 点击切换实现
        cols = ("sel", "idx", "action", "from", "to")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="none")
        import ui.ui_theme as _ut; _ut.bind_treeview_hover(tree)
        widths = {"sel": 46, "idx": 50, "action": 96, "from": 280, "to": 280}
        titles = {"sel": "选", "idx": "#", "action": "操作", "from": "从（原）", "to": "到（新）"}
        for c in cols:
            anchor = 'center' if c in ('sel', 'idx') else 'w'
            tree.heading(c, text=titles[c])
            tree.column(c, width=widths[c], anchor=anchor, stretch=(c in ('from', 'to')))
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
        CHECKED, UNCHECKED = "☑", "☐"
        # iid -> 是否勾选；默认全选
        checked_map: dict[str, bool] = {}

        for i, c in enumerate(changes, 1):
            iid = f"r{i}"
            action = c.get("action", "操作")
            # 每种 action 用**独立的 tag 名**，否则同名 tag 会被后一行的配色覆盖，
            # 导致所有行都变成最后一项的颜色（原实现就是这个 bug）。
            tag = f"act_{action}"
            tree.insert("", _tk.END, iid=iid, values=(
                CHECKED, i, action, c.get("from", ""), c.get("to", "")
            ), tags=(tag,))
            tree.tag_configure(tag, foreground=action_color.get(action, "black"))
            checked_map[iid] = True

        count_var = _tk.StringVar()

        def _refresh_count():
            n = sum(1 for v in checked_map.values() if v)
            count_var.set(f"已选 {n} / 共 {len(changes)} 项")
            try:
                apply_btn.config(text=f"✅ 应用选中的 {n} 项",
                                 state=("normal" if n else "disabled"))
            except Exception:
                pass

        def _set_checked(iid: str, value: bool):
            checked_map[iid] = value
            tree.set(iid, "sel", CHECKED if value else UNCHECKED)

        def _toggle(iid: str):
            _set_checked(iid, not checked_map.get(iid, True))
            _refresh_count()

        def _on_click(event):
            row = tree.identify_row(event.y)
            if not row:
                return
            # 点"选"列切换单项；点其它列不误伤（避免用户想横向拖动时误改勾选）
            if tree.identify_column(event.x) == "#1":
                _toggle(row)

        def _on_space(event):
            row = tree.focus() or (tree.identify_row(event.y) if hasattr(event, 'y') else "")
            if row:
                _toggle(row)

        tree.bind("<Button-1>", _on_click)
        tree.bind("<Double-1>", lambda e: _on_click(e))

        # —— 批量勾选工具条 ——
        tool_row = ttk.Frame(top)
        tool_row.pack(fill='x', padx=12, pady=(2, 0))

        def _all(value: bool):
            for iid in checked_map:
                _set_checked(iid, value)
            _refresh_count()

        def _invert():
            for iid in list(checked_map):
                _set_checked(iid, not checked_map[iid])
            _refresh_count()

        ttk.Button(tool_row, text="全选", width=8, command=lambda: _all(True)).pack(side='left', padx=(0, 4))
        ttk.Button(tool_row, text="全不选", width=8, command=lambda: _all(False)).pack(side='left', padx=4)
        ttk.Button(tool_row, text="反选", width=8, command=_invert).pack(side='left', padx=4)
        ttk.Label(tool_row, textvariable=count_var,
                  font=('Microsoft YaHei', 10, 'bold'), foreground='#1f6feb').pack(side='right')

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
                    from utils.config import save_config as _save
                    _save(self.app.config_data)
                except Exception:
                    pass
            if not result:
                top.destroy()
                return
            # 只把用户仍然勾选的项交给真正的执行函数
            filtered = [c for i, c in enumerate(changes, 1) if checked_map.get(f"r{i}", False)]
            top.destroy()
            try:
                on_confirm(filtered)
            except Exception:
                on_confirm()

        btn_row = ttk.Frame(top)
        btn_row.pack(fill='x', padx=12, pady=12)
        apply_btn = ttk.Button(btn_row, text="✅ 应用选中的项", command=lambda: on_close(True))
        apply_btn.pack(side='right', padx=4)
        ttk.Button(btn_row, text="❌ 取消", command=lambda: on_close(False)).pack(side='right', padx=4)

        _refresh_count()
        # Esc 取消是对话框的通用预期；再把窗口摆到主窗口中央，避免出现在屏幕角落
        top.bind("<Escape>", lambda e: on_close(False))
        tree.bind("<space>", _on_space)
        try:
            top.update_idletasks()
            px, py = self.app.winfo_rootx(), self.app.winfo_rooty()
            pw, ph = self.app.winfo_width(), self.app.winfo_height()
            tw, th = top.winfo_width(), top.winfo_height()
            top.geometry(f"+{max(0, px + (pw - tw) // 2)}+{max(0, py + (ph - th) // 3)}")
        except Exception:
            pass
        try:
            apply_btn.focus_set()
        except Exception:
            pass
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
        try:
            self.app.set_cancel_visible(False)
        except Exception:
            pass
        self.app.status_var.set("就绪")
        if self.app.progress_var.get() >= 100:
            # 用默认参数绑定 progress_var，避免 lambda 延迟时 self/app 变化
            self.app.after(1000, lambda pv=self.app.progress_var: pv.set(0))

    def on_task_cancelled(self):
        """任务被用户取消：复位进度与状态，隐藏取消按钮。"""
        try:
            self.app.set_cancel_visible(False)
        except Exception:
            pass
        self.app.status_var.set("已取消")
        self.app.progress_var.set(0)
        self.on_log("⏹ 任务已取消（部分结果可能未保存）", 'warning')

    def on_task_error(self, error):
        try:
            self.app.set_cancel_visible(False)
        except Exception:
            pass
        self.app.status_var.set("出错")
        self.on_log(f"❌ 后台任务出错: {error}", 'error')
        # 新手友好：把技术错误翻译成大白话弹框
        try:
            from ui.dialogs import Dialogs
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

    # ---------- 环境诊断 / 状态栏指示灯同步（问题三：OB 可用性）----------
    def check_environment(self, *,
                          announce_missing: bool = False,
                          show_dialog: bool = False,
                          parent=None):
        """
        统一环境检查入口：
          - 检测 OpenBabel（pybel + CLI），并更新状态栏右侧 OB 指示灯
          - 检测 PSI4（仅检查 Python 包，不跑任务）
          - 当 announce_missing=True 时：
              * 若 OB 不可用 → 弹「环境诊断」对话框，让用户有机会装 / 手动设路径
          - 当 show_dialog=True 时：无论是否出错都弹环境诊断对话框
        返回 dict：{ob_ok, ob_msg, ob_details, psi4_ok, psi4_msg}
        """
        app = self.app
        try:
            import chem.openbabel_utils as ob_utils
            ob_ok, ob_msg, ob_det = ob_utils.check_openbabel()
        except Exception as _oe:
            ob_ok, ob_msg, ob_det = False, f"check_openbabel 抛错: {_oe}", {}

        psi4_ok = False
        psi4_msg = "未检测 PSI4"
        try:
            import chem.psi4 as psi4  # type: ignore
            _v = getattr(psi4, "__version__", None)
            psi4_ok = True
            psi4_msg = f"psi4 已导入 (version {_v or '未声明'})"
        except Exception as _pe:
            psi4_msg = f"未导入 psi4（不影响文件整理与 OpenBabel 工具）: {_pe}"

        # —— 同步状态栏 OB 指示灯 ——
        try:
            dot = getattr(app, "ob_dot_canvas", None)
            lab = getattr(app, "ob_dot_label", None)
            status_text = getattr(app, "ob_dot_status_var", None)
            fill = "#0EA288" if ob_ok else ("#E5484D")
            if dot is not None:
                try:
                    dot.itemconfig("dot", fill=fill, outline=fill, width=1)
                    # 加一个小高光（更像「灯」）
                    try:
                        dot.delete("hl")
                        sz = dot.winfo_width() or 18
                        r2 = max(4, sz // 5)
                        dot.create_oval(3, 3, 3 + r2, 3 + r2, fill="#FFFFFF",
                                        outline="", tags="hl")
                    except Exception:
                        pass
                except Exception:
                    pass
            if status_text is not None:
                try:
                    status_text.set(("OB 就绪" if ob_ok else "OB 未就绪"))
                except Exception:
                    pass
            if lab is not None:
                try:
                    lab.configure(fg=(("#0EA288") if ob_ok else "#E5484D"))
                except Exception:
                    pass
            # 绑定/重绑定点击 → 打开环境诊断对话框
            def _on_ob_dot_click(*_a):
                try:
                    self.check_environment(announce_missing=False, show_dialog=True)
                except Exception as _e:
                    messagebox.showerror("打开环境诊断失败", str(_e))
            for wid in (dot, lab):
                if wid is not None:
                    try:
                        wid.bind("<Button-1>", _on_ob_dot_click, add="+")
                    except Exception:
                        pass
        except Exception as _se:
            logger.debug("更新 OB 指示灯失败：%s", _se)

        # 日志（避免打扰，只在 OB 不可用且 announce 时 warn）
        try:
            if ob_ok:
                logger.debug("环境检测 OK：%s", ob_msg)
            else:
                logger.warning("环境检测：OpenBabel 不可用：%s  （可在状态栏右侧点击指示灯，或帮助 → 环境诊断解决）",
                               ob_msg)
        except Exception:
            pass

        # 是否需要弹窗
        need_dialog = bool(show_dialog)
        if (not need_dialog) and announce_missing and (not ob_ok):
            need_dialog = True
        if need_dialog:
            try:
                # 延迟一点打开，避免与启动时的主窗口抢焦点
                def _op():
                    try:
                        from ui.dialogs import Dialogs
                        dlg = Dialogs(app, self)
                        try:
                            dlg.show_environment_dialog(parent=parent or app,
                                                        ob_details=ob_det,
                                                        psi4_details={
                                                            "ok": psi4_ok,
                                                            "message": psi4_msg,
                                                        })
                        except Exception:
                            pass
                    except Exception as _de:
                        messagebox.showerror("打开环境诊断失败", str(_de))
                try:
                    app.after(200, _op)
                except Exception:
                    _op()
            except Exception as _de:
                logger.warning("无法打开环境诊断对话框：%s", _de)

        return {
            "ob_ok": ob_ok,
            "ob_msg": ob_msg,
            "ob_details": ob_det or {},
            "psi4_ok": psi4_ok,
            "psi4_msg": psi4_msg,
        }
