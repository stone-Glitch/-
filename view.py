#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口 - 整合所有组件
"""

import sys
import tkinter as tk
from tkinter import ttk

from logger import default_logger as logger
from config import load_config, save_config
from task_manager import TaskManager
from controller import Controller
from dialogs import Dialogs
from app_helpers import AppHelpers
from ui_builder import build_ui, apply_aurora_theme


class MainView(tk.Tk):
    def __init__(self):
        super().__init__()
        self.config_data = load_config()
        self.title("🫧  分子与计算文件管理器 ｜ Aurora Frost")
        self.geometry(self.config_data.get("window_geometry", "1100x780"))
        self.minsize(960, 680)

        # ---- 修复字体模糊：启用高DPI支持 (Windows) ----
        try:
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError) as e:
            logger.debug("启用高DPI支持失败: %s", e)

        # 计算缩放因子
        try:
            screen_width = self.winfo_screenwidth()
            if screen_width >= 1920:
                scale = min(screen_width / 1920.0 * 1.2, 2.0)
                scale = max(1.2, scale)
            else:
                scale = 1.0
            self.tk.call('tk', 'scaling', scale)
            font_size = int(16 * scale) if sys.platform == 'win32' else int(11 * scale)
            default_font = ('Microsoft YaHei UI', font_size) if sys.platform == 'win32' else ('Arial', font_size)
        except (tk.TclError, ValueError, OSError) as e:
            logger.debug("计算缩放因子失败，使用默认字体: %s", e)
            default_font = ('Microsoft YaHei UI', 10) if sys.platform == 'win32' else ('Arial', 11)

        self.option_add('*Font', default_font)
        self.option_add('*Dialog.msg.font', ('Microsoft YaHei UI', 10))

        # —— 先应用 Aurora Frost 全局主题（所有组件样式统一定义）——
        apply_aurora_theme(self)

        style = ttk.Style(self)
        style.configure('.', font=default_font)

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

        # PSI4 配置记忆
        psi4_cfg = self.config_data.get("psi4_config", {})
        self.psi4_last_method = psi4_cfg.get("last_method", "b3lyp")
        self.psi4_last_basis = psi4_cfg.get("last_basis", "6-31g*")
        self.psi4_last_task = psi4_cfg.get("last_task", "energy")

        # 构建界面
        build_ui(self)

        # 自动加载映射并扫描
        if self.mapping_file_var.get():
            self.controller.load_mapping_file()
        self.controller.scan_files()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- 任务回调（转发给 helpers） -----
    def on_task_done(self, result):
        self.helpers.on_task_done(result)

    def on_task_error(self, error):
        self.helpers.on_task_error(error)

    def on_close(self):
        self.task_manager.stop()
        config = {
            "work_dir": self.work_dir_var.get(),
            "mapping_file": self.mapping_file_var.get(),
            "ext_filter": self.ext_filter_var.get(),
            "window_geometry": self.geometry(),
            "psi4_config": {
                "last_method": getattr(self, 'psi4_last_method', 'b3lyp'),
                "last_basis": getattr(self, 'psi4_last_basis', '6-31g*'),
                "last_task": getattr(self, 'psi4_last_task', 'energy')
            }
        }
        save_config(config)
        self.destroy()


if __name__ == "__main__":
    app = MainView()
    app.mainloop()