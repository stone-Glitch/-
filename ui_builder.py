#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 构建器 - 大字体、扁平卡片风格，无 Canvas 装饰
- 顶部保留 Aurora 辅助类（AuroraTheme / apply_aurora_theme / AuroraGradientCanvas /
  make_aurora_card / ToolTip / add_tooltip），供 dialogs.py 等复用
- 底部主界面 build_ui 系列函数：纯 tk.Frame + ttk，零 Canvas 嵌套，稳定显示
"""

import math
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
from constants import SUPPORTED_EXTS


# ------------------------- 🎨 主题颜色常量 -------------------------
class AuroraTheme:
    BG_START     = "#F5F7FF"
    BG_END       = "#EEF3FF"
    CARD_BG      = "#FFFFFF"
    CARD_BORDER  = "#D7E2FF"
    CARD_HL      = "#B7CCFF"
    CARD_SHADE   = "#E6ECFF"
    TEXT_MAIN    = "#1A2142"
    TEXT_MUTED   = "#6B7599"
    TEXT_BADGE   = "#FFFFFF"
    BRAND_BLUE   = "#3B6EFF"
    BRAND_GREEN  = "#0EA288"
    BRAND_PURPLE = "#8B5CF6"
    BRAND_ORANGE = "#FF8A3D"
    BRAND_RED    = "#E5484D"
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
        base = base.lstrip("#")
        r, g, b = (int(base[i:i+2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * pct)
        g = int(g + (255 - g) * pct)
        b = int(b + (255 - b) * pct)
        return f"#{r:02x}{g:02x}{b:02x}"


# ------------------------- 🔠 字体基线（问题一：字太小 修复） -------------------------
# 所有控件显式指定 font=app._fonts["BASE"] 等，避免依赖系统默认 9pt。
# font_size 来自 config.font_size（默认 14pt），配合 DPI 放大系数再调整一次。
def resolve_font_specs(app, force_pt: int | None = None) -> dict:
    """
    基于 config 计算字体尺寸，结果存到 app._fonts 字典。

    参数：
      - force_pt：如果调用方传入（比如字体大小对话框保存后热更新），则忽略
        config.font_size，直接以 force_pt 作为 raw_pt；常用于「保存后不重启」时
        的尽力刷新。
    """
    try:
        cfg = getattr(app, "config_data", {}) or {}
    except Exception:
        cfg = {}
    if isinstance(force_pt, int) and force_pt > 0:
        raw_pt = int(force_pt)
    else:
        raw_pt = int(cfg.get("font_size", 14) or 14)
    raw_pt = max(8, min(24, raw_pt))                      # 8..24pt（字体对话框放宽）

    # DPI 放大（Windows 125% 缩放常见）：如果 config.font_follow_dpi=True，按 DPI/96 再乘一次
    follow_dpi = bool(cfg.get("font_follow_dpi", True))
    scale = 1.0
    if follow_dpi:
        try:
            dpi = float(app.winfo_fpixels("1i"))           # 1 英寸 = DPI 像素
            if dpi > 0:
                scale = dpi / 96.0
        except Exception:
            scale = 1.0
        # DPI 缩放后保留 0.85~1.75，防止在 4K 屏上过大或在特殊屏上过小
        scale = max(0.85, min(1.75, scale))

    # 四舍五入成整数 pt
    base_pt = max(10, int(round(raw_pt * scale)))
    bold_pt = base_pt
    tree_pt = max(10, base_pt - 1)
    log_pt  = max(10, base_pt - 1)
    tab_pt  = base_pt
    btn_big_pt = max(11, base_pt)
    h1_pt = max(12, base_pt + 2)

    # 字体族：优先用系统 UI 级雅黑/微软雅黑；英文日志用 Consolas
    family_cn = "Microsoft YaHei UI" if sys.platform == "win32" else "Microsoft YaHei"
    family_mono = "Consolas" if sys.platform == "win32" else "Menlo"

    specs = {
        "BASE":      (family_cn, base_pt),
        "BOLD":      (family_cn, bold_pt, "bold"),
        "SMALL":     (family_cn, max(10, base_pt - 1)),
        "H1":        (family_cn, h1_pt, "bold"),
        "TREE":      (family_cn, tree_pt),
        "TREEHEAD":  (family_cn, tree_pt, "bold"),
        "TAB":       (family_cn, tab_pt, "bold"),
        "BIGBTN":    (family_cn, btn_big_pt, "bold"),
        "BTN":       (family_cn, base_pt, "bold"),
        "BTN2":      (family_cn, base_pt),
        "ENTRY":     (family_cn, base_pt),
        "LABEL":     (family_cn, base_pt),
        "LOG":       (family_mono, log_pt),
        "STATUS":    (family_cn, max(10, base_pt - 1)),
        "TOOLTIP":   (family_cn, max(9, base_pt - 2)),
    }
    app._fonts = specs
    # 菜单栏右侧「字号 Npt」快捷显示：有就更新
    try:
        var = getattr(app, "_menu_font_pt_var", None)
        if isinstance(var, tk.StringVar):
            var.set(f"字号 {raw_pt}pt")
    except Exception:
        pass
    # 也存到 app.option_add，对没有显式传 font 的老控件兜底（ttk 走 theme，tk 原生会读 *Font）
    try:
        app.option_add("*Font", specs["BASE"])
        app.option_add("*Label.Font", specs["BASE"])
        app.option_add("*Button.Font", specs["BASE"])
        app.option_add("*Entry.Font", specs["ENTRY"])
        app.option_add("*Text.Font", specs["BASE"])
    except Exception:
        pass
    return specs


# ------------------------- 🧭 自绘菜单栏（设置 / 帮助：字体完全可控）-------------------------
def build_menu_bar(app) -> None:
    """
    用自绘 Frame + tk.Menubutton 做顶部菜单栏（Windows 原生 Menu 的 cascade 字体不可控）。
    参考经验 415826：不要用 app.config(menu=menubar) 依赖系统绘制，改成自己在顶部放一个 Frame，
    里面放 Menubutton，字体用 app._fonts。
    """
    F = getattr(app, "_fonts", {})
    BASE      = F.get("BASE",      ("Microsoft YaHei UI", 12))
    BOLD      = F.get("BOLD",      ("Microsoft YaHei UI", 12, "bold"))
    BTN       = F.get("BTN",       ("Microsoft YaHei UI", 12, "bold"))
    SMALL     = F.get("SMALL",     ("Microsoft YaHei UI", 11))
    MENU_ITEM = F.get("MENU_ITEM", F.get("BASE", ("Microsoft YaHei UI", 12)))

    # 菜单栏整体背景：用浅色，比主内容稍深一点做层级感
    bar = tk.Frame(app, bg=COLORS.get("menu_bar_bg", "#E1EBFF"), bd=0,
                   highlightbackground=COLORS.get("card_border", "#C7D5FF"),
                   highlightthickness=1)
    bar.pack(side=tk.TOP, fill=tk.X, padx=0, pady=0)

    # —— 1) 应用标题标签（左侧）——
    try:
        tk.Label(bar, text="  分子管理器  ",
                 bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
                 fg=COLORS.get("primary", "#3B6EFF"),
                 font=BOLD, anchor="w", padx=6, pady=4
                 ).pack(side=tk.LEFT)
    except Exception:
        pass

    # 辅助：创建一个 Menubutton + 下拉 tk.Menu
    def _make_mb(bar_parent, label: str, side=tk.LEFT):
        # Menubutton 本身 tk.Menubutton 比 ttk.Menubutton 好配色
        mb = tk.Menubutton(
            bar_parent, text=label,
            bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
            fg=COLORS.get("text", "#1A2142"),
            activebackground=COLORS.get("menu_hover_bg", "#C8D9FF"),
            activeforeground=COLORS.get("primary", "#3B6EFF"),
            font=BTN, relief=tk.FLAT, bd=0, padx=14, pady=5,
            cursor="hand2",
        )
        mb.pack(side=side, padx=0, pady=0)
        menu = tk.Menu(mb, tearoff=0,
                       bg="#FFFFFF", fg="#1A2142",
                       activebackground=COLORS.get("primary", "#3B6EFF"),
                       activeforeground="#FFFFFF",
                       font=MENU_ITEM, bd=1, relief=tk.SOLID)
        mb.configure(menu=menu)
        return mb, menu

    # —— 2) ⚙️ 设置菜单 ——
    _mb_set, menu_set = _make_mb(bar, "  ⚙️ 设置  ")
    try:
        menu_set.add_command(
            label="  🔤 字体大小…",
            command=lambda: _safe_call(app, "show_font_size_dialog_from_menu"),
        )
        menu_set.add_separator()
        # 预留给以后扩展（保留“预览前确认”等开关，先接已有变量避免空）
        try:
            _prev_var = getattr(app, "preview_before_operation_var", None)
            if _prev_var is None:
                _prev_var = tk.BooleanVar(value=True)
                app.preview_before_operation_var = _prev_var
            menu_set.add_checkbutton(
                label="  ⏱️ 文件整理前先预览（建议开启）",
                variable=_prev_var,
                onvalue=True, offvalue=False,
                command=lambda: _persist_preview_toggle(app),
            )
        except Exception:
            pass
        # 手动 OB 路径快捷入口
        menu_set.add_command(
            label="  🧭 OpenBabel 可执行路径…",
            command=lambda: _open_ob_path_dialog(app),
        )
    except Exception:
        pass

    # —— 3) ❓ 帮助菜单 ——
    _mb_help, menu_help = _make_mb(bar, "  ❓ 帮助  ")
    try:
        menu_help.add_command(
            label="  🧪 环境诊断（检查 OB / PSI4 依赖）",
            command=lambda: _safe_call(app, "show_environment_dialog_from_menu"),
        )
        menu_help.add_separator()
        # 状态栏 OB 指示灯快捷入口
        menu_help.add_command(
            label="  🧭 手动设置 OpenBabel 可执行路径…",
            command=lambda: _open_ob_path_dialog(app),
        )
        menu_help.add_command(
            label="  🔤 调整界面字体大小…",
            command=lambda: _safe_call(app, "show_font_size_dialog_from_menu"),
        )
        # 关于
        menu_help.add_separator()
        menu_help.add_command(
            label="  ℹ️ 关于",
            command=lambda: _show_about(app),
        )
    except Exception:
        pass

    # —— 4) 右侧状态：字体大小 + 工作目录信息（可选）——
    try:
        right_row = tk.Frame(bar, bg=COLORS.get("menu_bar_bg", "#E1EBFF"))
        right_row.pack(side=tk.RIGHT, padx=6, pady=0)
        # 字体大小显示（点击可快捷改）
        try:
            cfg = getattr(app, "config_data", {}) or {}
            _cur_pt = int(cfg.get("font_size", 14) or 14)
        except Exception:
            _cur_pt = 14
        _font_pt_var = tk.StringVar(value=f"字号 {_cur_pt}pt")
        _font_btn = tk.Button(
            right_row, textvariable=_font_pt_var,
            bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
            fg=COLORS.get("primary", "#3B6EFF"),
            activebackground=COLORS.get("menu_hover_bg", "#C8D9FF"),
            activeforeground=COLORS.get("primary", "#3B6EFF"),
            font=SMALL, relief=tk.FLAT, bd=0, padx=10, pady=5,
            cursor="hand2",
            command=lambda: _safe_call(app, "show_font_size_dialog_from_menu"),
        )
        _font_btn.pack(side=tk.RIGHT, padx=2, pady=0)
        app._menu_font_pt_var = _font_pt_var
    except Exception:
        pass


# ——— 菜单栏内部辅助：安全调用 app 方法（容错）———
def _safe_call(app, method_name: str):
    try:
        fn = getattr(app, method_name, None)
        if callable(fn):
            return fn()
    except Exception as _e:
        try:
            from logger import default_logger as _log
            _log.warning("菜单栏调用 %s 失败：%s", method_name, _e)
        except Exception:
            print(f"[menu] {method_name} failed:", _e)


def _persist_preview_toggle(app) -> None:
    try:
        v = bool(getattr(app, "preview_before_operation_var", None) and
                 app.preview_before_operation_var.get())
        cfg = getattr(app, "config_data", None)
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["preview_before_operation"] = v
        app.config_data = cfg
        try:
            from config import save_config
            save_config(cfg)
        except Exception:
            pass
    except Exception:
        pass


def _open_ob_path_dialog(app) -> None:
    try:
        from dialogs import Dialogs
        dlg = Dialogs(app, getattr(app, "controller", None))
        cb = getattr(app.helpers, "check_environment", None)
        dlg.show_obabel_path_dialog(
            parent=app,
            on_saved_callback=(lambda: cb(announce_missing=False) if callable(cb) else None),
        )
    except Exception as _e:
        try:
            from tkinter import messagebox
            messagebox.showerror("打开失败", f"无法打开 OpenBabel 路径设置：\n{_e}")
        except Exception:
            pass


def _show_about(app) -> None:
    try:
        from tkinter import messagebox
        try:
            cfg = getattr(app, "config_data", {}) or {}
            pt = int(cfg.get("font_size", 14) or 14)
        except Exception:
            pt = 14
        messagebox.showinfo(
            "关于 分子管理器",
            "分子管理器（MolManager）\n\n"
            "用于化学 / 物理计算文件夹整理、分子格式转换、\n"
            "OpenBabel 工具、PSI4 量化任务 / 刚性扫描 / 动画。\n\n"
            f"当前字号：{pt} pt\n"
            "  • 顶部「⚙️ 设置 → 字体大小…」可调整\n"
            "  • 右下状态栏指示灯：绿=OB 就绪，红=OB 不可用\n"
            "  • 点击指示灯可快速进入「环境诊断」\n",
            parent=app,
        )
    except Exception:
        pass


# ------------------------- 🎨 应用全局 ttk 主题 -------------------------
def apply_aurora_theme(app) -> None:
    T = AuroraTheme
    style = ttk.Style(app)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # —— 取字体基线（问题一：字太小）——
    fonts = getattr(app, "_fonts", None)
    if not fonts:
        # 兜底：build_ui 里一定先 resolve_font_specs；这里只是防调用顺序出错
        try:
            fonts = resolve_font_specs(app)
        except Exception:
            fonts = {
                "BASE":      ("Microsoft YaHei UI", 12),
                "BOLD":      ("Microsoft YaHei UI", 12, "bold"),
                "BIGBTN":    ("Microsoft YaHei UI", 13, "bold"),
                "BTN":       ("Microsoft YaHei UI", 12, "bold"),
                "TREE":      ("Microsoft YaHei UI", 11),
                "TREEHEAD":  ("Microsoft YaHei UI", 11, "bold"),
                "TAB":       ("Microsoft YaHei UI", 12, "bold"),
                "ENTRY":     ("Microsoft YaHei UI", 12),
            }

    style.configure(
        ".",
        background=T.BG_END,
        foreground=T.TEXT_MAIN,
        fieldbackground=T.CARD_BG,
        font=fonts["BASE"],
        borderwidth=0,
    )

    style.configure(
        "Aurora.TButton",
        background=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        padding=(14, 8),
        borderwidth=1,
        relief="solid",
        focusthickness=0,
        font=fonts["BTN2"],
    )
    style.map(
        "Aurora.TButton",
        background=[("active", T.glow(T.BRAND_BLUE, 0.88)), ("pressed", T.glow(T.BRAND_BLUE, 0.72))],
        foreground=[("active", T.BRAND_BLUE), ("pressed", T.TEXT_BADGE)],
        bordercolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        lightcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        darkcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
    )

    style.configure(
        "Aurora.BigAccent.TButton",
        background=T.BRAND_GREEN,
        foreground=T.TEXT_BADGE,
        padding=(18, 12),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BIGBTN"],
    )
    style.map(
        "Aurora.BigAccent.TButton",
        background=[("active", "#11B99A"), ("pressed", "#0C8873")],
        foreground=[("active", T.TEXT_BADGE), ("pressed", T.TEXT_BADGE)],
    )

    style.configure(
        "Aurora.Primary.TButton",
        background=T.BRAND_BLUE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BTN"],
    )
    style.map(
        "Aurora.Primary.TButton",
        background=[("active", "#5A85FF"), ("pressed", "#2E58D6")],
    )

    style.configure(
        "Aurora.Purple.TButton",
        background=T.BRAND_PURPLE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BTN"],
    )
    style.map(
        "Aurora.Purple.TButton",
        background=[("active", "#9B75F7"), ("pressed", "#7348D6")],
    )

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
        font=fonts["BOLD"],
        padding=(6, 0, 6, 6),
    )

    style.configure(
        "Aurora.TEntry",
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.CARD_BORDER,
        darkcolor=T.CARD_BORDER,
        padding=6,
        focusthickness=0,
        font=fonts["ENTRY"],
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
        font=fonts["ENTRY"],
    )
    style.map(
        "Aurora.TCombobox",
        bordercolor=[("focus", T.BRAND_BLUE)],
    )

    # rowheight= (pt+2)*2：让行高随字体放大，避免 Tree 字挤
    tree_row = max(26, int(fonts["TREE"][1]) * 2 + 6)
    style.configure(
        "Aurora.Treeview",
        background=T.CARD_BG,
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        rowheight=tree_row,
        borderwidth=1,
        relief="solid",
        bordercolor=T.CARD_BORDER,
        font=fonts["TREE"],
    )
    style.configure(
        "Aurora.Treeview.Heading",
        background=T.glow(T.BRAND_BLUE, 0.9),
        foreground=T.TEXT_MAIN,
        font=fonts["TREEHEAD"],
        relief="flat",
        padding=6,
        borderwidth=0,
    )
    style.map(
        "Aurora.Treeview",
        background=[("selected", T.TREE_SEL_BG)],
        foreground=[("selected", T.TREE_SEL_FG)],
    )

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
        font=fonts["TAB"],
    )
    style.map(
        "Aurora.TNotebook.Tab",
        background=[("selected", T.BRAND_BLUE)],
        foreground=[("selected", T.TEXT_BADGE), ("active", T.BRAND_BLUE)],
        expand=[("selected", (0, 0, 0, 2))],
    )

    style.configure(
        "Aurora.Horizontal.TProgressbar",
        troughcolor=T.CARD_SHADE,
        background=T.BRAND_GREEN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.BRAND_GREEN,
        darkcolor=T.BRAND_GREEN,
        thickness=14,
    )

    style.configure(
        "Aurora.TPanedwindow",
        background=T.BG_END,
        sashwidth=4,
        sashrelief="flat",
        borderwidth=0,
    )

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

    app._aurora_theme = T
    app._aurora_style = style


# ------------------------- 🎨 渐变背景画布 -------------------------
class AuroraGradientCanvas(tk.Canvas):
    def __init__(self, master, c1: str, c2: str, particles: int = 14, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        self._c1 = c1
        self._c2 = c2
        self._particles_n = particles
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Map>", self._on_map, add="+")

    def _on_map(self, _evt=None):
        try:
            w = self.winfo_width()
            h = self.winfo_height()
            if w > 1 and h > 1:
                try:
                    self.itemconfigure("content", width=w, height=h)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._redraw()
        except Exception:
            pass

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
        for y in range(0, h, 2):
            t = y / max(1, h - 1)
            t_e = t * t * (3 - 2 * t)
            col = self._rgb2hex((self._lerp(rgb1[i], rgb2[i], t_e) for i in range(3)))
            self.create_rectangle(0, y, w, y + 2, fill=col, outline=col)
        import random
        rng = random.Random(42)
        palette = [AuroraTheme.BRAND_BLUE, AuroraTheme.BRAND_GREEN, AuroraTheme.BRAND_PURPLE]
        for i in range(self._particles_n):
            cx = int(rng.uniform(0.05 * w, 0.95 * w))
            cy = int(rng.uniform(0.05 * h, 0.9 * h))
            r = int(rng.uniform(40, 150))
            col = rng.choice(palette)
            for k in range(6, 0, -1):
                alpha = 0.03 * k
                rgb = self._hex2rgb(col)
                bg = self._hex2rgb(self._c2 if cy / h > 0.5 else self._c1)
                mixed = self._rgb2hex(self._lerp(bg[i], rgb[i], alpha) for i in range(3))
                self.create_oval(cx - r * k / 6, cy - r * k / 6,
                                 cx + r * k / 6, cy + r * k / 6,
                                 fill=mixed, outline=mixed)
        try:
            w2 = self.winfo_width()
            h2 = self.winfo_height()
            if w2 > 1 and h2 > 1:
                self.itemconfigure("content", width=w2, height=h2)
        except Exception:
            pass


# ------------------------- 🎨 玻璃卡片容器 -------------------------
def make_aurora_card(parent, title: str | None = None, accent: str | None = None, *,
                     app_ref=None) -> tuple[tk.Frame, tk.Frame]:
    T = AuroraTheme
    accent = accent or T.BRAND_GREEN
    outer = tk.Frame(
        parent,
        bg=T.CARD_BORDER,
        highlightthickness=0,
        bd=0,
    )
    inner = tk.Frame(outer, bg=T.CARD_BG, bd=0, highlightthickness=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    if title:
        header = tk.Frame(inner, bg=T.CARD_BG, bd=0)
        header.pack(fill=tk.X, padx=18, pady=(16, 0))
        cap = tk.Frame(header, bg=accent, height=18, width=4, bd=0)
        cap.pack(side=tk.LEFT)
        tk.Frame(header, bg=T.glow(accent, 0.6), height=18, width=2, bd=0).pack(side=tk.LEFT)
        title_font = ("Microsoft YaHei UI", 12, "bold")
        if app_ref is not None:
            try:
                title_font = getattr(app_ref, "_fonts", {}).get("BOLD", title_font)
            except Exception:
                pass
        tk.Label(
            header,
            text=title,
            bg=T.CARD_BG,
            fg=T.TEXT_MAIN,
            font=title_font,
            padx=10, pady=0,
        ).pack(side=tk.LEFT)
        rule = tk.Frame(header, bg=T.CARD_BG, height=22)
        rule.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        tk.Frame(rule, bg=T.CARD_BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)

    return outer, inner


# ------------------------- 🫧 Tooltip 升级：玻璃胶囊 -------------------------
class ToolTip:
    def __init__(self, widget, text: str, font=None):
        self.widget = widget
        self.text = text
        self.font = font or ("Microsoft YaHei UI", 9)
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
            font=self.font,
            wraplength=320,
        ).pack()

    def _hide_tip(self):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


def add_tooltip(widget, text: str, font=None):
    ToolTip(widget, text, font=font)


# ------------------------- 🧘 主界面：大字体、扁平卡片风格 -------------------------
# 设计：
#   - 100% tk.Frame + ttk，零 Canvas / create_window 嵌套（彻底避免 content 尺寸为 0）
#   - 大字号：卡片标题 13pt / 正文 12pt / Tree 11pt（行高 30）/ 日志 13pt
#   - 分区：配置 / 操作 / 文件列表+日志 / 状态栏
#   - 兼容旧逻辑：filter_status_var / filter_ext_var 默认 "全部"
#   - 关键词过滤条（输入即搜）

COLORS = {
    "bg": "#EFF3F8",
    "card_bg": "#FFFFFF",
    "card_border": "#C8D0DC",
    "primary": "#2A6DF4",
    "success": "#1E8E5C",
    "warning": "#E67E22",
    "danger": "#E74C3C",
    "text": "#2C3E50",
    "text_light": "#5D6D7E",
}


# ------------------------- 🔧 辅助：可折叠面板（Labelframe 可「展开/收起」） -------------------------
class CollapsibleFrame(tk.LabelFrame):
    """
    一个可折叠的 LabelFrame：标题栏右侧有「▼/▶」按钮，点击后收起下方内容；
    用于「高级参数」「扫描参数」等默认折叠、不干扰新手但保持功能完整的面板。
    """

    def __init__(self, master, title: str = "", collapsed: bool = False, **kwargs):
        kwargs.setdefault("bg", COLORS["card_bg"])
        kwargs.setdefault("fg", COLORS["text"])
        # 字太小：LabelFrame 标题默认 12→至少 13pt bold（跟随 config 默认 14pt 的 BOLD 基线）
        kwargs.setdefault("font", ('Microsoft YaHei', 13, 'bold'))
        kwargs.setdefault("relief", tk.GROOVE)
        kwargs.setdefault("bd", 2)
        super().__init__(master, text=f"  {title}  ", **kwargs)
        self._collapsed = collapsed
        self._title = title
        # 子容器：所有用户内容都应塞到 self.body 里
        self.body = tk.Frame(self, bg=COLORS["card_bg"])
        # 「▼ / ▶」切换按钮：嵌入 labelwidget 机制更复杂，这里在 label_frame 的「空白」放
        # 一个小按钮到右上角即可（grid 里塞一个 LabelFrame 内没有直接右上角位置，改用叠加实现）
        self._toggle_btn = tk.Button(
            self, text="▼", relief=tk.FLAT, bg=COLORS["card_bg"], fg=COLORS["primary"],
            font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')), cursor="hand2", width=3,
            command=self._toggle,
        )
        # 用 place 放到右上角，不影响 body 的 pack/grid
        self._toggle_btn.place(relx=1.0, y=2, anchor="ne")
        self.bind("<Configure>", lambda _e: self._reposition_toggle())
        self._last_collapsed = not collapsed  # 触发初次展开/收起
        self._toggle()

    def _reposition_toggle(self):
        try:
            self._toggle_btn.place(relx=1.0, y=2, anchor="ne")
        except Exception:
            pass

    def _toggle(self):
        want_collapse = not self._collapsed
        if self._collapsed == want_collapse and self._last_collapsed == want_collapse:
            # 初始化首次：用当前状态来决定 body 显示
            want_collapse = self._collapsed
        if want_collapse:
            # 收起：body 从布局中移除
            try:
                self.body.pack_forget()
            except Exception:
                try:
                    self.body.grid_forget()
                except Exception:
                    pass
            self._toggle_btn.config(text="▶")
        else:
            # 展开：body 显示（优先 pack fill=x）
            self.body.pack(fill=tk.X, expand=False, padx=4, pady=(0, 6))
            self._toggle_btn.config(text="▼")
        self._last_collapsed = want_collapse
        self._collapsed = want_collapse


# ------------------------- 🎨 颜色调色板（保持原清爽扁平风格，新增「推荐/信息/警告/危险」按钮色） -------------------------
COLORS = {
    "bg": "#EFF3F8",
    "card_bg": "#FFFFFF",
    "card_border": "#C8D0DC",
    "primary": "#2A6DF4",
    "success": "#1E8E5C",
    "warning": "#E67E22",
    "danger": "#E74C3C",
    "text": "#2C3E50",
    "text_light": "#5D6D7E",
    # 颜色编码：按钮背景
    "btn_recommend_bg": "#1E8E5C",   # 绿色：一键修复等推荐操作
    "btn_info_bg": "#2A6DF4",        # 蓝色：信息/扫描
    "btn_warn_bg": "#E67E22",        # 橙色：警告操作
    "btn_danger_bg": "#E74C3C",      # 红色：删除
    "btn_text": "#FFFFFF",
}


def build_ui(app):
    """
    构建新版主界面：
      - 顶部全局工具栏（工作目录/最近目录/扫描/撤销重做/进度条）
      - 中部 ttk.Notebook 三标签页（📁 文件管理 / 🔬 计算与动画 / ⚙️ 高级工具）
      - 底部状态栏（状态文字 + 进度条 + 操作提示）
    **零功能损失**：所有旧变量 app.work_dir_entry / app.tree / app.log_text / app.fix_mode_var
    等名称完全保留，controller.py 与 dialogs.py 保持不改动。

    ===== 问题一（字太小）修复 =====
    - 在任何控件创建前先 resolve_font_specs，把字体基线写到 app._fonts 和 app.option_add。
    - 之后所有显式创建的 Label / Button / Entry / Combobox / Treeview / Notebook 页签 / 日志 / 状态栏 都用统一字体。
    - apply_aurora_theme 再把 ttk 控件样式改成同一套字体。
    """
    # === 字太小：Step 1. 先算字体基线 ===
    try:
        resolve_font_specs(app)
    except Exception as _e:
        # 字体计算失败不影响主流程，走系统默认
        import traceback as _tb
        print("[ui_builder] resolve_font_specs failed:", _tb.format_exc())
    apply_aurora_theme_if_available(app)

    # —— 0. 顶部菜单栏（自绘 Menubutton，平台无关；字体完全可控）——
    try:
        build_menu_bar(app)
    except Exception as _me:
        import traceback as _tb
        print("[ui_builder] build_menu_bar failed:", _tb.format_exc())

    main = tk.Frame(app, bg=COLORS["bg"])
    main.pack(fill=tk.BOTH, expand=True)
    main.grid_rowconfigure(0, weight=0)   # toolbar
    main.grid_rowconfigure(1, weight=1)   # notebook （拉伸占满）
    main.grid_rowconfigure(2, weight=0)   # status bar
    main.grid_columnconfigure(0, weight=1)

    app.configure(bg=COLORS["bg"])

    # —— 1. 顶部工具栏 ——
    build_toolbar(app, main)

    # —— 2. 三标签页 Notebook ——
    app.main_notebook = ttk.Notebook(main)
    app.main_notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 4))

    # Tab1：📁 文件管理（新手主战场）
    tab_file = tk.Frame(app.main_notebook, bg=COLORS["bg"])
    app.main_notebook.add(tab_file, text="  📁  文件管理  ")
    build_tab_file_management(app, tab_file)

    # Tab2：🔬 计算与动画（日常任务）
    tab_compute = tk.Frame(app.main_notebook, bg=COLORS["bg"])
    app.main_notebook.add(tab_compute, text="  🔬  计算与动画  ")
    build_tab_compute_and_animation(app, tab_compute)

    # Tab3：⚙️ 高级工具（专家工具箱，功能零丢失）
    tab_advanced = tk.Frame(app.main_notebook, bg=COLORS["bg"])
    app.main_notebook.add(tab_advanced, text="  ⚙️  高级工具  ")
    build_tab_advanced_tools(app, tab_advanced)

    # —— 3. 底部状态栏（替换原来的 build_status_bar，增加「操作提示」） ——
    build_status_bar_new(app)

    # —— 兼容旧 apply_filter：UI 上已删除 status/ext 下拉，默认都为 "全部" ——
    for _attr, _default in (("filter_status_var", "全部"), ("filter_ext_var", "全部")):
        v = getattr(app, _attr, None)
        if v is None:
            setattr(app, _attr, tk.StringVar(value=_default))
        else:
            try:
                v.set(_default)
            except Exception:
                pass

    # —— 关键词过滤：<KeyRelease> 实时刷新 ——
    try:
        app.filter_keyword_entry.bind("<KeyRelease>", lambda e: app.helpers.apply_filter())
    except Exception:
        pass


def apply_aurora_theme_if_available(app):
    """如果有 apply_aurora_theme 就调用（tkk Notebook/Progressbar/Button 样式更统一）。"""
    try:
        from ui_builder import apply_aurora_theme
        apply_aurora_theme(app)
    except Exception:
        pass


# ===========================================================
# 🔝 顶部全局工具栏
# ===========================================================
def build_toolbar(app, parent):
    """
    顶部工具栏：工作目录显示 + 最近目录 + 扫描/刷新 + 撤销/重做，
    进度条放到状态栏（底部），新手的主要动作集中在各标签页。
    """
    # 取字体（问题一：字太小）
    F = getattr(app, "_fonts", {})
    BASE      = F.get("BASE",      ("Microsoft YaHei", 12))
    BOLD      = F.get("BOLD",      ("Microsoft YaHei", 12, "bold"))
    SMALL_BTN = F.get("BTN2",      ("Microsoft YaHei", 12))
    ENTRY     = F.get("ENTRY",     ("Microsoft YaHei", 12))
    HINT_BTN  = F.get("SMALL",     ("Microsoft YaHei", 11))

    bar = tk.Frame(parent, bg=COLORS["card_bg"], bd=1, relief=tk.SOLID,
                   highlightbackground=COLORS["card_border"], highlightthickness=1)
    bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    bar.grid_columnconfigure(2, weight=1)

    # —— 列 0：工作目录 ——
    tk.Label(bar, text=" 📂 工作目录:", bg=COLORS["card_bg"],
             fg=COLORS["text"], font=BOLD).grid(row=0, column=0, sticky="w", padx=8, pady=6)
    app.work_dir_entry = ttk.Entry(bar, textvariable=app.work_dir_var, font=ENTRY, width=38)
    app.work_dir_entry.grid(row=0, column=1, sticky="w", padx=(0, 6), pady=6)

    def _row0_btn(text, cmd, bg=None, fg=None, tip=""):
        style_kw = {}
        if bg:
            style_kw.update(bg=bg, fg=fg or COLORS["btn_text"],
                            activebackground=bg, activeforeground=fg or COLORS["btn_text"])
        b = tk.Button(bar, text=text, command=cmd, relief=tk.RAISED, bd=1, padx=10, pady=5,
                      font=SMALL_BTN, cursor="hand2", **style_kw)
        if tip:
            add_tooltip(b, tip, font=HINT_BTN)
        return b

    _row0_btn("浏览…", app.controller.browse_work_dir,
              tip="选择新的工作目录并扫描文件").grid(row=0, column=2, sticky="w", padx=2, pady=6)
    try:
        _row0_btn("🕘 最近", app.controller.show_recent_dirs_dialog,
                  tip="从最近打开的工作目录中切换").grid(row=0, column=3, sticky="w", padx=2, pady=6)
    except Exception:
        pass

    # —— 分隔 ——
    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=4, sticky="ns", padx=8, pady=4)

    # —— 列：扫描 / 刷新 ——
    _row0_btn("🔍 扫描文件", app.controller.scan_files,
              bg=COLORS["btn_info_bg"], tip="重新扫描工作目录下的所有计算文件"
              ).grid(row=0, column=5, sticky="w", padx=2, pady=6)
    _row0_btn("🔄 刷新显示", app.controller.scan_files,
              tip="刷新文件列表显示"
              ).grid(row=0, column=6, sticky="w", padx=2, pady=6)

    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=7, sticky="ns", padx=8, pady=4)

    # —— 列：撤销 / 重做 ——
    _row0_btn("↩ 撤销", app.controller.undo_last,
              tip="撤销上一步文件操作（重命名/移动/整理等）"
              ).grid(row=0, column=8, sticky="w", padx=2, pady=6)
    try:
        _row0_btn("↪ 重做", app.controller.redo_last,
                  tip="重做被撤销的操作"
                  ).grid(row=0, column=9, sticky="w", padx=2, pady=6)
    except Exception:
        pass

    # —— 列：文件类型过滤入口 ——
    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=10, sticky="ns", padx=8, pady=4)
    tk.Label(bar, text="文件类型:", bg=COLORS["card_bg"],
             fg=COLORS["text_light"], font=getattr(app, '_fonts', {}).get('SMALL', ('Microsoft YaHei', 11))).grid(row=0, column=11, sticky="w", padx=(0, 4), pady=6)
    app.ext_display_var = tk.StringVar()
    app.helpers.update_ext_display()
    tk.Label(bar, textvariable=app.ext_display_var, bg="#E6EEF8", fg=COLORS["primary"],
             font=getattr(app, '_fonts', {}).get('LOG', ('Consolas', 12)), relief=tk.SUNKEN, padx=10, pady=2
             ).grid(row=0, column=12, sticky="w", padx=(0, 4), pady=6)
    _row0_btn("选择…", app.controller.show_ext_filter_dialog,
              tip="调整需要显示/扫描的文件扩展名"
              ).grid(row=0, column=13, sticky="w", padx=2, pady=6)


# ===========================================================
# 📁 Tab1：文件管理（新手默认页面）
# ===========================================================
def build_tab_file_management(app, parent):
    """
    文件管理页：
      - 上：映射文件管理行
      - 中：两行主操作按钮（一键修复 / 整理 / 映射 高确定性操作）
      - 下：文件列表（Treeview + 过滤） +  右侧 日志（垂直 PanedWindow 保留）
    """
    parent.grid_rowconfigure(2, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    # —— R0：映射管理（新手最困惑的点之一：把映射拉到最前面，显眼） ——
    map_card = tk.LabelFrame(parent, text="  🗂️  中英文/编号映射（可双击中文名条目编辑）  ",
                             bg=COLORS["card_bg"], fg=COLORS["text"],
                             font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')), relief=tk.GROOVE, bd=2)
    map_card.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 4))
    map_card.grid_columnconfigure(1, weight=1)

    tk.Label(map_card, text="映射文件路径:", bg=COLORS["card_bg"],
             fg=COLORS["text"], font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12))
             ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
    app.map_entry = ttk.Entry(map_card, textvariable=app.mapping_file_var, font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12)))
    app.map_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=8)

    def _card_btn(master, text, cmd, col, bg=None, tip=""):
        kw = {}
        if bg:
            kw.update(bg=bg, fg=COLORS["btn_text"], activebackground=bg, activeforeground=COLORS["btn_text"])
        b = tk.Button(master, text=text, command=cmd, font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')),
                      relief=tk.RAISED, bd=1, padx=10, pady=4, cursor="hand2", **kw)
        b.grid(row=0, column=col, padx=3, pady=8)
        if tip:
            add_tooltip(b, tip)
        return b

    _card_btn(map_card, "📂 浏览", app.controller.browse_mapping, 2, tip="选择要加载的映射文件(.txt/.csv)")
    _card_btn(map_card, "📥 加载", app.controller.load_mapping_file, 3,
              bg=COLORS["btn_info_bg"], tip="读取映射文件，立刻生效到列表")
    try:
        _card_btn(map_card, "✏️ 编辑映射", app.controller.show_mapping_editor_dialog, 4,
                  tip="打开映射编辑器：增删改中英文条目")
        _card_btn(map_card, "📊 映射管理器", app.controller.show_mapping_manager_dialog, 5,
                  tip="映射批量导入/导出/补全工具")
    except Exception:
        pass

    try:
        _card_btn(map_card, "📋 生成缺失CSV", app.controller.generate_missing, 6,
                  tip="扫描工作目录，把找不到中文名的文件名导出为 CSV 模板")
        _card_btn(map_card, "⬇ 导入CSV", (lambda: app.controller.show_mapping_manager_dialog()
                                            if hasattr(app.controller, "show_mapping_manager_dialog")
                                            else app.controller.generate_missing()), 7,
                  tip="从 CSV 导入中英文映射")
    except Exception:
        pass

    tk.Label(map_card, text="  已加载:", bg=COLORS["card_bg"],
             fg=COLORS["text_light"], font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12))
             ).grid(row=0, column=8, padx=(10, 2), pady=8, sticky="w")
    tk.Label(map_card, textvariable=app.mapping_count, bg=COLORS["card_bg"], fg=COLORS["primary"],
             font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 14, 'bold'))
             ).grid(row=0, column=9, sticky="w", pady=8)

    # —— R1：核心操作按钮（推荐操作绿色高亮，信息类蓝色，删除类红色） ——
    ops_card = tk.LabelFrame(parent, text="  ⚡  常用文件操作（推荐：先按顺序点前 3 个）  ",
                             bg=COLORS["card_bg"], fg=COLORS["text"],
                             font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')), relief=tk.GROOVE, bd=2)
    ops_card.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 4))

    def _action_btn(master, text, cmd, row, col, bg=None, tip="", width=16):
        kw = {}
        if bg:
            kw.update(bg=bg, fg=COLORS["btn_text"], activebackground=bg, activeforeground=COLORS["btn_text"])
        b = tk.Button(master, text=text, command=cmd, font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')),
                      relief=tk.RAISED, bd=1, width=width, pady=6, cursor="hand2", **kw)
        b.grid(row=row, column=col, padx=4, pady=6, sticky="ew")
        if tip:
            add_tooltip(b, tip)
        return b

    # ====== 行 1：高确定性一键式操作（新手专用） ======
    # 1.1 一键修复（推荐：绿色）
    _action_btn(ops_card, "🔧 一键修复全部", app.controller.run_fix_by_mode, 0, 0,
                bg=COLORS["btn_recommend_bg"],
                tip="依次执行：映射重命名→修复中文名→修复命名错误→修正中文内容（每项可预览取消）", width=18)
    # 1.2 按类型整理（蓝色）
    _action_btn(ops_card, "📂 按类型整理", app.controller.organize_by_type, 0, 1,
                bg=COLORS["btn_info_bg"],
                tip="按扩展名把文件移动到 mol_files/xyz_files/fchk_files 等子目录")
    # 1.3 删除重复文件（橙色警告）
    _action_btn(ops_card, "🧹 删除重复文件", app.controller.remove_duplicate_files, 0, 2,
                bg=COLORS["btn_warn_bg"],
                tip="扫描内容完全相同的重复文件并删除（会先弹确认）")
    # 1.4 生成/导出缺失 CSV（信息蓝）
    try:
        _action_btn(ops_card, "📋 生成缺失映射表", app.controller.generate_missing, 0, 3,
                    tip="把没有中文名的文件列表导出为 CSV 模板，方便批量填入后导入")
    except Exception:
        pass

    # ====== 行 2：仍常用但更具体的操作 ======
    _action_btn(ops_card, "🧪 补全 .mol 文件", app.controller.supplement_mol, 1, 0,
                tip="对有 .xyz 但缺 .mol 的文件，用 OpenBabel 自动生成 mol")
    _action_btn(ops_card, "📁 按文件名分组", app.controller.organize_by_basename, 1, 1,
                tip="按基本名（无扩展名）相同，把 .mol/.xyz/.fchk/.out 等放入同名文件夹")
    _action_btn(ops_card, "🏷️ 前缀重命名", app.controller.prefix_rename_dialog, 1, 2,
                tip="为选中的文件批量加前缀、改后缀（弹对话框配置）")
    # 删除选中（危险操作：红色）
    _action_btn(ops_card, "🗑️ 删除选中文件", app.controller.delete_selected, 1, 3,
                bg=COLORS["btn_danger_bg"],
                tip="删除列表中当前勾选的文件（建议先预览选中项）")

    # ====== 行 3：修复模式选择（高级用户可以精确选择修复类型，新手一般不用动） ======
    row3 = tk.Frame(ops_card, bg=COLORS["card_bg"])
    row3.grid(row=2, column=0, columnspan=8, sticky="ew", padx=4, pady=(0, 6))
    tk.Label(row3, text="  💡 修复模式（高级）：", bg=COLORS["card_bg"],
             fg=COLORS["text_light"], font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold'))
             ).pack(side=tk.LEFT, padx=(0, 8))
    app.fix_mode_var = tk.StringVar(value="一键修复（推荐）")
    fix_menu = ttk.Combobox(row3, textvariable=app.fix_mode_var,
                            values=["一键修复（推荐）", "映射重命名", "修复中文名", "修复命名错误", "修正中文内容"],
                            width=24, state="readonly", font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12)))
    fix_menu.pack(side=tk.LEFT, padx=3)
    add_tooltip(fix_menu, "如果你只需要单独执行某一步修复，可在此切换；否则推荐保持「一键修复」")
    tk.Button(row3, text="▶ 执行", command=app.controller.run_fix_by_mode,
              font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')), relief=tk.RAISED, bd=1, padx=12, pady=3,
              bg=COLORS["btn_recommend_bg"], fg=COLORS["btn_text"], cursor="hand2"
              ).pack(side=tk.LEFT, padx=6)

    # 让列可拉伸
    for c in range(8):
        ops_card.grid_columnconfigure(c, weight=1)

    # —— R2：文件列表 + 日志（垂直分割） ——
    _build_paned_file_and_log(app, parent, row=2, column=0)


# ===========================================================
# 🔬 Tab2：计算与动画
# ===========================================================
def build_tab_compute_and_animation(app, parent):
    """
    计算页：
      - 顶部：快速预设（RUN_PRESETS 下拉 + ▶ 运行）—— 新手零参数
      - 中部：「高级 PSI4 参数」折叠面板（展开后是完整的 PSI4 设置按钮）—— 专家使用
      - 底部：「扫描参数（线性/刚性）」折叠面板 + 反应动画按钮
    """
    parent.grid_rowconfigure(4, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    # —— R0：快速预设（新手零参数区）——
    preset_card = tk.LabelFrame(parent, text="  ⚡  快速计算预设（选一个直接运行，无需了解方法/基组细节）  ",
                                bg=COLORS["card_bg"], fg=COLORS["text"],
                                font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')), relief=tk.GROOVE, bd=2)
    preset_card.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 4))
    preset_card.grid_columnconfigure(2, weight=1)

    try:
        from constants import RUN_PRESETS
        preset_names = list(RUN_PRESETS.keys())
    except Exception:
        RUN_PRESETS = {}
        preset_names = []

    tk.Label(preset_card, text=" 🎯 选择预设:", bg=COLORS["card_bg"], fg=COLORS["text"],
             font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold'))).grid(row=0, column=0, padx=10, pady=12, sticky="w")

    # 保存下拉以便 hover 显示说明
    app.quick_preset_var = tk.StringVar(value=(preset_names[0] if preset_names else "请先定义 RUN_PRESETS"))
    preset_cb = ttk.Combobox(preset_card, textvariable=app.quick_preset_var,
                             values=preset_names, state="readonly", width=40,
                             font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12)))
    preset_cb.grid(row=0, column=1, padx=4, pady=12, sticky="w")

    # 预设说明提示：当选中变化时，tooltip 自动更新（简化：悬停预设说明静态提示）
    def _on_preset_change(_e=None):
        try:
            name = app.quick_preset_var.get()
            info = RUN_PRESETS.get(name, {})
            parts = []
            for k in ("task_type", "method", "basis", "solvent", "preset_name"):
                if k in info and info[k]:
                    parts.append(f"{k}={info[k]}")
            add_tooltip(preset_cb, f"当前预设参数：\n" + "\n".join(parts) if parts else "无")
        except Exception:
            pass

    preset_cb.bind("<<ComboboxSelected>>", _on_preset_change)
    _on_preset_change()

    def _run_quick_preset():
        """把 RUN_PRESETS[name] 对应参数填到 PSI4 对话框，并打开（所有任务仍复用 PSI4 对话框）。"""
        try:
            name = app.quick_preset_var.get()
            info = RUN_PRESETS.get(name, {})
        except Exception:
            info = {}
        # 把 RUN_PRESETS 里的参数记到 app 上，后续 PSI4 对话框可在打开时读取（若 dialogs 已支持 preset_name，则直接触发）
        app._last_run_preset_name = info.get("preset_name", info.get("name", name))
        # 最终仍然调 PSI4 对话框——**所有原参数/任务类型/溶剂/D3 完全保留**，功能零损失
        app.controller.show_psi4_dialog()

    run_btn = tk.Button(preset_card, text="▶  运行所选文件", command=_run_quick_preset,
                        font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 14, 'bold')),
                        relief=tk.RAISED, bd=1, padx=18, pady=8, cursor="hand2",
                        bg=COLORS["btn_recommend_bg"], fg=COLORS["btn_text"])
    run_btn.grid(row=0, column=3, padx=10, pady=12, sticky="e")
    add_tooltip(run_btn, "会自动打开 PSI4 完整对话框（专家参数可按需修改），默认使用预设里的方法/基组/溶剂")

    # —— R1：反应动画大按钮 + 高级对话框入口 ——
    quick_actions = tk.Frame(parent, bg=COLORS["bg"])
    quick_actions.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 4))

    def _qa_btn(text, cmd, tip="", bg=None):
        kw = {}
        if bg:
            kw.update(bg=bg, fg=COLORS["btn_text"], activebackground=bg, activeforeground=COLORS["btn_text"])
        b = tk.Button(quick_actions, text=text, command=cmd, font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')),
                      relief=tk.RAISED, bd=1, padx=12, pady=6, cursor="hand2", **kw)
        b.pack(side=tk.LEFT, padx=4, pady=4)
        if tip:
            add_tooltip(b, tip)
        return b

    _qa_btn("🎬 制作反应动画", (lambda: (
        hasattr(app.controller, "show_reaction_animation_dialog")
        and app.controller.show_reaction_animation_dialog())
        or app.controller.show_advanced_tools_dialog()),
        tip="多反应物+多产物 → 插值生成反应轨迹/能量图/动画 GIF",
        bg=COLORS["btn_info_bg"])
    _qa_btn("⚡ 打开完整 PSI4 面板", app.controller.show_psi4_dialog,
            tip="完整 PSI4 设置：任务/方法/基组/溶剂/D3/电荷/内存/扫描 等全部可调")
    _qa_btn("📊 反应能垒/能垒图", (lambda: (
        hasattr(app.controller, "show_advanced_tools_dialog")
        and app.controller.show_advanced_tools_dialog())),
        tip="打开高级工具 → 反应能垒图 / pKa / NMR 等")
    _qa_btn("📈 构象搜索 / NMR / pKa / IRC", (lambda: (
        hasattr(app.controller, "show_advanced_tools_dialog")
        and app.controller.show_advanced_tools_dialog())),
        tip="构象搜索、过渡态 IRC、pKa 预测、Boltzmann 加权 NMR")

    # —— R2：高级 PSI4 参数（可折叠，默认收起）——
    adv = CollapsibleFrame(parent, title="⚙️ 高级计算参数（专家使用，包含所有任务类型/扫描/方法/基组/溶剂/电荷/内存）",
                            collapsed=True)
    adv.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 4))

    tk.Label(adv.body, text="  完整 PSI4 对话框包含：任务类型下拉 (单点/优化/频率/扫描/过渡态/激发态/SAPT/热化学)、方法/基组、\n"
                            "  溶剂(PCM/SMD)、D3 色散、电荷/多重度、内存(GB)、步数/收敛限、线性/刚性扫描参数 等 —— 所有原功能全部可用。",
             wraplength=900, justify="left",
             bg=COLORS["card_bg"], fg=COLORS["text_light"],
             font=getattr(app, '_fonts', {}).get('SMALL', ('Microsoft YaHei', 11))).pack(anchor="w", padx=8, pady=6)
    row_b = tk.Frame(adv.body, bg=COLORS["card_bg"])
    row_b.pack(fill="x", padx=8, pady=(0, 8))
    tk.Button(row_b, text="⚡ 打开 PSI4 完整设置对话框", command=app.controller.show_psi4_dialog,
              font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')), relief=tk.RAISED, bd=1, padx=14, pady=6,
              cursor="hand2", bg=COLORS["btn_info_bg"], fg=COLORS["btn_text"]
              ).pack(side=tk.LEFT, padx=4)
    try:
        tk.Button(row_b, text="🛠 高级扫描（线性/刚性）", command=app.controller.show_advanced_tools_dialog,
                  font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')), relief=tk.RAISED, bd=1, padx=14, pady=6,
                  cursor="hand2", bg=COLORS["btn_warn_bg"], fg=COLORS["btn_text"]
                  ).pack(side=tk.LEFT, padx=4)
    except Exception:
        pass

    # —— R3：扫描参数（可折叠）+ 说明 ——
    scan_adv = CollapsibleFrame(parent, title="📈 线性/刚性扫描参数（用于势能面 PES 扫描）", collapsed=True)
    scan_adv.grid(row=3, column=0, sticky="ew", padx=4, pady=(2, 4))
    tk.Label(scan_adv.body, text="  线性扫描：两个端点结构 → 线性插值 N 帧 → 每帧跑单点能 → 能垒 CSV/图；\n"
                                "  刚性扫描：固定某个二面角/键长/键角步进，其他自由优化（完整 PSI4 对话框里可配置）。",
             wraplength=900, justify="left",
             bg=COLORS["card_bg"], fg=COLORS["text_light"],
             font=getattr(app, '_fonts', {}).get('SMALL', ('Microsoft YaHei', 11))).pack(anchor="w", padx=8, pady=6)
    tk.Button(scan_adv.body, text="📊 打开高级扫描/能垒图工具", command=app.controller.show_advanced_tools_dialog,
              font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')), relief=tk.RAISED, bd=1, padx=12, pady=5,
              cursor="hand2", bg=COLORS["btn_info_bg"], fg=COLORS["btn_text"]
              ).pack(anchor="w", padx=8, pady=(0, 8))

    # —— R4：文件列表 + 日志（垂直分割），方便选中文件后直接跑 PSI4 ——
    _build_paned_file_and_log(app, parent, row=4, column=0, show_in_tab2=True)


# ===========================================================
# ⚙️ Tab3：高级工具（子 Notebook 4 页）
# ===========================================================
def build_tab_advanced_tools(app, parent):
    """
    高级工具页：子 Notebook 4 页（分子工具 / 波函数 / 动力学 / 数据管理），
    所有原 OpenBabel + PSI4 高级对话框 + 历史/结果浏览/目录同步 入口全部收纳。
    功能零损失。
    """
    parent.grid_rowconfigure(0, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    nb = ttk.Notebook(parent)
    nb.grid(row=0, column=0, sticky="nsew", padx=2, pady=(6, 4))
    app.advanced_notebook = nb

    # —— 子页 1：分子工具（OB 全家桶 + 分子式） ——
    t1 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t1, text="  🧪  分子工具 (OB)  ")
    _adv_grid_of_buttons(t1, [
        ("🔬 OpenBabel 工具（全功能）", app.controller.show_openbabel_dialog, True,
         "格式转换/SMILES生成/描述符/叠加/2D预览/手性/pH加氢/SDF拆分/InChIKey"),
        ("🧮 分子式/分子量/元素分析", lambda: app.dialogs.show_formula_dialog()
         if hasattr(app, "dialogs") and hasattr(app.dialogs, "show_formula_dialog") else None, False,
         "从 XYZ/MOL/INP 等解析分子式、精确质量、元素百分比"),
        ("🔎 最近工作目录", app.controller.show_recent_dirs_dialog, False,
         "快速切换到之前打开过的工作目录"),
        ("📐 导出几何参数 CSV", lambda: app.controller.export_geometry_csv()
         if hasattr(app.controller, "export_geometry_csv") else None, False,
         "把文件列表里分子的键长/键角/二面角批量导出 CSV"),
    ])

    # —— 子页 2：波函数与分析（PSI4 所有高级 + NMR/pKa/IRC） ——
    t2 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t2, text="  🧠  波函数 / NMR / pKa  ")
    _adv_grid_of_buttons(t2, [
        ("⚡ PSI4 完整计算（所有任务类型）", app.controller.show_psi4_dialog, True,
         "单点/优化/频率/过渡态/激发态/SAPT/热化学 + 溶剂/D3/内存/电荷"),
        ("📊 高级扫描（线性/刚性/能垒图）", app.controller.show_advanced_tools_dialog, True,
         "势能面 PES 线性扫描、刚性扫描、能垒曲线"),
        ("🎞️ IRC + 反应路径动画", app.controller.show_advanced_tools_dialog, False,
         "从 TS 结构跑 IRC 前向/反向，导出动画帧"),
        ("🧪 Boltzmann 加权 ¹H NMR 模拟", app.controller.show_advanced_tools_dialog, False,
         "OB 构象搜索 + PSI4 CPHF NMR σ + TMS 参考 → δ + Lorentz 展宽 PNG"),
        ("⚗️ pKa 热力学循环预测", app.controller.show_advanced_tools_dialog, False,
         "SMD/water 水相单点 + H+(aq) 经验值 → pKa 估算 ±2"),
        ("🧩 构象搜索（OB MMFF + PSI4 高精度）", app.controller.show_advanced_tools_dialog, False,
         "多构象搜索 + Boltzmann 权重"),
        ("🧬 反应路径能垒图", app.controller.show_advanced_tools_dialog, False,
         "多步反应路径 Ea/ΔG 能垒图 + CSV 导出"),
    ])

    # —— 子页 3：动画与分子可视化 ——
    t3 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t3, text="  🎬  动画 / 反应路径  ")
    _adv_grid_of_buttons(t3, [
        ("🎬 反应动画生成器", (lambda: (
            hasattr(app.controller, "show_reaction_animation_dialog")
            and app.controller.show_reaction_animation_dialog())), True,
         "多反应物+多产物 → 自动对齐原子 → 插值 N 帧轨迹 → 能量 CSV + SDF/XYZ"),
        ("🛠 高级工具箱（反应动画/NMR/pKa/IRC 综合入口）", app.controller.show_advanced_tools_dialog, False,
         "综合高级功能单页入口"),
        ("🎞 结果浏览器 / 轨迹播放", (lambda: (
            hasattr(app.controller, "show_results_browser_dialog")
            and app.controller.show_results_browser_dialog())), False,
         "浏览 PSI4 .out/.fchk、动画轨迹、NMR PNG/CSV 等产物"),
    ])

    # —— 子页 4：数据管理（历史/结果/目录同步/映射编辑器） ——
    t4 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t4, text="  🗂️  数据管理 / 历史  ")
    _adv_grid_of_buttons(t4, [
        ("📜 操作历史（撤销/重做列表）", (lambda: (
            hasattr(app.controller, "show_history_dialog")
            and app.controller.show_history_dialog())), False,
         "查看所有已执行文件操作，支持逐条撤销/重做"),
        ("🔍 结果浏览器（PSI4 输出/谱图）", (lambda: (
            hasattr(app.controller, "show_results_browser_dialog")
            and app.controller.show_results_browser_dialog())), False,
         "按工作目录浏览计算输出 .out/.fchk/.log、NMR 图、反应 CSV"),
        ("🔄 目录同步 / 差异比对", (lambda: (
            hasattr(app.controller, "show_diff_sync_dialog")
            and app.controller.show_diff_sync_dialog())), False,
         "两个目录间双向 diff：缺失项、同名不同内容，选择同步方向"),
        ("✏️ 映射编辑器", (lambda: (
            hasattr(app.controller, "show_mapping_editor_dialog")
            and app.controller.show_mapping_editor_dialog())), False,
         "逐条增删改中英文映射条目（即时生效）"),
        ("📊 映射管理器（导入/导出/补全）", (lambda: (
            hasattr(app.controller, "show_mapping_manager_dialog")
            and app.controller.show_mapping_manager_dialog())), False,
         "批量导入 CSV / 导出模板 / 从现有文件补全"),
    ])


def _adv_grid_of_buttons(parent, buttons_spec):
    """
    以 2 列网格形式放置「高级工具按钮」，每个按钮：
    (文字, 回调, 是否高亮主色, tooltip文字)
    按钮下方自动有小字 tooltip 说明，新手友好。
    """
    container = tk.Frame(parent, bg=COLORS["bg"])
    container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    for i in range(2):
        container.grid_columnconfigure(i, weight=1)

    for idx, spec in enumerate(buttons_spec):
        text, cmd, highlight, tip = (spec + (None,))[:4] if len(spec) < 4 else spec
        r, c = divmod(idx, 2)
        card = tk.Frame(container, bg=COLORS["card_bg"], bd=1, relief=tk.SOLID,
                        highlightbackground=COLORS["card_border"], highlightthickness=1)
        card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
        card.grid_rowconfigure(0, weight=0)
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        # 按钮：占满卡片宽
        bg = COLORS["btn_info_bg"] if highlight else COLORS["card_bg"]
        fg = COLORS["btn_text"] if highlight else COLORS["text"]
        btn = tk.Button(card, text=text, command=cmd, font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 13, 'bold')),
                        relief=tk.RAISED, bd=1, pady=10, cursor="hand2",
                        bg=bg, fg=fg, activebackground=bg, activeforeground=fg)
        btn.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        if tip:
            add_tooltip(btn, tip)
            # tooltip 文字也同时显示在卡片下方（避免用户不知道要悬停）
            tk.Label(card, text="💡 " + (tip if len(tip) <= 96 else tip[:94] + "…"),
                     wraplength=360, justify="left",
                     bg=COLORS["card_bg"], fg=COLORS["text_light"],
                     font=getattr(app, '_fonts', {}).get('SMALL', ('Microsoft YaHei', 11))).grid(row=1, column=0, sticky="nw", padx=10, pady=(0, 8))


# ===========================================================
# 📊 公共：文件列表 + 日志（垂直分割）
# ===========================================================
def _build_paned_file_and_log(app, parent, row, column, show_in_tab2: bool = False):
    """
    文件列表 + 日志 垂直 PanedWindow。
    注意：**app.tree / app.log_text / app.context_menu / app.filter_keyword_entry / filter_count_var 只创建一次**，
    第二次调用（tab2 复用）时，就不创建 Treeview/Log 控件，而是放一个占位提示：
    「切回「📁 文件管理」页查看文件列表与日志」，避免多份 UI 导致 controller 引用错漏。
    这保证 controller.py/dialogs.py 里所有对 app.tree / app.log_text 的引用仍然唯一、功能零损失。
    """
    if hasattr(app, "_file_log_paned_built") and app._file_log_paned_built:
        # Tab2 版本：显示一个友好的占位卡片，提示当前文件列表在 Tab1；右侧放常用按钮直通 Tab1
        placeholder = tk.Frame(parent, bg=COLORS["bg"])
        placeholder.grid(row=row, column=column, sticky="nsew", pady=(0, 4))
        card = tk.Frame(placeholder, bg=COLORS["card_bg"], bd=1, relief=tk.SOLID,
                        highlightbackground=COLORS["card_border"], highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(card, text="\n   💡 提示：当前选中的文件列表、日志输出请在左侧「📁 文件管理」标签页查看。\n"
                            "   在这里选择预设并点「运行」后，会自动打开 PSI4 对话框。\n",
                 bg=COLORS["card_bg"], fg=COLORS["text_light"],
                 font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12)), justify="left").pack(padx=16, pady=20, anchor="w")

        def _jump_tab1():
            try:
                app.main_notebook.select(0)
            except Exception:
                pass

        row_b = tk.Frame(card, bg=COLORS["card_bg"])
        row_b.pack(anchor="w", padx=16, pady=(0, 20))
        tk.Button(row_b, text="跳转到 📁 文件管理页", command=_jump_tab1,
                  font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')), relief=tk.RAISED, bd=1, padx=12, pady=5,
                  cursor="hand2", bg=COLORS["btn_info_bg"], fg=COLORS["btn_text"]).pack(side=tk.LEFT, padx=4)
        tk.Button(row_b, text="🔍 立刻扫描文件列表", command=app.controller.scan_files,
                  font=getattr(app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')), relief=tk.RAISED, bd=1, padx=12, pady=5,
                  cursor="hand2").pack(side=tk.LEFT, padx=4)
        return

    paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
    paned.grid(row=row, column=column, sticky="nsew", pady=(0, 4))
    parent.grid_rowconfigure(row, weight=1)
    app._file_log_paned_built = True

    # ---------- 文件列表 ----------
    list_frame = tk.LabelFrame(paned, text="📄 文件列表（右键删除 / 双击编辑中文名）", bg=COLORS["card_bg"],
                               font=getattr(app, '_fonts', {}).get('H1', ('Microsoft YaHei', 14, 'bold')), relief=tk.GROOVE, bd=2)
    paned.add(list_frame, weight=2)

    # 🔎 关键词过滤条（输入即搜）
    filter_row = tk.Frame(list_frame, bg=COLORS["card_bg"])
    filter_row.pack(fill=tk.X, padx=8, pady=6)
    tk.Label(filter_row, text="🔎 关键词:", bg=COLORS["card_bg"],
             fg=COLORS["text"], font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 13))).pack(side=tk.LEFT, padx=(0, 6))
    app.filter_keyword_var = getattr(app, "filter_keyword_var", None) or tk.StringVar()
    app.filter_keyword_entry = ttk.Entry(
        filter_row, textvariable=app.filter_keyword_var, width=30,
        font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 13)),
    )
    app.filter_keyword_entry.pack(side=tk.LEFT, padx=(0, 8))
    app.filter_keyword_entry.bind("<KeyRelease>", lambda e: app.helpers.apply_filter())
    ttk.Button(filter_row, text="清除",
               command=lambda: (app.filter_keyword_var.set(""), app.helpers.apply_filter()),
               width=8).pack(side=tk.LEFT)
    if not getattr(app, "filter_count_var", None):
        app.filter_count_var = tk.StringVar(value="共 0 / 0 个")
    tk.Label(filter_row, textvariable=app.filter_count_var,
             bg=COLORS["card_bg"], fg=COLORS["primary"],
             font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 14, 'bold'))).pack(side=tk.LEFT, padx=(16, 0))

    columns = ("文件名", "状态", "英文名", "中文名")
    app.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=18)
    app.tree.heading("文件名", text="文件名")
    app.tree.heading("状态", text="状态")
    app.tree.heading("英文名", text="英文名")
    app.tree.heading("中文名", text="中文名")
    app.tree.column("文件名", width=350, anchor=tk.W)
    app.tree.column("状态", width=160, anchor=tk.CENTER)
    app.tree.column("英文名", width=220, anchor=tk.W)
    app.tree.column("中文名", width=220, anchor=tk.W)

    style = ttk.Style()
    style.configure("Treeview", font=getattr(app, '_fonts', {}).get('BASE', ('Microsoft YaHei', 12)), rowheight=30)
    style.configure("Treeview.Heading", font=getattr(app, '_fonts', {}).get('BOLD', ('Microsoft YaHei', 14, 'bold')))

    vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=app.tree.yview)
    app.tree.configure(yscrollcommand=vsb.set)
    app.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    app.context_menu = tk.Menu(app, tearoff=0)
    app.context_menu.add_command(label="🗑️ 删除选中文件", command=app.controller.delete_selected)
    app.tree.bind("<Button-3>", app.controller.show_context_menu)

    # ---------- 日志 ----------
    log_frame = tk.LabelFrame(paned, text="📋 日志（所有操作/错误实时显示）", bg=COLORS["card_bg"],
                              font=getattr(app, '_fonts', {}).get('H1', ('Microsoft YaHei', 14, 'bold')), relief=tk.GROOVE, bd=2)
    paned.add(log_frame, weight=1)

    log_toolbar = tk.Frame(log_frame, bg=COLORS["card_bg"])
    log_toolbar.pack(fill=tk.X, padx=8, pady=6)
    ttk.Button(log_toolbar, text="🗑️ 清空日志", command=app.helpers.clear_log,
               width=12).pack(side=tk.LEFT)

    app.log_text = scrolledtext.ScrolledText(log_frame, height=10, wrap=tk.WORD,
                                             font=getattr(app, '_fonts', {}).get('LOG', ('Consolas', 13)), bg="#F8FAFC", fg=COLORS["text"])
    app.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    app.log_text.tag_config("info", foreground=COLORS["text"])
    app.log_text.tag_config("success", foreground=COLORS["success"])
    app.log_text.tag_config("error", foreground=COLORS["danger"])
    app.log_text.tag_config("warning", foreground=COLORS["warning"])


# ===========================================================
# 📊 底部状态栏（新版：状态 + 进度 + 操作提示 + OB 指示灯）
# ===========================================================
def build_status_bar_new(app):
    """
    替换旧 build_status_bar：
    - 左侧：status_var（就绪/处理中）
    - 中左：操作提示 tip_var（上一个按钮做了什么、下一步建议）
    - 右侧：进度条 + 清除日志按钮 + OB 状态指示灯（绿/红圆点，点击看诊断）
    """
    # 字体（问题一：字太小）
    F = getattr(app, "_fonts", {})
    STATUS_F  = F.get("STATUS",  ("Microsoft YaHei", 11))
    TIP_F     = F.get("BASE",    ("Microsoft YaHei", 12))
    BTN_F     = F.get("BTN2",    ("Microsoft YaHei", 12))
    IND_BOLD  = F.get("BOLD",    ("Microsoft YaHei", 12, "bold"))

    status_frame = tk.Frame(app, bg="#E6ECF4", bd=0, relief=tk.FLAT)
    status_frame.pack(side=tk.BOTTOM, fill=tk.X)

    app.status_var = getattr(app, "status_var", None) or tk.StringVar(value="就绪")
    status_label = tk.Label(status_frame, textvariable=app.status_var, relief=tk.SUNKEN,
                            anchor=tk.W, font=STATUS_F,
                            bg=COLORS["card_bg"], fg=COLORS["text"], padx=10, pady=4)
    status_label.pack(side=tk.LEFT, fill=tk.X, expand=False, padx=(8, 6), pady=4)
    try:
        status_label.configure(width=28)
    except Exception:
        pass

    # 新增：操作提示 label（「按钮点击后给用户看下一步做什么」）
    app.action_tip_var = tk.StringVar(value="💡 新手推荐：先在左侧工作目录点「浏览」选文件夹 → 点「🔧 一键修复全部」")
    tip_label = tk.Label(status_frame, textvariable=app.action_tip_var,
                         anchor=tk.W, font=TIP_F,
                         bg="#E6ECF4", fg=COLORS["primary"], padx=8)
    tip_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=4)

    # —— 问题三：OpenBabel 指示灯（绿点 = 可用 / 红点 = 不可用，悬停显示摘要，点击 = 环境诊断）——
    app.ob_status_var = tk.StringVar(value="OB: 检测中…")
    app.ob_dot_canvas: tk.Canvas | None = None  # 后面 MainView 写状态会 set 颜色
    ob_frame = tk.Frame(status_frame, bg="#E6ECF4", bd=0)
    ob_frame.pack(side=tk.RIGHT, padx=(0, 6), pady=4)
    # 圆点画布（18x18，直径 14）
    dot_c = tk.Canvas(ob_frame, width=18, height=18, bg="#E6ECF4", highlightthickness=0, bd=0, cursor="hand2")
    dot_c.pack(side=tk.LEFT, padx=(0, 4))
    dot_c.create_oval(2, 2, 16, 16, fill="#B7C0D6", outline="#B7C0D6", tags="dot")  # 灰色 = 还未检测
    app.ob_dot_canvas = dot_c
    ob_text = tk.Label(ob_frame, textvariable=app.ob_status_var,
                       bg="#E6ECF4", fg=COLORS["text"], font=IND_BOLD, cursor="hand2")
    ob_text.pack(side=tk.LEFT)
    # 点击画布 or 文本 → 打开环境诊断（helpers 里提供该方法）
    def _on_click_ob(_evt=None):
        try:
            if hasattr(app, "helpers") and hasattr(app.helpers, "show_env_diagnosis_dialog"):
                app.helpers.show_env_diagnosis_dialog()
        except Exception as _e:
            try:
                from tkinter import messagebox as _mb
                _mb.showinfo("环境诊断", f"环境诊断调用失败：{_e}")
            except Exception:
                pass
    dot_c.bind("<Button-1>", _on_click_ob)
    ob_text.bind("<Button-1>", _on_click_ob)
    add_tooltip(ob_frame,
                "OpenBabel 状态：\n  ● 绿色 = 可用\n  ● 红色 = 不可用\n点击查看诊断 / 手动设置 obabel 路径")

    # 进度条
    app.progress_var = getattr(app, "progress_var", None) or tk.DoubleVar(value=0.0)
    app.progress_bar = ttk.Progressbar(status_frame, variable=app.progress_var, maximum=100, length=220)
    app.progress_bar.pack(side=tk.RIGHT, padx=8, pady=4)
    ttk.Button(status_frame, text="清除日志", command=app.helpers.clear_log,
               ).pack(side=tk.RIGHT, padx=(0, 8), pady=4)

    # —— 便捷：把常用按钮的动作提示写出来（通过 monkey-patch helpers.on_log 很危险，不如在几个常用函数包一层）——
    _inject_action_tips(app)


def _inject_action_tips(app):
    """
    把常见 controller 动作包一层「动作完成后写提示到 action_tip_var」。
    非侵入式：用 try/except，失败不影响功能。
    """
    def _tip(msg: str):
        try:
            app.action_tip_var.set("💡 " + msg)
        except Exception:
            pass

    # 给几个最常用的控制器函数包装
    pairs = [
        ("scan_files", "已扫描文件列表，下一步：点「🔧 一键修复全部」自动处理命名问题"),
        ("run_fix_by_mode", "修复已完成。下一步：点「📂 按类型整理」或「📁 按文件名分组」归档"),
        ("organize_by_type", "已按扩展名整理归档。下一步：选文件 → 切到「🔬 计算与动画」运行预设"),
        ("organize_by_basename", "已按基本名分组（每个分子一个子目录）。下一步：点「生成缺失映射表」批量补名"),
        ("load_mapping_file", "映射已加载！列表里中文名已更新。下一步：点「一键修复全部」执行映射重命名"),
        ("generate_missing", "缺失的文件名已导出 CSV。填完中文名后，用「映射管理器」导入即可"),
        ("undo_last", "已撤销上一步。需要前进？点工具栏「↪ 重做」"),
        ("remove_duplicate_files", "重复文件清理完成。建议先点「扫描文件」确认结果"),
    ]
    for name, tip in pairs:
        try:
            original = getattr(app.controller, name)

            def _wrap(fn, t):
                def _w(*a, **kw):
                    try:
                        ret = fn(*a, **kw)
                    finally:
                        try:
                            _tip(t)
                        except Exception:
                            pass
                    return ret
                return _w
            setattr(app.controller, name, _wrap(original, tip))
        except Exception:
            pass
