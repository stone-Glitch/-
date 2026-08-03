#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话框模块 - 路由
保持原 Dialogs 类接口不变，实际实现拆分到各子模块。
"""
from .base import (
    friendly_error, _append_text, _clear_text,
    register_dialog_temp_dir, unregister_dialog_temp_dir,
    force_cleanup_dialog_temp_dirs
)
from .common import (
    show_ext_filter_dialog, show_font_size_dialog, show_environment_dialog,
    show_obabel_path_dialog, show_recent_dirs_dialog
)
from .psi4_dialog import show_psi4_dialog
from .openbabel_dialog import show_openbabel_dialog
from .mapping_dialog import show_mapping_manager_dialog, show_mapping_editor_dialog
from .reaction_dialog import show_reaction_animation_dialog
from .history_dialog import show_history_dialog
from .results_dialog import show_results_browser_dialog
from .sync_dialog import show_diff_sync_dialog
from .advanced_tools_dialog import show_advanced_tools_dialog
from .analytics_dialog import show_formula_dialog, export_geometry_csv


class Dialogs:
    """保持原接口，所有方法转发到子模块函数"""
    def __init__(self, app, controller):
        self.app = app
        self.controller = controller

    def _get_app(self):
        return self.app

    def _get_controller(self):
        return self.controller

    # ---- 转发所有对话框方法 ----
    def show_ext_filter_dialog(self):
        show_ext_filter_dialog(self.app, self.controller)

    def show_font_size_dialog(self, parent=None):
        show_font_size_dialog(self.app, parent=parent)

    def show_environment_dialog(self, parent=None, ob_details=None, psi4_details=None):
        show_environment_dialog(self.app, parent=parent, ob_details=ob_details, psi4_details=psi4_details)

    def show_obabel_path_dialog(self, parent=None, on_saved_callback=None):
        show_obabel_path_dialog(self.app, parent=parent, on_saved_callback=on_saved_callback)

    def show_recent_dirs_dialog(self):
        show_recent_dirs_dialog(self.app, self.controller)

    def show_psi4_dialog(self):
        show_psi4_dialog(self.app, self.controller)

    def show_openbabel_dialog(self):
        show_openbabel_dialog(self.app, self.controller)

    def show_mapping_manager_dialog(self):
        show_mapping_manager_dialog(self.app, self.controller)

    def show_mapping_editor_dialog(self):
        show_mapping_editor_dialog(self.app, self.controller)

    def show_reaction_animation_dialog(self):
        show_reaction_animation_dialog(self.app, self.controller)

    def show_history_dialog(self):
        show_history_dialog(self.app, self.controller)

    def show_results_browser_dialog(self):
        show_results_browser_dialog(self.app, self.controller)

    def show_diff_sync_dialog(self):
        show_diff_sync_dialog(self.app, self.controller)

    def show_advanced_tools_dialog(self):
        show_advanced_tools_dialog(self.app, self.controller)

    def show_formula_dialog(self):
        show_formula_dialog(self.app, self.controller)

    def export_geometry_csv(self):
        export_geometry_csv(self.app, self.controller)

    # ---- 工具方法 ----
    @staticmethod
    def friendly_error(err):
        return friendly_error(err)

    def _append_text(self, widget, text, tag=None, see_end=True):
        _append_text(self.app, widget, text, tag, see_end)

    def _clear_text(self, widget):
        _clear_text(self.app, widget)