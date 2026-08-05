# -*- coding: utf-8 -*-
"""
对话框几何适配工具。

问题：UI 里大量对话框使用写死的 geometry("WxH")，既不钳制到屏幕，也不允许缩放。
在比设计屏小的屏幕上，窗口比屏幕还大 → 标题栏 / 底部按钮被屏幕边缘直接裁掉
（用户原话"有些窗口还是直接裁切"）。

fix：
  - fit_dialog_geometry(dialog, w, h)：把请求尺寸钳制进父窗口所在屏幕，并相对父窗口居中；
    返回可直接喂给 dialog.geometry(...) 的字符串。
  - make_scrollable_body(dialog)：把对话框主体包进「画布 + 垂直滚动条」，返回 (canvas, body)；
    调用方把所有内容 pack/grid 进 body，footer（按钮栏）自行 pack 在 body 之外，保证始终可见。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def fit_dialog_geometry(dialog, w: int, h: int, min_w: int = 420, min_h: int = 300) -> str:
    """把对话框几何钳制到父窗口所在屏幕内，并相对父窗口居中。"""
    def _screen():
        try:
            return dialog.winfo_screenwidth(), dialog.winfo_screenheight()
        except Exception:
            return 0, 0

    def _parent_rect():
        parent = getattr(dialog, "master", None)
        if parent is not None and hasattr(parent, "winfo_exists") and parent.winfo_exists():
            try:
                return parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height()
            except Exception:
                return None
        return None

    sw, sh = _screen()
    if sw <= 0 or sh <= 0:
        return f"{int(w)}x{int(h)}"

    RESERVED = 60  # 给任务栏 / 标题栏留余量
    w = max(min_w, min(int(w), sw - 20))
    h = max(min_h, min(int(h), sh - RESERVED))

    rect = _parent_rect()
    if rect is not None:
        px, py, pw, ph = rect
        x = max(0, min(px + (pw - w) // 2, sw - w))
        y = max(0, min(py + (ph - h) // 2, sh - h - 30))
    else:
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
    return f"{w}x{h}+{x}+{y}"


def make_scrollable_body(dialog, padx: int = 12, pady: int = 12):
    """在 dialog 中创建可滚动内容区。

    用法：
        canvas, body = make_scrollable_body(dialog)
        # 之后所有内容 pack/grid 进 body
        # footer（按钮）在 dialog 上单独 pack(side=tk.BOTTOM, fill=tk.X)
    返回 (canvas, body_frame)。
    """
    outer = tk.Frame(dialog)
    outer.pack(fill=tk.BOTH, expand=True, padx=padx, pady=pady)

    canvas = tk.Canvas(outer, highlightthickness=0)
    vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vbar.pack(side=tk.RIGHT, fill=tk.Y)

    body = tk.Frame(canvas)
    canvas.create_window((0, 0), window=body, anchor="nw")

    def _on_body_configure(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    body.bind("<Configure>", _on_body_configure)

    # 鼠标滚轮滚动（仅当内容超出时）
    def _on_mousewheel(event):
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _cleanup(_e=None):
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass

    dialog.bind("<Destroy>", _cleanup)

    return canvas, body
