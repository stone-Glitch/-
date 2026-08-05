#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口 - 整合所有组件
"""

import sys
import tkinter as tk
from tkinter import ttk

from utils.logger import default_logger as logger
from utils.config import load_config, save_config
from core.task_manager import TaskManager
from core.controller import Controller
from ui.dialogs import Dialogs
from ui.app_helpers import AppHelpers
from ui.ui_builder import build_ui, apply_aurora_theme as _apply_aurora_theme  # noqa: F401
from ui.wizard import maybe_show_first_run_wizard  # 首次使用向导
# _apply_aurora_theme 不再调用（新版清爽扁平 UI 统一用 LabelFrame + ttk 原生样式，
# 不再依赖 Aurora Frost 的 Canvas / 粒子装饰），但保留导入避免旧插件/脚本误用。
# 如需启用旧版主题，可在 build_ui() 之前手工调用 _apply_aurora_theme(self)。


def _clamp_geometry_to_screen(root, geom: str, min_w: int = 960, min_h: int = 680) -> str:
    """
    把「上次保存的窗口几何」钳制回当前屏幕内。

    为什么必须做：用户可能上次在外接大屏（如 1920x1080）用过，配置里存下
    "1920x1005+-207+-30"；换回笔记本小屏（如 1536x864）再打开时：
      - 窗口比屏幕大 → 右侧/底部内容永久看不到；
      - y 为负 → **标题栏跑到屏幕上方之外，用户无法用鼠标拖动窗口**，只能改配置文件自救。
    原先只判断「窗口中心点是否落在屏幕内」，上面这种几何中心点仍在屏内，检查会放行，
    所以必须改成对 宽/高/x/y 全部做边界钳制。
    """
    import re as _re
    try:
        m = _re.match(r"^\s*(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?\s*$", str(geom))
        if not m:
            return geom
        w, h = int(m.group(1)), int(m.group(2))
        has_pos = m.group(3) is not None
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        if sw <= 0 or sh <= 0:
            return geom
        # 给任务栏 + 窗口标题栏留出余量，避免"贴满屏幕反而看不到底部按钮"
        RESERVED_H = 80
        w = max(1, min(w, max(min_w, sw - 20)))
        h = max(1, min(h, max(min_h, sh - RESERVED_H)))
        if not has_pos:
            return f"{w}x{h}"
        x, y = int(m.group(3)), int(m.group(4))
        # x/y 钳到 [0, 屏幕尺寸-窗口尺寸]，保证标题栏一定可见、窗口一定在屏内
        x = max(0, min(x, max(0, sw - w)))
        y = max(0, min(y, max(0, sh - h - 40)))
        return f"{w}x{h}+{x}+{y}"
    except Exception:
        return geom


class MainView(tk.Tk):
    def __init__(self):
        try:
            super().__init__()
            self.config_data = load_config()
            self.title("🫧  分子与计算文件管理器 ｜ Aurora Frost")
            _saved_geom = self.config_data.get("window_geometry", "1100x780")
            _safe_geom = _clamp_geometry_to_screen(self, _saved_geom)
            if _safe_geom != _saved_geom:
                logger.info("窗口几何 %s 超出当前屏幕，已自动修正为 %s", _saved_geom, _safe_geom)
            self.geometry(_safe_geom)
            self.minsize(960, 680)

            # ---- 修复字体模糊：**强制**启用高DPI支持 (Windows) ----
            # 问题一补充：SetProcessDPIAware 可能失败，尝试 Win10+ 的 Per-Monitor V2；
            # 即便失败也继续走 fallback，不中断启动。
            try:
                if sys.platform == 'win32':
                    import ctypes
                    try:
                        # 优先：Per-Monitor V2（Win10 1703+），应对不同屏幕不同 DPI
                        ctypes.windll.shcore.SetProcessDpiAwareness(2)
                    except (OSError, AttributeError, ValueError):
                        try:
                            ctypes.windll.shcore.SetProcessDpiAwareness(1)
                        except (OSError, AttributeError, ValueError):
                            ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError) as e:
                logger.debug("启用高DPI支持失败（非致命，字体仍按配置放大）: %s", e)

            # ---- 问题一：全局字体基线（来自 config.font_size + font_follow_dpi）----
            # 计算逻辑交给 ui_builder.resolve_font_specs，保证 MainView 与子控件使用同一套规则。
            # 这里显式调用一次，把 app._fonts 和 option_add 先准备好，后续 build_ui 内会再调用一次
            # （第二次调用命中的是同一 config，结果一致，幂等。）
            try:
                from ui.ui_builder import resolve_font_specs as _resolve_fonts
                _F = _resolve_fonts(self)
                _default_font = _F["BASE"]
            except Exception as _fe:
                logger.debug("从 config 计算字体基线失败，使用默认字体: %s", _fe)
                _default_font = ('Microsoft YaHei UI', 12) if sys.platform == 'win32' else ('Arial', 12)

            # tk.call('tk', 'scaling') 也要调大，保证 ttk 控件的内边距/图标也随之变大
            try:
                # 目标 pt 值：_default_font[1]。win32 默认 96DPI 下，tk scaling 点/英寸≈1.0 对应约 9pt；
                # 按比例换算：我们的默认 14pt → scaling ≈ 14/9 ≈ 1.56
                pt = int(_default_font[1]) if len(_default_font) > 1 else 12
                _s = max(1.1, min(2.2, pt / 9.0))
                self.tk.call('tk', 'scaling', _s)
            except (tk.TclError, ValueError, OSError) as e:
                logger.debug("tk scaling 设置失败，使用默认: %s", e)

            self.option_add('*Font', _default_font)
            self.option_add('*Dialog.msg.font', _default_font)
            self.option_add('*Menu.Font',    _default_font)
            self.option_add('*Button.Font',  _default_font)
            self.option_add('*Label.Font',   _default_font)
            self.option_add('*Entry.Font',   _default_font)
            self.option_add('*Text.Font',    _default_font)

            # 全局 ttk 样式 + 字体（使用当前平台默认 clam 主题 + 默认 background/fieldbackground，
            # 不强制 Aurora Frost 的 Canvas/粒子美学，保持清爽扁平风格）
            try:
                _s = ttk.Style(self)
                try:
                    _s.theme_use("clam")
                except tk.TclError:
                    pass
                _s.configure('.', font=_default_font)
            except Exception:
                pass
            style = ttk.Style(self)
            style.configure('.', font=_default_font)

            # 核心组件（顺序很重要）
            self.task_manager = TaskManager(self)
            self.task_manager.start()

            # 1. 先创建 AppHelpers
            self.helpers = AppHelpers(self)

            # 2. 再创建 Controller（传入 helpers）
            self.controller = Controller(self, self.helpers)

            # 3. 最后创建 Dialogs
            self.dialogs = Dialogs(self, self.controller)

            # 变量
            self.work_dir_var = tk.StringVar(value=str(self.controller.model.work_dir))
            self.mapping_file_var = tk.StringVar(value=self.config_data.get("mapping_file", ""))
            self.ext_filter_var = tk.StringVar(value=self.config_data.get("ext_filter", ".mol,.xyz,.fchk,.out,.inp"))
            self.mapping_count = tk.StringVar(value="未加载")
            self.current_files = []
            self.progress_var = tk.DoubleVar(value=0.0)
            self.last_scan_result = []
            self.filter_keyword_var = tk.StringVar(value="")
            self.filter_status_var = tk.StringVar(value="全部")
            self.filter_ext_var = tk.StringVar(value="全部")
            self.filter_count_var = tk.StringVar(value="共 0 / 0 个")
            # 设置-菜单栏：是否整理前先预览（从 config 载入）
            try:
                _prev_default = bool(self.config_data.get("preview_before_operation", True))
            except Exception:
                _prev_default = True
            self.preview_before_operation_var = tk.BooleanVar(value=_prev_default)

            # PSI4 配置记忆
            psi4_cfg = self.config_data.get("psi4_config", {})
            self.psi4_last_method = psi4_cfg.get("last_method", "b3lyp")
            self.psi4_last_basis = psi4_cfg.get("last_basis", "6-31g*")
            self.psi4_last_task = psi4_cfg.get("last_task", "energy")

            # 构建界面（清爽扁平布局，稳定无 Canvas 嵌套）
            build_ui(self)

            # ---- 启动后强制刷新布局三板斧（无 Aurora Canvas，纯 Frame/LabelFrame 布局）----
            try:
                self.update_idletasks()
            except Exception:
                pass
            try:
                self.geometry(self.geometry())   # 强制 <Configure>，让 paned/tree/log 完成权重分配
            except Exception:
                pass
            try:
                self.after(50, lambda: self.update_idletasks())
            except Exception:
                pass
            try:
                self.after(200, lambda: self.update_idletasks())
            except Exception:
                pass

            # 把 GUI 日志面板挂到根 logger（必须在 build_ui 之后，因为 log_text 此时存在）
            from utils.logger import attach_gui_handler
            attach_gui_handler(lambda: self)

            # —— 问题二：日志空白修复。在 GUI handler 挂载后，立刻输出 2 条 welcome banner，
            # 再回放 setup_logging → 此刻之间的日志（attach_gui_handler 内部已回放），
            # 保证用户第一次打开程序永远能在日志面板看到内容。
            try:
                _wd = str(self.work_dir_var.get() or "(未设置工作目录)")
                logger.success("✅ 欢迎使用 分子管理器！工作目录：%s", _wd)
                logger.info(
                    "💡 新手路径：① 左上「浏览…」选择工作目录 → ② 点「🔧 一键修复全部」 → ③ 点「📂 按类型整理」归档"
                )
                logger.info(
                    "💡 查看依赖状态：右下状态栏有 OB 指示灯，点击可一键诊断/手动设置 OpenBabel 路径。"
                )
            except Exception:
                pass

            # —— 问题三：首次环境检查（300ms 后台跑，不卡界面）——
            # 写完欢迎日志后，延迟调用 helpers.check_environment()，
            # 该方法会填状态栏的 OB 指示灯颜色和文字（绿/红），如果 OB 不可用会弹诊断。
            def _env_check_and_apply_status():
                try:
                    fn = getattr(self.helpers, "check_environment", None)
                    if callable(fn):
                        fn(announce_missing=False)
                except Exception as _env_e:
                    try:
                        logger.debug("环境检查调用失败（非致命）：%s", _env_e)
                    except Exception:
                        pass
            self.after(350, _env_check_and_apply_status)

            # 如果 OB 严重不可用（用户连 Python 包都没装），在 800ms 后主动弹「环境设置」对话框，
            # 引导用户安装或手动选择路径（不阻塞，用户可以关了继续用基础功能）。
            def _maybe_pop_env_dialog():
                try:
                    fn = getattr(self.helpers, "maybe_prompt_environment_on_first_run", None)
                    if callable(fn):
                        fn()
                except Exception:
                    pass
            self.after(800, _maybe_pop_env_dialog)

            # -------- 延迟初始化：让主窗口先完整渲染，再在 300ms 后加载映射 + 扫描 --------
            # 这样能显著降低「点击 exe → 看到可用界面」的感知时间，避免文件列表空白带来的
            # 「启动慢」心理感受；而且映射/扫描本身都是后台任务，只是把 submit 的时机后延。
            def _delayed_init():
                try:
                    if self.mapping_file_var.get():
                        self.controller.load_mapping_file()
                except Exception as _init_e:
                    try:
                        logger.debug("延迟加载映射失败（非致命）: %s", _init_e)
                    except Exception:
                        pass
                try:
                    self.controller.scan_files()
                except Exception as _init_e:
                    try:
                        logger.warning("延迟扫描提交失败: %s", _init_e)
                    except Exception:
                        pass

            self.after(300, _delayed_init)

            # -------------------- 首次使用向导（非阻塞，仅当 config_data["first_run"] 不是 False 时弹） --------------------
            maybe_show_first_run_wizard(self)

            # -------- 启动后把窗口带到顶层（修复 splash 关闭后主窗口被其他应用遮挡 / 屏幕外 / 最小化 问题）--------
            # ⚠️ 重要：不能在 __init__ 里直接 lift() —— 此时窗口尚未 map（WM 还没绘制），
            #   lift/focus_force 都会被吞掉；必须用 after(50, …) 在下一轮事件循环里做，
            #   同时用「瞬时 topmost=True → 100ms 后 topmost=False」的技巧，保证不管 splash
            #   还是其他应用窗口在前面，主窗口一定能被用户第一眼看到。
            def _bring_to_front():
                try:
                    self.deiconify()
                except Exception:
                    pass
                try:
                    self.update_idletasks()
                except Exception:
                    pass
                try:
                    # 先强制 topmost（跨平台最可靠的置顶手段），然后 100ms 后再取消，
                    # 避免用户后续操作中窗口永远顶在最前
                    self.attributes("-topmost", True)
                except Exception:
                    pass
                try:
                    self.lift()
                except Exception:
                    pass
                try:
                    self.focus_force()
                except Exception:
                    pass
                try:
                    self.focus_set()
                except Exception:
                    pass

                def _undo_topmost():
                    try:
                        self.attributes("-topmost", False)
                    except Exception:
                        pass
                try:
                    self.after(100, _undo_topmost)
                except Exception:
                    # after 不可用的话立刻直接取消（退化到一次性 lift）
                    _undo_topmost()

                # 多显示器/负坐标兼容：如果窗口几何中心不在任一屏幕范围内，强制居中
                try:
                    import re as _re
                    geom = self.geometry()  # 格式 "WxH+X+Y"
                    m = _re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geom)
                    if m:
                        w, h, x, y = (int(x) for x in m.groups())
                        sw = self.winfo_screenwidth()
                        sh = self.winfo_screenheight()
                        cx, cy = x + w // 2, y + h // 2
                        if not (0 <= cx < sw and 0 <= cy < sh):
                            nx = max(0, (sw - w) // 2)
                            ny = max(0, (sh - h) // 2)
                            self.geometry(f"{w}x{h}+{nx}+{ny}")
                except Exception:
                    pass

            try:
                self.after(50, _bring_to_front)
            except Exception:
                # after 不可用：退化到同步执行
                try:
                    _bring_to_front()
                except Exception:
                    pass

            # -------- 全局快捷键绑定（易用性改进）--------
            # 注意：F1 帮助里列出的每一个键都必须真实绑定在这里，
            # 否则用户按了没反应会直接怀疑「这软件是不是坏了」。
            self.bind("<Control-g>", lambda e: self._on_ctrl_g())
            self.bind("<Control-s>", lambda e: self._on_ctrl_s())
            self.bind("<F1>", lambda e: self._show_help())
            self.bind("<Control-z>", self._on_ctrl_z)
            self.bind("<Control-y>", self._on_ctrl_y)
            self.bind("<Control-Shift-Z>", self._on_ctrl_y)   # 另一种常见的「重做」习惯
            self.bind("<F5>", self._on_f5)
            self.bind("<Control-f>", self._on_ctrl_f)

            self.protocol("WM_DELETE_WINDOW", self.on_close)
        except Exception as e:
            import traceback as _tb
            # 把完整堆栈先打进日志，这样即使用户看不到弹窗也能在日志文件里查
            logger.error("MainView 初始化失败: %s", e)
            logger.error("堆栈:\n%s", _tb.format_exc())
            # 关键：如果 super().__init__() 已经执行成功（也就是 Tk 根已经创建），
            # 那么此时半初始化的 MainView 仍然是一个活的 Tk 窗口，如果不 destroy，
            # 它会作为「看不见的主窗口」留在 Tk 解释器里，让后续 messagebox.showerror
            # 选它做父窗口，导致错误对话框也看不见。
            try:
                # 用 Tkinter 的标准方式判断 tk 解释器是否还活着
                if bool(getattr(self, 'tk', None)):
                    try:
                        self.destroy()
                    except Exception:
                        pass
            except Exception:
                pass
            # 再原样抛出去，让 main.py 的 load_main 捕获，弹出 showerror 友好提示
            raise

    # ----- 快捷键处理 -----
    def _on_ctrl_g(self):
        """Ctrl+G: 打开反应动画对话框"""
        if hasattr(self.controller, "show_reaction_animation_dialog"):
            self.controller.show_reaction_animation_dialog()
        else:
            self.helpers.on_log("⚠️ 反应动画功能未加载", "warning")

    def _on_ctrl_s(self):
        """Ctrl+S: 保存当前配置"""
        try:
            self._save_config()
            self.helpers.on_log("✅ 配置已保存", "success")
        except Exception as e:
            self.helpers.on_log(f"❌ 保存配置失败: {e}", "error")

    def _is_text_input_focused(self) -> bool:
        """当前焦点是否落在文本输入类控件上。

        用于让 Ctrl+Z / Ctrl+F 这类键在输入框里保持「编辑文本」的原生语义，
        不要被全局的「撤销文件操作」抢走——否则用户在改路径时按 Ctrl+Z
        会突然回滚一批文件，非常吓人。
        """
        try:
            w = self.focus_get()
        except Exception:
            return False
        if w is None:
            return False
        try:
            return w.winfo_class() in (
                "Entry", "TEntry", "Text", "ScrolledText",
                "TCombobox", "Spinbox", "TSpinbox", "Listbox",
            )
        except Exception:
            return False

    def _on_ctrl_z(self, event=None):
        """Ctrl+Z: 撤销上一步文件操作（输入框内则交回给输入框）"""
        if self._is_text_input_focused():
            return None
        try:
            self.controller.undo_last()
        except Exception as e:
            self.helpers.on_log(f"❌ 撤销失败: {e}", "error")
        return "break"

    def _on_ctrl_y(self, event=None):
        """Ctrl+Y / Ctrl+Shift+Z: 重做被撤销的文件操作"""
        if self._is_text_input_focused():
            return None
        try:
            self.controller.redo_last()
        except Exception as e:
            self.helpers.on_log(f"❌ 重做失败: {e}", "error")
        return "break"

    def _on_f5(self, event=None):
        """F5: 重新扫描工作目录"""
        try:
            self.controller.scan_files()
        except Exception as e:
            self.helpers.on_log(f"❌ 刷新失败: {e}", "error")
        return "break"

    def _on_ctrl_f(self, event=None):
        """Ctrl+F: 把光标送到文件列表上方的搜索框"""
        try:
            entry = getattr(self, "filter_keyword_entry", None)
            if entry is not None:
                # 搜索框在「📁 文件管理」页，先切过去再聚焦，否则用户看不到光标在哪
                nb = getattr(self, "main_notebook", None)
                if nb is not None:
                    try:
                        nb.select(0)
                    except Exception:
                        pass
                entry.focus_set()
                entry.select_range(0, "end")
        except Exception as e:
            logger.debug("Ctrl+F 聚焦搜索框失败: %s", e)
        return "break"

    def _show_help(self):
        """F1: 显示快捷键帮助（此处列出的键必须与实际 bind 一一对应）"""
        help_text = """⌨️ 快捷键帮助

F5              重新扫描工作目录
Ctrl+F          跳到文件搜索框
Ctrl+Z          撤销上一步文件操作
Ctrl+Y          重做（也可用 Ctrl+Shift+Z）
Ctrl+G          打开反应动画对话框
Ctrl+S          保存当前配置
F1              显示此帮助

提示：在输入框里编辑文字时，Ctrl+Z 仍是普通的文本撤销，
不会误触发文件操作回滚。"""
        try:
            from tkinter import messagebox
            messagebox.showinfo("快捷键帮助", help_text, parent=self)
        except Exception:
            pass

    def _save_config(self):
        """保存当前配置到文件（内部使用）"""
        config = dict(self.config_data)
        config.update({
            "work_dir": self.work_dir_var.get(),
            "mapping_file": self.mapping_file_var.get(),
            "ext_filter": self.ext_filter_var.get(),
            "window_geometry": self.geometry(),
            "psi4_config": {
                "last_method": getattr(self, 'psi4_last_method', 'b3lyp'),
                "last_basis": getattr(self, 'psi4_last_basis', '6-31g*'),
                "last_task": getattr(self, 'psi4_last_task', 'energy')
            },
        })
        if hasattr(self, "preview_before_operation_var"):
            config["preview_before_operation"] = bool(self.preview_before_operation_var.get())
        save_config(config)

    # ----- 任务回调（转发给 helpers） -----
    def on_task_done(self, result):
        self.helpers.on_task_done(result)

    def on_task_error(self, error):
        self.helpers.on_task_error(error)

    def on_task_cancelled(self):
        """任务被用户取消（由 TaskManager 路由过来）。"""
        self.helpers.on_task_cancelled()

    def set_cancel_visible(self, visible: bool):
        """显示 / 隐藏状态栏的「取消」按钮。"""
        try:
            btn = getattr(self, "cancel_button", None)
            if btn is None:
                return
            if visible:
                btn.pack(side=tk.RIGHT, padx=4, pady=4)
            else:
                btn.pack_forget()
        except Exception:
            pass

    # ===== 问题三 + 用户需求：菜单栏入口 =====
    def show_environment_dialog_from_menu(self) -> None:
        """菜单栏「帮助 → 🧪 环境诊断」调用：直接打开诊断对话框。"""
        try:
            self.helpers.check_environment(show_dialog=True)
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("打开失败", f"无法打开环境诊断对话框：\n{e}")
            except Exception:
                pass

    def show_font_size_dialog_from_menu(self) -> None:
        """菜单栏「设置 → 字体大小…」调用：打开滑块对话框。"""
        try:
            from ui.dialogs import Dialogs
            dlg = Dialogs(self, self.controller)
            dlg.show_font_size_dialog(parent=self)
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("打开失败", f"无法打开字体大小设置：\n{e}")
            except Exception:
                pass

    def on_close(self):
        # ———— 关闭拦截：若有任务正在运行，先二次确认，避免杀掉正在写的文件 ————
        try:
            if getattr(self, "task_manager", None) is not None and self.task_manager.is_busy():
                from tkinter import messagebox
                if not messagebox.askyesno(
                    "有任务正在运行",
                    "当前有文件操作或计算任务正在进行中。\n退出可能会中断操作，"
                    "导致部分文件未保存或产物不完整。\n\n确定要退出吗？",
                    parent=self,
                ):
                    return  # 用户选择不退出 → 取消关闭
        except Exception:
            pass

        # ———— 先让 Tk 事件循环处理完所有 pending 的 after/repaint 回调，
        #    防止后台线程刚塞进来的 after(0, cb) 在 destroy 之后执行触发 TclError ————
        try:
            for _ in range(2):
                self.update_idletasks()
                self.update()
        except Exception:
            pass
        try:
            # stop() 内部会等 5 秒（100ms × 50 次），大部分情况下几秒内 worker 就会正常退出
            self.task_manager.stop()
        except Exception as e:
            try:
                logger.warning("关闭 TaskManager 异常: %s", e)
            except Exception:
                pass
        # 再给一次事件循环时间，把 _poll_results 里最后几条 after(0, cb) 跑掉（如果还在）
        try:
            for _ in range(2):
                self.update_idletasks()
                self.update()
        except Exception:
            pass
        # —— 先基于「已 deep_merge 过」的 config_data 来保存，避免只存一半字段丢失
        #    font_size / obabel_path / recent_work_dirs / preview_before_operation / font_follow_dpi 等 ——
        try:
            config = dict(self.config_data) if isinstance(self.config_data, dict) else {}
        except Exception:
            config = {}
        # 再覆盖需要实时同步的字段（work_dir、mapping_file 等是运行中会变的）
        config.update({
            "work_dir": self.work_dir_var.get(),
            "mapping_file": self.mapping_file_var.get(),
            "ext_filter": self.ext_filter_var.get(),
            "window_geometry": self.geometry(),
            "psi4_config": {
                "last_method": getattr(self, 'psi4_last_method', 'b3lyp'),
                "last_basis": getattr(self, 'psi4_last_basis', '6-31g*'),
                "last_task": getattr(self, 'psi4_last_task', 'energy')
            },
        })
        # 保存 preview 开关（菜单栏可能改过）
        try:
            if hasattr(self, "preview_before_operation_var"):
                config["preview_before_operation"] = bool(self.preview_before_operation_var.get())
        except Exception:
            pass
        save_config(config)
        # M-3 修复：关闭前同步跑一遍过期临时目录清理（>6 小时的就清掉）
        # 同步跑清理线程是同步执行耗时很短，不会卡死（几百毫秒；保证程序退出之前能清得更干净。
        try:
            _cleanup_stale_tempdirs(max_age_seconds=6 * 3600)
        except Exception as e:
            try:
                logger.debug("关闭时清理临时目录失败：%s", e)
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = MainView()
    app.mainloop()