#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 构建器 - 负责配置区、操作按钮、文件列表、日志区、状态栏的组装
🫧 美学：Aurora Frost（极光霜白）
   • 霜白→冰蓝渐变底，叠极光粒子光斑
   • 双层圆角卡片（1px 极光描边 + 柔光白底）
   • 主色：量子蓝 #3B6EFF ｜ 极光绿 #0EA288 ｜ 分子紫 #8B5CF6
   • 字体：Microsoft YaHei UI + Consolas（数字）
"""

import math
import tkinter as tk
from tkinter import ttk, scrolledtext
from constants import SUPPORTED_EXTS


# ------------------------- 🎨 主题颜色常量 -------------------------
class AuroraTheme:
    BG_START     = "#F5F7FF"   # 霜白
    BG_END       = "#EEF3FF"   # 冰蓝
    CARD_BG      = "#FFFFFF"
    CARD_BORDER  = "#D7E2FF"   # 淡蓝描边
    CARD_HL      = "#B7CCFF"   # 悬停描边（极光）
    CARD_SHADE   = "#E6ECFF"   # 阴影替代色
    TEXT_MAIN    = "#1A2142"   # 主文字（深夜蓝）
    TEXT_MUTED   = "#6B7599"   # 次级文字
    TEXT_BADGE   = "#FFFFFF"
    BRAND_BLUE   = "#3B6EFF"   # 量子蓝（主 accent）
    BRAND_GREEN  = "#0EA288"   # 极光绿（成功 / C 位）
    BRAND_PURPLE = "#8B5CF6"   # 分子紫（高级 / 特别）
    BRAND_ORANGE = "#FF8A3D"   # 火焰橙（警告 / TS）
    BRAND_RED    = "#E5484D"   # 红（错误）
    STEP_1       = "#3B6EFF"
    STEP_2       = "#8B5CF6"
    STEP_3       = "#0EA288"
    TOOLTIP_BG   = "#1A2142"
    TOOLTIP_FG   = "#FFFFFF"
    TREE_EVEN    = "#F8FAFF"
    TREE_ODD     = "#FFFFFF"
    TREE_SEL_BG  = "#E2EBFF"
    TREE_SEL_FG  = "#1A2142"
    LOG_BG       = "#F8FAFF"
    LOG_SEL      = "#3B6EFF"

    @staticmethod
    def glow(base: str, pct: float = 0.2) -> str:
        """把 16 进制颜色往白方向提亮 pct，用于发光效果"""
        base = base.lstrip("#")
        r, g, b = (int(base[i:i+2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * pct)
        g = int(g + (255 - g) * pct)
        b = int(b + (255 - b) * pct)
        return f"#{r:02x}{g:02x}{b:02x}"


# ------------------------- 🎨 应用全局 ttk 主题 -------------------------
def apply_aurora_theme(app) -> None:
    """
    在 MainView 初始化时调用：重写 ttk 组件样式，
    把整站变成 Aurora Frost 风格。
    """
    T = AuroraTheme
    style = ttk.Style(app)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # —— 全局默认 ——
    style.configure(
        ".",
        background=T.BG_END,
        foreground=T.TEXT_MAIN,
        fieldbackground=T.CARD_BG,
        font=("Microsoft YaHei UI", 10),
        borderwidth=0,
    )

    # —— 主按钮（圆角胶囊 + 极光描边）——
    style.configure(
        "Aurora.TButton",
        background=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        padding=(14, 8),
        borderwidth=1,
        relief="solid",
        focusthickness=0,
        font=("Microsoft YaHei UI", 10),
    )
    style.map(
        "Aurora.TButton",
        background=[("active", T.glow(T.BRAND_BLUE, 0.88)), ("pressed", T.glow(T.BRAND_BLUE, 0.72))],
        foreground=[("active", T.BRAND_BLUE), ("pressed", T.TEXT_BADGE)],
        bordercolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        lightcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        darkcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
    )

    # —— C 位大按钮（快速入门第 3 步）：极光绿发光 ——
    style.configure(
        "Aurora.BigAccent.TButton",
        background=T.BRAND_GREEN,
        foreground=T.TEXT_BADGE,
        padding=(18, 12),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=("Microsoft YaHei UI", 11, "bold"),
    )
    style.map(
        "Aurora.BigAccent.TButton",
        background=[("active", "#11B99A"), ("pressed", "#0C8873")],
        foreground=[("active", T.TEXT_BADGE), ("pressed", T.TEXT_BADGE)],
    )

    # —— 量子蓝主按钮（PSI4 计算等）——
    style.configure(
        "Aurora.Primary.TButton",
        background=T.BRAND_BLUE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.map(
        "Aurora.Primary.TButton",
        background=[("active", "#5A85FF"), ("pressed", "#2E58D6")],
    )

    # —— 分子紫按钮（高级工具箱）——
    style.configure(
        "Aurora.Purple.TButton",
        background=T.BRAND_PURPLE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.map(
        "Aurora.Purple.TButton",
        background=[("active", "#9B75F7"), ("pressed", "#7348D6")],
    )

    # —— LabelFrame：双层圆角卡片（clam 的 LabelFrame 支持 border）——
    style.configure(
        "Aurora.TLabelframe",
        background=T.BG_END,
        borderwidth=0,
        relief="flat",
        padding=(0, 0, 0, 0),
    )
    style.configure(
        "Aurora.TLabelframe.Label",
        background=T.BG_END,
        foreground=T.TEXT_MAIN,
        font=("Microsoft YaHei UI", 11, "bold"),
        padding=(6, 0, 6, 6),
    )

    # —— 输入 Entry / Combobox ——
    style.configure(
        "Aurora.TEntry",
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.CARD_BORDER,
        darkcolor=T.CARD_BORDER,
        padding=6,
        focusthickness=0,
    )
    style.map(
        "Aurora.TEntry",
        bordercolor=[("focus", T.BRAND_BLUE)],
        lightcolor=[("focus", T.BRAND_BLUE)],
        darkcolor=[("focus", T.BRAND_BLUE)],
    )
    style.configure(
        "Aurora.TCombobox",
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        background=T.CARD_BG,
        arrowcolor=T.BRAND_BLUE,
        bordercolor=T.CARD_BORDER,
        padding=6,
    )
    style.map(
        "Aurora.TCombobox",
        bordercolor=[("focus", T.BRAND_BLUE)],
    )

    # —— Treeview（文件列表）——
    style.configure(
        "Aurora.Treeview",
        background=T.CARD_BG,
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        rowheight=30,
        borderwidth=1,
        relief="solid",
        bordercolor=T.CARD_BORDER,
        font=("Microsoft YaHei UI", 10),
    )
    style.configure(
        "Aurora.Treeview.Heading",
        background=T.glow(T.BRAND_BLUE, 0.9),
        foreground=T.TEXT_MAIN,
        font=("Microsoft YaHei UI", 10, "bold"),
        relief="flat",
        padding=6,
        borderwidth=0,
    )
    style.map(
        "Aurora.Treeview",
        background=[("selected", T.TREE_SEL_BG)],
        foreground=[("selected", T.TREE_SEL_FG)],
    )

    # —— Notebook（高级工具箱 4 标签页）——
    style.configure(
        "Aurora.TNotebook",
        background=T.BG_END,
        borderwidth=0,
        tabmargins=(0, 4, 0, 0),
    )
    style.configure(
        "Aurora.TNotebook.Tab",
        background=T.CARD_BG,
        foreground=T.TEXT_MUTED,
        padding=(18, 10),
        borderwidth=1,
        relief="solid",
        bordercolor=T.CARD_BORDER,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.map(
        "Aurora.TNotebook.Tab",
        background=[("selected", T.BRAND_BLUE)],
        foreground=[("selected", T.TEXT_BADGE), ("active", T.BRAND_BLUE)],
        expand=[("selected", (0, 0, 0, 2))],
    )

    # —— 进度条 ——
    style.configure(
        "Aurora.Horizontal.TProgressbar",
        troughcolor=T.CARD_SHADE,
        background=T.BRAND_GREEN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.BRAND_GREEN,
        darkcolor=T.BRAND_GREEN,
        thickness=14,
    )

    # —— PanedWindow ——
    style.configure(
        "Aurora.TPanedwindow",
        background=T.BG_END,
        sashwidth=4,
        sashrelief="flat",
        borderwidth=0,
    )

    # —— 滚动条 ——
    style.configure(
        "Aurora.Vertical.TScrollbar",
        background=T.CARD_SHADE,
        troughcolor=T.BG_END,
        bordercolor=T.CARD_SHADE,
        arrowcolor=T.BRAND_BLUE,
        gripcount=0,
    )
    style.configure(
        "Aurora.Horizontal.TScrollbar",
        background=T.CARD_SHADE,
        troughcolor=T.BG_END,
        bordercolor=T.CARD_SHADE,
        arrowcolor=T.BRAND_BLUE,
        gripcount=0,
    )

    # —— 记忆：供后续 build_xxx 用 ——
    app._aurora_theme = T
    app._aurora_style = style


# ------------------------- 🎨 渐变背景画布 -------------------------
class AuroraGradientCanvas(tk.Canvas):
    """
    用 Canvas 手搓一条竖向渐变 + 几颗极光粒子点缀，
    然后在里面用 place 摆真正的内容容器。
    因为 Tk 原生不支持 CSS gradient，只能逐行 paint。
    """
    def __init__(self, master, c1: str, c2: str, particles: int = 14, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        self._c1 = c1
        self._c2 = c2
        self._particles_n = particles
        self.bind("<Configure>", self._redraw, add="+")

    @staticmethod
    def _hex2rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @staticmethod
    def _rgb2hex(rgb) -> str:
        r, g, b = (max(0, min(255, int(v))) for v in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _lerp(self, a, b, t):
        return a + (b - a) * t

    def _redraw(self, _evt=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        rgb1 = self._hex2rgb(self._c1)
        rgb2 = self._hex2rgb(self._c2)
        # 画渐变：每 2 像素一条，加速
        for y in range(0, h, 2):
            t = y / max(1, h - 1)
            # 加一点缓动（ease-in-out 让两端更深邃）
            t_e = t * t * (3 - 2 * t)
            col = self._rgb2hex((self._lerp(rgb1[i], rgb2[i], t_e) for i in range(3)))
            self.create_rectangle(0, y, w, y + 2, fill=col, outline=col)
        # 极光光晕粒子（圆形 + 不同透明度）
        import random
        rng = random.Random(42)
        palette = [AuroraTheme.BRAND_BLUE, AuroraTheme.BRAND_GREEN, AuroraTheme.BRAND_PURPLE]
        for i in range(self._particles_n):
            cx = int(rng.uniform(0.05 * w, 0.95 * w))
            cy = int(rng.uniform(0.05 * h, 0.9 * h))
            r = int(rng.uniform(40, 150))
            col = rng.choice(palette)
            # 画 6 层同心递减的填充，模拟柔光
            for k in range(6, 0, -1):
                alpha = 0.03 * k
                rgb = self._hex2rgb(col)
                bg = self._hex2rgb(self._c2 if cy / h > 0.5 else self._c1)
                mixed = self._rgb2hex(self._lerp(bg[i], rgb[i], alpha) for i in range(3))
                self.create_oval(cx - r * k / 6, cy - r * k / 6,
                                 cx + r * k / 6, cy + r * k / 6,
                                 fill=mixed, outline=mixed)


# ------------------------- 🎨 玻璃卡片容器 -------------------------
def make_aurora_card(parent, title: str | None = None, accent: str | None = None) -> tuple[tk.Frame, tk.Frame]:
    """
    双层圆角卡片：
      外层 Frame：1px 极光描边 + 柔和底色（做阴影替代）
      内层 Frame：纯白卡片 + 更大圆角视觉
    返回 (outer, inner)：把组件放进 inner 即可。

    若 title 非 None：在左上角画胶囊形彩色标题条。
    accent：标题条颜色，默认极光绿。
    """
    T = AuroraTheme
    accent = accent or T.BRAND_GREEN
    # —— 外层：极光描边容器 ——
    outer = tk.Frame(
        parent,
        bg=T.CARD_BORDER,
        highlightthickness=0,
        bd=0,
    )
    # 用 pack_propagate 允许固定尺寸，但这里自适应即可
    # —— 内层：纯白卡片（留 1px 边距露出外层描边）——
    inner = tk.Frame(outer, bg=T.CARD_BG, bd=0, highlightthickness=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    # —— 标题条（胶囊形彩色装饰 + 文字）——
    if title:
        header = tk.Frame(inner, bg=T.CARD_BG, bd=0)
        header.pack(fill=tk.X, padx=18, pady=(16, 0))
        # 左侧小胶囊装饰（模仿色块）
        cap = tk.Frame(header, bg=accent, height=18, width=4, bd=0)
        cap.pack(side=tk.LEFT)
        tk.Frame(header, bg=T.glow(accent, 0.6), height=18, width=2, bd=0).pack(side=tk.LEFT)
        tk.Label(
            header,
            text=title,
            bg=T.CARD_BG,
            fg=T.TEXT_MAIN,
            font=("Microsoft YaHei UI", 12, "bold"),
            padx=10, pady=0,
        ).pack(side=tk.LEFT)
        # 右侧一条渐隐分割线，增加设计感
        rule = tk.Frame(header, bg=T.CARD_BG, height=22)
        rule.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        tk.Frame(rule, bg=T.CARD_BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)

    return outer, inner


# ------------------------- 🫧 Tooltip 升级：玻璃胶囊 -------------------------
class ToolTip:
    """
    鼠标悬停气泡（Aurora Frost 版）：
    深夜蓝胶囊底 + 白字 + 小三角指向触发控件。
    """
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        self.id: str | None = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<ButtonPress>", self._on_leave)

    def _on_enter(self, _event=None):
        self.id = self.widget.after(380, self._show_tip)

    def _on_leave(self, _event=None):
        if self.id is not None:
            self.widget.after_cancel(self.id)
            self.id = None
        self._hide_tip()

    def _show_tip(self):
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 24
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            tw.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        T = AuroraTheme
        wrap = tk.Frame(tw, bg=T.BRAND_BLUE, bd=0, highlightthickness=0)
        wrap.pack()
        body = tk.Frame(wrap, bg=T.TOOLTIP_BG, padx=14, pady=10, bd=0)
        body.pack(padx=1, pady=1)
        tk.Label(
            body,
            text=self.text,
            justify=tk.LEFT,
            bg=T.TOOLTIP_BG,
            fg=T.TOOLTIP_FG,
            font=("Microsoft YaHei UI", 9),
            wraplength=320,
        ).pack()

    def _hide_tip(self):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


def add_tooltip(widget, text: str):
    ToolTip(widget, text)


def build_ui(app):
    """
    构建主界面的所有组件：
      • 先铺一层 AuroraGradientCanvas 做渐变背景（极光粒子）
      • 背景之上放一个 content 容器（纯透明 Frame）
      • content 里依次摆 4 张玻璃卡片（快速入门 / 高级配置 / 详细操作 / 列表+日志）
    """
    T = AuroraTheme
    # —— 渐变背景铺满整个窗口 ——
    bg = AuroraGradientCanvas(app, T.BG_START, T.BG_END, particles=16)
    bg.pack(fill=tk.BOTH, expand=True)

    # —— 内容容器（通过 window create_window 放在 Canvas 里，占满整个 Canvas）——
    content = tk.Frame(bg, bg=T.BG_END, bd=0, highlightthickness=0)
    bg.create_window(0, 0, window=content, anchor="nw", tags=("content",))

    def _sync_content_size(evt=None):
        w = max(bg.winfo_width(), 1)
        h = max(bg.winfo_height(), 1)
        bg.itemconfigure("content", width=w, height=h)
    bg.bind("<Configure>", _sync_content_size, add="+")

    content.grid_rowconfigure(0, weight=0)
    content.grid_rowconfigure(1, weight=0)
    content.grid_rowconfigure(2, weight=0)
    content.grid_rowconfigure(3, weight=1)
    content.grid_columnconfigure(0, weight=1)
    # 外层统一 padding（用内部 Frame pad 即可）
    for c in (content,):
        c.configure(padx=14, pady=14)

    # 给主窗口一个统一的品牌色标题栏底色（可选：bg 属性）
    try:
        app.configure(bg=T.BG_END)
    except tk.TclError:
        pass

    app._aurora_bg = bg          # 记下来方便后续刷新

    build_quickstart_card(app, content)
    build_config_frame(app, content)
    build_action_frame(app, content)
    build_paned_area(app, content)
    build_status_bar(app)

    app.filter_keyword_entry.bind("<KeyRelease>", lambda e: app.helpers.apply_filter())
    app.filter_status_combo.bind("<<ComboboxSelected>>", lambda e: app.helpers.apply_filter())
    app.filter_ext_combo.bind("<<ComboboxSelected>>", lambda e: app.helpers.apply_filter())


def build_quickstart_card(app, parent):
    """
    🚀 快速入门玻璃卡片（极光绿标题 + 3 步流程芯片 + 工具直达胶囊按钮）
    """
    T = AuroraTheme
    outer, inner = make_aurora_card(parent, title="🚀 快速入门｜新手按 ① → ② → ③ 顺序点", accent=T.STEP_3)
    outer.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    parent.grid_columnconfigure(0, weight=1)

    # 整体内容区，留足内边距
    body = tk.Frame(inner, bg=T.CARD_BG, bd=0)
    body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(12, 18))

    # —— 三步骤：用「连接线 + 彩色芯片 + 内容」——
    steps_row = tk.Frame(body, bg=T.CARD_BG, bd=0)
    steps_row.pack(fill=tk.X)

    s1_done, s2_done, s3_done = [None, None, None]

    def _chip(root, num: str, color: str, title: str, desc: str):
        """单个步骤芯片：圆形数字徽章 + 标题 + 说明 + 按钮"""
        wrap = tk.Frame(root, bg=T.CARD_BG, bd=0)
        # 左侧圆形数字徽章
        badge = tk.Frame(wrap, bg=color, width=42, height=42, bd=0, highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(0, 12))
        badge.pack_propagate(False)
        tk.Label(badge, text=num, bg=color, fg=T.TEXT_BADGE,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(expand=True)
        # 右侧文字区
        txt = tk.Frame(wrap, bg=T.CARD_BG, bd=0)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(txt, text=title, bg=T.CARD_BG, fg=T.TEXT_MAIN,
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill=tk.X)
        tk.Label(txt, text=desc, bg=T.CARD_BG, fg=T.TEXT_MUTED,
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill=tk.X)
        return wrap

    # ①
    c1_wrap = tk.Frame(steps_row, bg=T.CARD_BG, bd=0)
    c1_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    _chip(c1_wrap, "①", T.STEP_1, "选择文件夹", "找到放分子文件的那个目录").pack(fill=tk.X)
    b1 = ttk.Button(c1_wrap, text="📂 选择分子文件所在目录", style="Aurora.TButton",
                    command=lambda: (app.controller.browse_work_dir(),
                                     _qs_update_step_label(app, s1_done)))
    b1.pack(fill=tk.X, pady=(8, 0))
    s1_done = tk.Label(c1_wrap, text="  ⚪ 未开始", bg=T.CARD_BG, fg=T.TEXT_MUTED,
                       font=("Microsoft YaHei UI", 9))
    s1_done.pack(anchor="w", pady=(6, 0))

    # 连接线 1→2
    conn = tk.Frame(steps_row, bg=T.CARD_BG, bd=0, width=40)
    conn.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=22)
    tk.Frame(conn, bg=T.CARD_BORDER, height=2).pack(fill=tk.X, pady=(20, 0))

    # ②
    c2_wrap = tk.Frame(steps_row, bg=T.CARD_BG, bd=0)
    c2_wrap.grid(row=0, column=2, sticky="nsew", padx=8)
    _chip(c2_wrap, "②", T.STEP_2, "加载对照表（可跳过）", "英文名 → 中文名 CSV 映射").pack(fill=tk.X)
    b2 = ttk.Button(c2_wrap, text="📥 选英文名→中文映射 (可选)", style="Aurora.TButton",
                    command=lambda: (app.controller.browse_mapping(),
                                     app.controller.load_mapping_file(),
                                     _qs_update_step_label(app, s2_done, force_done=True)))
    b2.pack(fill=tk.X, pady=(8, 0))
    s2_done = tk.Label(c2_wrap, text="  ⚪ 可跳过", bg=T.CARD_BG, fg=T.TEXT_MUTED,
                       font=("Microsoft YaHei UI", 9))
    s2_done.pack(anchor="w", pady=(6, 0))

    # 连接线 2→3
    conn2 = tk.Frame(steps_row, bg=T.CARD_BG, bd=0, width=40)
    conn2.grid(row=0, column=3, sticky="ns", padx=(6, 2), pady=22)
    tk.Frame(conn2, bg=T.CARD_BORDER, height=2).pack(fill=tk.X, pady=(20, 0))

    # ③（C 位：极光绿渐变大按钮）
    c3_wrap = tk.Frame(steps_row, bg=T.CARD_BG, bd=0)
    c3_wrap.grid(row=0, column=4, sticky="nsew", padx=8)
    _chip(c3_wrap, "③", T.STEP_3, "一键搞定", "自动扫描 → 自动重命名｜闭眼点").pack(fill=tk.X)
    def _quick_fix_all():
        app.controller.scan_files()
        _qs_update_step_label(app, s1_done, force_done=True)
        app.after(220, lambda: (app.controller.run_fix_by_mode(),
                                 _qs_update_step_label(app, s3_done, force_done=True)))
    b3 = ttk.Button(c3_wrap, text="✨ 一键扫描并自动重命名（推荐！）",
                    style="Aurora.BigAccent.TButton", command=_quick_fix_all)
    b3.pack(fill=tk.X, pady=(8, 0))
    s3_done = tk.Label(c3_wrap, text="  ⚪ 点上方绿色按钮开始", bg=T.CARD_BG, fg=T.TEXT_MUTED,
                       font=("Microsoft YaHei UI", 9))
    s3_done.pack(anchor="w", pady=(6, 0))

    # 扩展列宽权重
    steps_row.grid_columnconfigure(0, weight=1)
    steps_row.grid_columnconfigure(2, weight=1)
    steps_row.grid_columnconfigure(4, weight=1)

    # —— 第二行：常用工具直达（胶囊按钮行）——
    tool_bar = tk.Frame(body, bg=T.CARD_BG, bd=0)
    tool_bar.pack(fill=tk.X, pady=(18, 0))

    # 上方分割装饰：左侧渐变色 + 中间文字 + 右侧渐变色
    deco = tk.Frame(tool_bar, bg=T.CARD_BG, bd=0)
    deco.pack(fill=tk.X, pady=(0, 12))
    tk.Frame(deco, bg=T.CARD_BORDER, height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=9)
    tk.Label(deco, text="  🧰  常用工具直达  ", bg=T.CARD_BG, fg=T.TEXT_MUTED,
             font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
    tk.Frame(deco, bg=T.CARD_BORDER, height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=9)

    q_row = tk.Frame(tool_bar, bg=T.CARD_BG, bd=0)
    q_row.pack(fill=tk.X)
    q_react = ttk.Button(q_row, text="🎬  做反应动画  (GIF / MP4)", style="Aurora.Primary.TButton",
                         command=app.controller.show_reaction_animation_dialog)
    q_react.pack(side=tk.LEFT, padx=(0, 10))
    q_psi = ttk.Button(q_row, text="⚡  量子化学计算  PSI4", style="Aurora.TButton",
                       command=app.controller.show_psi4_dialog)
    q_psi.pack(side=tk.LEFT, padx=(0, 10))
    q_ob = ttk.Button(q_row, text="🔬  OpenBabel 工具箱", style="Aurora.TButton",
                      command=app.controller.show_openbabel_dialog)
    q_ob.pack(side=tk.LEFT, padx=(0, 10))
    q_adv = ttk.Button(q_row, text="🧰  高级工具箱 （新页面）", style="Aurora.Purple.TButton",
                       command=app.controller.show_advanced_tools_dialog)
    q_adv.pack(side=tk.LEFT, padx=(0, 10))
    tk.Label(q_row, text="  悬停任意按钮查看使用说明 💡",
             bg=T.CARD_BG, fg=T.TEXT_MUTED,
             font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)

    # —— tooltip 绑定 ——
    add_tooltip(b1, "第 1 步：选择放着你 .mol / .xyz / .fchk 等\n分子文件的那个文件夹，比如『D:\\我的分子文件』")
    add_tooltip(b2, "第 2 步（可以不做）：选一张 .csv 对照表\n格式：左列英文名，右列中文名\n例如：ch4, 甲烷")
    add_tooltip(b3, "第 3 步：按一下就自动搞定！\n自动扫描文件夹 → 找到需要改名的文件 → 自动加上中文名")
    add_tooltip(q_react, "做化学反应动画：\n左边选反应物分子，右边选产物分子\n选个溶剂，点一下就出 GIF/MP4 动图")
    add_tooltip(q_psi, "跑量子化学计算：\n算单点能 / 优化结构 / 振动频率\n需要先装好 PSI4 软件")
    add_tooltip(q_ob, "OpenBabel 工具箱：\n.mol ↔ .xyz 互转、画 2D 结构图、\n分子叠合对齐 等")
    add_tooltip(q_adv, "打开高级功能新标签页：\n"
                       "• 构象搜索 + 二面角扫描 / 批量 HOMO LUMO\n"
                       "• TS IRC 最小能量路径 + 能垒台阶图 + Eyring t₁/₂\n"
                       "• pKa SMD 预测 / ¹H NMR Boltzmann 谱图")

    app._qs_labels = {"s1": s1_done, "s2": s2_done, "s3": s3_done}


def _qs_update_step_label(app, label_widget, force_done: bool = False):
    """更新快速入门步骤状态标签：⚪ 未做 → 🔵 进行中 → 🟢 已完成"""
    T = AuroraTheme
    if force_done:
        label_widget.configure(text="  🟢 已完成", fg=T.BRAND_GREEN,
                               font=("Microsoft YaHei UI", 9, "bold"))
        return
    work_dir = app.work_dir_var.get().strip()
    if work_dir and app._qs_labels.get("s1") is label_widget:
        label_widget.configure(text="  🟢 已选择", fg=T.BRAND_GREEN,
                               font=("Microsoft YaHei UI", 9, "bold"))
    mapping_txt = app.mapping_count.get().strip()
    if mapping_txt and mapping_txt not in ("未加载", "0 条") and app._qs_labels.get("s2") is label_widget:
        label_widget.configure(text="  🟢 已加载", fg=T.BRAND_GREEN,
                               font=("Microsoft YaHei UI", 9, "bold"))


def build_config_frame(app, parent):
    """
    ⚙️ 高级配置玻璃卡片（量子蓝标题 accent）。
    """
    T = AuroraTheme
    outer, inner = make_aurora_card(parent, title="⚙️  高级配置（新手用上面的快速入门即可）", accent=T.BRAND_BLUE)
    outer.grid(row=1, column=0, sticky="ew", pady=(0, 12))

    body = tk.Frame(inner, bg=T.CARD_BG, bd=0)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(14, 18))
    body.grid_columnconfigure(1, weight=1)

    # —— 公用：一个左对齐的 Label 函数 ——
    def _label(text: str):
        return tk.Label(body, text=text, bg=T.CARD_BG, fg=T.TEXT_MUTED,
                        font=("Microsoft YaHei UI", 10, "bold"))

    # 工作目录
    lbl_wd = _label("工作目录")
    lbl_wd.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 8))
    app.work_dir_entry = ttk.Entry(body, textvariable=app.work_dir_var, style="Aurora.TEntry")
    app.work_dir_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))
    btn_wd = ttk.Button(body, text="📂 浏览", style="Aurora.TButton", command=app.controller.browse_work_dir)
    btn_wd.grid(row=0, column=2, sticky="w", pady=(0, 8))
    add_tooltip(lbl_wd, "工作目录：存放 .mol / .xyz / .fchk 等\n分子文件的文件夹")
    add_tooltip(app.work_dir_entry, "也可以直接把路径粘贴到这里")
    add_tooltip(btn_wd, "弹出文件夹选择器，选择后会立刻自动扫描")

    # 映射文件
    lbl_map = _label("映射文件")
    lbl_map.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=8)
    app.map_entry = ttk.Entry(body, textvariable=app.mapping_file_var, style="Aurora.TEntry")
    app.map_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=8)
    col1 = tk.Frame(body, bg=T.CARD_BG, bd=0)
    col1.grid(row=1, column=2, sticky="w", pady=8)
    btn_map_browse = ttk.Button(col1, text="📂 浏览", style="Aurora.TButton", command=app.controller.browse_mapping)
    btn_map_browse.pack(side=tk.LEFT)
    btn_map_load = ttk.Button(col1, text="📥 加载映射", style="Aurora.TButton", command=app.controller.load_mapping_file)
    btn_map_load.pack(side=tk.LEFT, padx=(8, 10))
    tk.Label(col1, text="已加载：", bg=T.CARD_BG, fg=T.TEXT_MUTED,
             font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    tk.Label(col1, textvariable=app.mapping_count, bg=T.CARD_BG, fg=T.BRAND_BLUE,
             font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
    add_tooltip(lbl_map, "映射 = 英文名→中文名 的对照表\n格式：CSV，左列 english 右列 chinese\n例如：ch4, 甲烷")
    add_tooltip(btn_map_load, "把 CSV 里的中英文对照读入内存")

    # 文件类型
    lbl_ext = _label("文件类型")
    lbl_ext.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
    app.ext_display_var = tk.StringVar()
    app.helpers.update_ext_display()
    ext_display = tk.Label(body, textvariable=app.ext_display_var,
                           bg=T.glow(T.BRAND_BLUE, 0.92), fg=T.BRAND_BLUE,
                           relief="flat", padx=12, pady=6,
                           font=("Consolas", 10))
    ext_display.grid(row=2, column=1, sticky="w", padx=(0, 10), pady=(8, 0))
    col2 = tk.Frame(body, bg=T.CARD_BG, bd=0)
    col2.grid(row=2, column=2, sticky="w", pady=(8, 0))
    btn_ext = ttk.Button(col2, text="📋 选择文件类型", style="Aurora.TButton", command=app.controller.show_ext_filter_dialog)
    btn_ext.pack(side=tk.LEFT)
    btn_scan_here = ttk.Button(col2, text="🔍 应用过滤", style="Aurora.TButton", command=app.controller.scan_files)
    btn_scan_here.pack(side=tk.LEFT, padx=(8, 0))
    add_tooltip(lbl_ext, "只显示哪些后缀名的文件，默认已包含常见的 .mol .xyz .fchk .out .inp")
    add_tooltip(btn_scan_here, "按当前选的文件类型重新扫描文件夹（=刷新列表）")


def build_action_frame(app, parent):
    """🛠️ 详细操作玻璃卡片（分子紫 accent）—— 4 行分区，每行一个 emoji 小标题 + 按钮胶囊"""
    T = AuroraTheme
    outer, inner = make_aurora_card(parent, title="🛠️  详细操作｜高级功能区", accent=T.BRAND_PURPLE)
    outer.grid(row=2, column=0, sticky="ew", pady=(0, 12))

    body = tk.Frame(inner, bg=T.CARD_BG, bd=0)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(14, 18))

    def _row(icon: str, title: str, accent: str):
        """生成一行（标签头 + 按钮容器 + 分隔条）"""
        wrap = tk.Frame(body, bg=T.CARD_BG, bd=0)
        wrap.pack(fill=tk.X, pady=6)
        # 左侧竖条 + 标题
        head = tk.Frame(wrap, bg=T.CARD_BG, bd=0)
        head.pack(side=tk.LEFT, padx=(0, 14))
        tk.Frame(head, bg=accent, width=3, height=22, bd=0).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(head, text=f"{icon} {title}", bg=T.CARD_BG, fg=T.TEXT_MAIN,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        # 右侧按钮容器
        btns = tk.Frame(wrap, bg=T.CARD_BG, bd=0)
        btns.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return btns

    # —— 第一行：文件操作 ——
    row0 = _row("📁", "文件", AuroraTheme.STEP_1)
    btn_scan = ttk.Button(row0, text="扫描文件", style="Aurora.TButton", command=app.controller.scan_files)
    btn_scan.pack(side=tk.LEFT, padx=(0, 6))
    btn_missing = ttk.Button(row0, text="生成缺失列表", style="Aurora.TButton", command=app.controller.generate_missing)
    btn_missing.pack(side=tk.LEFT, padx=(0, 6))
    btn_suppl = ttk.Button(row0, text="补全 .mol", style="Aurora.TButton", command=app.controller.supplement_mol)
    btn_suppl.pack(side=tk.LEFT, padx=(0, 6))
    btn_dedup = ttk.Button(row0, text="删除重复文件", style="Aurora.TButton", command=app.controller.remove_duplicate_files)
    btn_dedup.pack(side=tk.LEFT, padx=(0, 6))
    btn_undo = ttk.Button(row0, text="↩  撤销", style="Aurora.TButton", command=app.controller.undo_last)
    btn_undo.pack(side=tk.LEFT, padx=(0, 6))
    add_tooltip(btn_scan, "重新读取工作目录并刷新文件列表")
    add_tooltip(btn_missing, "导出一张 CSV：哪些英文名在映射表里找不到，\n方便你后续去补对照表")
    add_tooltip(btn_suppl, "对于只有 .xyz 没有 .mol 的文件，自动用 OpenBabel 转一份 .mol")
    add_tooltip(btn_dedup, "对比相同文件大小+内容哈希，只留 1 份（其余进回收站）")
    add_tooltip(btn_undo, "撤销最近一次改名/移动（最多 1 次）")

    # —— 第二行：整理操作 ——
    row1 = _row("📂", "整理", AuroraTheme.STEP_2)
    btn_by_type = ttk.Button(row1, text="按类型整理", style="Aurora.TButton", command=app.controller.organize_by_type)
    btn_by_type.pack(side=tk.LEFT, padx=(0, 6))
    btn_by_name = ttk.Button(row1, text="按文件名分组", style="Aurora.TButton", command=app.controller.organize_by_basename)
    btn_by_name.pack(side=tk.LEFT, padx=(0, 6))
    btn_prefix = ttk.Button(row1, text="前缀重命名", style="Aurora.TButton", command=app.controller.prefix_rename_dialog)
    btn_prefix.pack(side=tk.LEFT, padx=(0, 6))
    add_tooltip(btn_by_type, "按扩展名分文件夹：\n.mol → mol_files/、.xyz → xyz_files/")
    add_tooltip(btn_by_name, "按同名（不含扩展名）分到同一个子文件夹")
    add_tooltip(btn_prefix, "批量加前缀/日期/分子量等前缀到文件名开头")

    # —— 第三行：修复操作 ——
    row2 = _row("🔧", "修复", AuroraTheme.BRAND_ORANGE)
    app.fix_mode_var = tk.StringVar(value="一键修复（推荐）")
    fix_modes = ["一键修复（推荐）", "映射重命名", "修复中文名", "修复命名错误", "修正中文内容"]
    fix_menu = ttk.Combobox(row2, textvariable=app.fix_mode_var, values=fix_modes, width=18, state="readonly",
                            style="Aurora.TCombobox")
    fix_menu.pack(side=tk.LEFT, padx=(0, 6))
    btn_run_fix = ttk.Button(row2, text="▶  执行修复", style="Aurora.Primary.TButton", command=app.controller.run_fix_by_mode)
    btn_run_fix.pack(side=tk.LEFT, padx=(0, 6))
    add_tooltip(fix_menu, "选修复模式：\n• 一键修复=最常用，帮你全做了")
    add_tooltip(btn_run_fix, "按选的模式执行重命名/修复")

    # —— 第四行：工具（量子蓝 / 分子紫 / 极光绿 三色胶囊）——
    row3 = _row("🧪", "工具", AuroraTheme.BRAND_GREEN)
    btn_psi4 = ttk.Button(row3, text="⚡  PSI4 计算", style="Aurora.Primary.TButton", command=app.controller.show_psi4_dialog)
    btn_psi4.pack(side=tk.LEFT, padx=(0, 6))
    btn_ob = ttk.Button(row3, text="🔬  OpenBabel", style="Aurora.TButton", command=app.controller.show_openbabel_dialog)
    btn_ob.pack(side=tk.LEFT, padx=(0, 6))
    btn_hist = ttk.Button(row3, text="📜  历史记录", style="Aurora.TButton", command=app.controller.show_history_dialog)
    btn_hist.pack(side=tk.LEFT, padx=(0, 6))
    btn_diff = ttk.Button(row3, text="🔍  目录差异", style="Aurora.TButton", command=app.controller.show_diff_sync_dialog)
    btn_diff.pack(side=tk.LEFT, padx=(0, 6))
    btn_mapedit = ttk.Button(row3, text="📝  映射表编辑", style="Aurora.TButton", command=app.controller.show_mapping_editor_dialog)
    btn_mapedit.pack(side=tk.LEFT, padx=(0, 6))
    btn_adv = ttk.Button(row3, text="🧰  高级工具箱", style="Aurora.Purple.TButton", command=app.controller.show_advanced_tools_dialog)
    btn_adv.pack(side=tk.LEFT, padx=(0, 6))
    btn_anim = ttk.Button(row3, text="🎬  反应动画", style="Aurora.Primary.TButton", command=app.controller.show_reaction_animation_dialog)
    btn_anim.pack(side=tk.LEFT, padx=(0, 6))
    btn_refresh = ttk.Button(row3, text="🔄  刷新", style="Aurora.TButton", command=app.controller.scan_files)
    btn_refresh.pack(side=tk.LEFT, padx=(0, 6))
    add_tooltip(btn_psi4, "打开 PSI4 单电能/优化/振动频率计算（需另外安装 PSI4）")
    add_tooltip(btn_ob, "OpenBabel 工具箱（分子转换 / 2D 结构 / 叠合对齐…）")
    add_tooltip(btn_hist, "查看做过的改名记录，恢复错误操作可从这里翻")
    add_tooltip(btn_diff, "对比两个目录里的文件差异，或把文件同步过去")
    add_tooltip(btn_mapedit, "可视化编辑英文→中文映射对照")
    add_tooltip(btn_adv, "🛠️ 高级功能新页面：\n"
                          "• OB 分子工具（SMILES 搜索/手性/pH/SDF/InChIKey）\n"
                          "• 波函数 / 构象搜索 / 二面角扫描 / 批量 HOMO LUMO\n"
                          "• TS IRC 路径 + 能垒图 + Eyring k & t₁/₂\n"
                          "• pKa (SMD) 预测 + ¹H NMR Boltzmann 谱图")
    add_tooltip(btn_anim, "开反应动画制作窗口：反应物+产物→GIF/MP4")
    add_tooltip(btn_refresh, "刷新文件列表（同扫描文件）")


def build_paned_area(app, parent):
    """
    📄📋 文件列表 + 日志（PanedWindow 拆分），各自单独套一层玻璃卡片 + 标题
    """
    T = AuroraTheme
    paned = ttk.PanedWindow(parent, orient=tk.VERTICAL, style="Aurora.TPanedwindow")
    paned.grid(row=3, column=0, sticky="nsew")
    parent.grid_rowconfigure(3, weight=1)

    # ========== 上：文件列表卡片 ==========
    outer_file, inner_file = make_aurora_card(parent=None, title="📄  文件列表｜File Browser", accent=T.BRAND_BLUE)
    # 外层卡片作为 PanedWindow 的子窗口
    file_card_body = tk.Frame(paned, bg=T.BG_END, bd=0)
    outer_file.master = file_card_body
    outer_file.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
    # 手动把 outer_file 的 parent 改为 file_card_body
    for slave in outer_file.pack_slaves():
        pass   # noop
    # 重建父容器关系（tkinter 的 Frame 不能改 master，直接重新 new 一个）
    # → 直接在这里重新 make 一次
    outer_file.destroy()
    outer_file, inner_file = make_aurora_card(file_card_body, title="📄  文件列表｜File Browser", accent=T.BRAND_BLUE)
    outer_file.pack(fill=tk.BOTH, expand=True)
    paned.add(file_card_body, weight=3)

    body_file = tk.Frame(inner_file, bg=T.CARD_BG, bd=0)
    body_file.pack(fill=tk.BOTH, expand=True, padx=18, pady=(14, 16))

    # —— 过滤条 ——
    filter_row = tk.Frame(body_file, bg=T.CARD_BG, bd=0)
    filter_row.pack(fill=tk.X, pady=(0, 10))

    lbl_filter = tk.Label(filter_row, text="🔎 过滤", bg=T.CARD_BG, fg=T.TEXT_MUTED,
                          font=("Microsoft YaHei UI", 10, "bold"))
    lbl_filter.pack(side=tk.LEFT, padx=(0, 10))
    add_tooltip(lbl_filter, "筛选查看文件：输入关键字即可\n例如：甲烷、.xyz、ch4…")

    app.filter_keyword_entry = ttk.Entry(filter_row, textvariable=app.filter_keyword_var, width=22,
                                         style="Aurora.TEntry")
    app.filter_keyword_entry.pack(side=tk.LEFT, padx=(0, 8))
    add_tooltip(app.filter_keyword_entry, "输入部分文件名即可过滤，不需要全拼")

    status_opts = ["全部", "✅ 已正确命名", "⏳ 待重命名", "⏳ 纯中文，待修复", "❌ 无映射", "📄 计算文件"]
    app.filter_status_combo = ttk.Combobox(filter_row, textvariable=app.filter_status_var,
                                           values=status_opts, state="readonly", width=18,
                                           style="Aurora.TCombobox")
    app.filter_status_combo.pack(side=tk.LEFT, padx=(0, 8))
    add_tooltip(app.filter_status_combo,
                "按命名状态筛选：\n✅ 正确=中英文都有\n⏳ 待重命名=只有英文名\n❌ 无映射=在对照表里找不到")

    sorted_exts = sorted(e.lstrip(".") for e in SUPPORTED_EXTS)
    ext_opts = ["全部"] + sorted_exts
    app.filter_ext_combo = ttk.Combobox(filter_row, textvariable=app.filter_ext_var,
                                        values=ext_opts, state="readonly", width=8,
                                        style="Aurora.TCombobox")
    app.filter_ext_combo.pack(side=tk.LEFT, padx=(0, 8))
    add_tooltip(app.filter_ext_combo, "只看某一类文件（mol / xyz / fchk / out / inp）")

    tk.Label(filter_row, textvariable=app.filter_count_var, bg=T.CARD_BG, fg=T.BRAND_BLUE,
             font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 8))

    def clear_filter():
        app.filter_keyword_var.set("")
        app.filter_status_var.set("全部")
        app.filter_ext_var.set("全部")
        app.helpers.apply_filter()

    btn_clear = ttk.Button(filter_row, text="清除过滤器", style="Aurora.TButton", command=clear_filter)
    btn_clear.pack(side=tk.RIGHT)
    add_tooltip(btn_clear, "把以上 3 个过滤条件都清空，显示全部文件")

    # —— Treeview ——
    tree_wrap = tk.Frame(body_file, bg=T.CARD_BG, bd=1, highlightthickness=1,
                        highlightbackground=T.CARD_BORDER)
    tree_wrap.pack(fill=tk.BOTH, expand=True)
    columns = ("文件名", "状态", "英文名", "中文名", "MW", "LogP", "TPSA")
    app.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", height=14,
                            style="Aurora.Treeview")
    for c, text, w in (("文件名", "文件名", 320),
                       ("状态", "状态", 150),
                       ("英文名", "英文名", 160),
                       ("中文名", "中文名", 160),
                       ("MW", "分子量", 80),
                       ("LogP", "LogP", 70),
                       ("TPSA", "TPSA", 70)):
        app.tree.heading(c, text=text)
        app.tree.column(c, width=w, anchor="center" if c in ("MW", "LogP", "TPSA", "状态") else "w")
    add_tooltip(app.tree,
                "点击表头可排序；MW/LogP/TPSA 由 OpenBabel 自动算出\n（分子文件扫描完成后后台异步填充）")
    # 斑马纹
    app.tree.tag_configure("even", background=T.TREE_EVEN)
    app.tree.tag_configure("odd", background=T.TREE_ODD)

    scrollbar = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=app.tree.yview,
                              style="Aurora.Vertical.TScrollbar")
    app.tree.configure(yscrollcommand=scrollbar.set)
    app.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 右键菜单
    app.context_menu = tk.Menu(app, tearoff=0)
    app.context_menu.add_command(label="🖼️  预览 2D 结构", command=app.controller.preview_2d_structure)
    app.context_menu.add_command(label="🧪  分子式 / 元素分析", command=app.controller.show_formula_dialog)
    app.context_menu.add_separator()
    app.context_menu.add_command(label="📐  导出键长/键角 CSV", command=app.controller.export_geometry_csv)
    app.context_menu.add_command(label="📝  批量计算描述符 (MW / LogP / TPSA)", command=app.controller.batch_fill_descriptors)
    app.context_menu.add_separator()
    app.context_menu.add_command(label="🗑️  删除选中文件", command=app.controller.delete_selected)
    app.tree.bind("<Button-3>", app.controller.show_context_menu)

    # ========== 下：日志卡片 ==========
    outer_log, inner_log = make_aurora_card(None, title="📋  运行日志｜Logs", accent=T.BRAND_GREEN)
    log_card_body = tk.Frame(paned, bg=T.BG_END, bd=0)
    outer_log.destroy()
    outer_log, inner_log = make_aurora_card(log_card_body, title="📋  运行日志｜Logs", accent=T.BRAND_GREEN)
    outer_log.pack(fill=tk.BOTH, expand=True)
    paned.add(log_card_body, weight=2)

    body_log = tk.Frame(inner_log, bg=T.CARD_BG, bd=0)
    body_log.pack(fill=tk.BOTH, expand=True, padx=18, pady=(14, 16))
    body_log.grid_columnconfigure(0, weight=1)
    body_log.grid_rowconfigure(1, weight=1)

    # —— 日志工具栏：过滤芯片 + 导出/清空按钮 ——
    log_toolbar = tk.Frame(body_log, bg=T.CARD_BG, bd=0)
    log_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

    chip_labels = [
        ("DEBUG",   "debug",   T.TEXT_MUTED,   "#E6ECFF"),
        ("INFO",    "info",    T.BRAND_BLUE,   T.glow(T.BRAND_BLUE, 0.92)),
        ("SUCCESS", "success", T.BRAND_GREEN,  T.glow(T.BRAND_GREEN, 0.90)),
        ("WARNING", "warning", T.BRAND_ORANGE, T.glow(T.BRAND_ORANGE, 0.90)),
        ("ERROR",   "error",   T.BRAND_RED,    T.glow(T.BRAND_RED, 0.90)),
    ]
    app._log_chip_vars = {}
    for i, (lbl, key, fg, bg) in enumerate(chip_labels):
        var = tk.BooleanVar(value=True)
        app._log_chip_vars[key] = var
        chip = tk.Checkbutton(
            log_toolbar, text=f" {lbl} ", variable=var,
            bg=T.CARD_BG, fg=fg, selectcolor=bg,
            activebackground=T.CARD_BG, activeforeground=fg,
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=0, highlightthickness=0,
            command=lambda k=key, v=var: app.helpers._toggle_log_level(k, v),
            cursor="hand2",
        )
        chip.grid(row=0, column=i, padx=(0, 8))
        add_tooltip(chip, f"点击切换是否显示【{lbl}】级别日志")

    # 右侧：导出 TXT / 导出 CSV / 清空 按钮
    btn_bar = tk.Frame(log_toolbar, bg=T.CARD_BG, bd=0)
    btn_bar.grid(row=0, column=10, sticky="e")
    log_toolbar.grid_columnconfigure(10, weight=1)

    btn_export_txt = ttk.Button(btn_bar, text="📄 导出 TXT", style="Aurora.TButton",
                                 command=lambda: app.helpers._export_log("txt"))
    btn_export_txt.pack(side=tk.RIGHT, padx=(8, 0))
    add_tooltip(btn_export_txt, "把当前全部日志导出为 .txt 文本文件\n（含所有级别，不受过滤芯片影响）")

    btn_export_csv = ttk.Button(btn_bar, text="📊 导出 CSV", style="Aurora.TButton",
                                 command=lambda: app.helpers._export_log("csv"))
    btn_export_csv.pack(side=tk.RIGHT, padx=(8, 0))
    add_tooltip(btn_export_csv, "把当前全部日志导出为 .csv 表格\n（含时间/级别/消息列，可 Excel 打开）")

    btn_top_perf = ttk.Button(btn_bar, text="⚡ 性能 Top10", style="Aurora.TButton",
                               command=lambda: app.helpers._show_top_perf())
    btn_top_perf.pack(side=tk.RIGHT, padx=(8, 0))
    add_tooltip(btn_top_perf, "显示当前会话最耗时的 10 个操作\n（单位：毫秒，用于找慢操作瓶颈）")

    btn_clear = ttk.Button(btn_bar, text="🗑️  清空日志", style="Aurora.TButton",
                            command=lambda: app.helpers.clear_log())
    btn_clear.pack(side=tk.RIGHT, padx=(8, 0))
    add_tooltip(btn_clear, "清空当前日志面板\n（二次确认，避免误点）")

    # —— 日志文本区 ——
    log_wrap = tk.Frame(body_log, bg=T.LOG_BG, bd=1, highlightthickness=1,
                        highlightbackground=T.CARD_BORDER)
    log_wrap.grid(row=1, column=0, sticky="nsew")
    app.log_text = scrolledtext.ScrolledText(
        log_wrap,
        height=7,
        wrap=tk.WORD,
        font=("Consolas", 11),
        bg=T.LOG_BG,
        fg=T.TEXT_MAIN,
        insertbackground=T.BRAND_BLUE,
        selectbackground=T.glow(T.BRAND_BLUE, 0.8),
        relief="flat",
        bd=0,
        padx=12, pady=8,
    )
    app.log_text.pack(fill=tk.BOTH, expand=True)

    # —— 更醒目的彩色背景标签：ERROR 红底条 / WARNING 橙底条 / SUCCESS 绿底条 / DEBUG 灰字 ——
    app.log_text.tag_configure("info", foreground=T.TEXT_MAIN, background=T.LOG_BG)
    app.log_text.tag_configure("debug", foreground=T.TEXT_MUTED,
                               background="#F4F6FF",
                               font=("Consolas", 10, "italic"))
    app.log_text.tag_configure("success",
                               foreground="#0C8873",
                               background=T.glow(T.BRAND_GREEN, 0.93),
                               font=("Consolas", 11, "bold"))
    app.log_text.tag_configure("warning",
                               foreground="#B65A1A",
                               background=T.glow(T.BRAND_ORANGE, 0.92),
                               font=("Consolas", 11, "bold"))
    app.log_text.tag_configure("error",
                               foreground="#9A1D21",
                               background=T.glow(T.BRAND_RED, 0.92),
                               font=("Consolas", 11, "bold"))
    app.log_text.tag_configure("critical",
                               foreground="#FFFFFF",
                               background=T.BRAND_RED,
                               font=("Consolas", 11, "bold"))


def build_status_bar(app):
    """
    底部状态栏：左侧状态文字（深夜蓝条 + 极光白字），右侧进度条 + 清除日志按钮
    """
    T = AuroraTheme
    status_frame = tk.Frame(app, bg=T.TEXT_MAIN, bd=0, height=38, highlightthickness=0)
    status_frame.pack(side=tk.BOTTOM, fill=tk.X)
    status_frame.pack_propagate(False)

    # —— 左侧状态文字 ——
    left = tk.Frame(status_frame, bg=T.TEXT_MAIN, bd=0)
    left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14, pady=7)
    # 左侧小色点（绿=就绪）
    dot = tk.Frame(left, bg=T.BRAND_GREEN, width=10, height=10, bd=0)
    dot.pack(side=tk.LEFT, padx=(0, 10))
    # 让色点变成圆形（用 Canvas 画个圆）
    dot_canvas = tk.Canvas(left, width=12, height=12, bg=T.TEXT_MAIN, highlightthickness=0, bd=0)
    dot_canvas.pack(side=tk.LEFT, padx=(0, 10))
    dot_canvas.create_oval(1, 1, 11, 11, fill=T.BRAND_GREEN, outline=T.BRAND_GREEN)
    # 让色点随状态变化
    app._status_dot = dot_canvas
    app.status_var = tk.StringVar(value="  就绪 · Ready")

    def _on_status_change(*_):
        txt = app.status_var.get().strip()
        c = T.BRAND_GREEN
        if any(k in txt for k in ("错误", "失败", "Error", "Fail", "❌")):
            c = T.BRAND_RED
        elif any(k in txt for k in ("警告", "Warn", "⚠️")):
            c = T.BRAND_ORANGE
        elif any(k in txt for k in ("进行中", "Running", "计算", "扫描", "⏳")):
            c = T.BRAND_BLUE
        app._status_dot.delete("all")
        app._status_dot.create_oval(1, 1, 11, 11, fill=c, outline=c)
    app.status_var.trace_add("write", _on_status_change)

    tk.Label(left, textvariable=app.status_var, bg=T.TEXT_MAIN, fg=T.TEXT_BADGE,
             font=("Microsoft YaHei UI", 10), anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

    # —— 右侧：清除日志 + 进度条 ——
    right = tk.Frame(status_frame, bg=T.TEXT_MAIN, bd=0)
    right.pack(side=tk.RIGHT, fill=tk.Y, padx=12, pady=6)
    clear_btn = ttk.Button(right, text="清除日志", style="Aurora.TButton", command=app.helpers.clear_log)
    clear_btn.pack(side=tk.RIGHT, padx=(10, 0))
    app.progress_bar = ttk.Progressbar(right, variable=app.progress_var, maximum=100, length=180,
                                       style="Aurora.Horizontal.TProgressbar")
    app.progress_bar.pack(side=tk.RIGHT, padx=(0, 10))


