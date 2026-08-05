# -*- coding: utf-8 -*-
"""
深色护眼主题系统（UI 重构核心）。

设计目标（见 UI_DESIGN.md）：
  - 深色背景层级 + 青绿强调色，降低长时间盯计算/日志的视觉疲劳；
  - 通过「ttk.Style 配置」+「tk 全局 option_add 默认」双重覆盖，
    让现有 tk / ttk 控件在不动业务逻辑的前提下自动转深色；
  - 覆盖 apply_aurora_theme 配置的 Aurora.* 样式，确保深色为权威主题。

只动视觉层，对外契约（app.log_text / progress_var / status_var /
ext_display_var / mapping_count / set_cancel_visible / task_manager /
helpers / on_task_*）一律不变。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------- 深色调色板（与 UI_DESIGN.md 完全一致） ----------
DARK = {
    "bg":             "#0F1419",  # 应用主背景
    "surface":        "#161B22",  # 侧边栏 / 卡片 / 状态栏
    "elevated":       "#1C2330",  # 悬停 / 抬升 / 输入框底
    "input":          "#0D1117",  # 输入框 / 文本框底
    "border":         "#232B3A",  # 分隔线 / 卡片描边
    "accent":         "#2DD4BF",  # 主强调（青绿）
    "accent_hover":   "#5EEAD4",
    "link":           "#58A6FF",  # 链接 / 信息
    "text":           "#E6EDF3",  # 正文
    "text_secondary": "#9DA7B3",  # 次要说明
    "text_hint":      "#8B97AC",  # 占位 / 禁用（已提亮，避免与深色背景对比过低）
    "success":        "#3FB950",
    "warning":        "#D29922",
    "error":          "#F85149",
    "error_hover":    "#FF7B72",
}


def bind_treeview_hover(tree, hover_bg="#20283A"):
    """给 Treeview 行加悬停高亮：合并 tag（不丢失已有 tag，如状态色）、

    不覆盖已选中行、鼠标离开时清除。"""
    try:
        tree.tag_configure("tv_hover", background=hover_bg)
    except Exception:
        pass
    last = {"iid": None}

    def _tags(iid):
        try:
            return list(tree.item(iid, "tags"))
        except Exception:
            return []

    def _add(iid):
        if iid in tree.selection():
            return
        tg = _tags(iid)
        if "tv_hover" not in tg:
            tree.item(iid, tags=tg + ["tv_hover"])

    def _remove(iid):
        tg = _tags(iid)
        if "tv_hover" in tg:
            tg.remove("tv_hover")
            tree.item(iid, tags=tg)

    def _motion(evt):
        try:
            row = tree.identify_row(evt.y)
        except Exception:
            return
        if row == last["iid"]:
            return
        if last["iid"]:
            _remove(last["iid"])
        last["iid"] = row
        if row:
            _add(row)

    def _leave(_evt):
        if last["iid"]:
            _remove(last["iid"])
            last["iid"] = None

    tree.bind("<Motion>", _motion)
    tree.bind("<Leave>", _leave)


def apply_dark_theme(root: tk.Tk | tk.Toplevel) -> None:
    """把整个 Tk 应用切换为深色护眼主题。须在 resolve_font_specs 之后调用。"""
    F = getattr(root, "_fonts", None) or {}
    BASE  = F.get("BASE",      ("Microsoft YaHei", 12))
    BOLD  = F.get("BOLD",      ("Microsoft YaHei", 12, "bold"))
    BTN   = F.get("BTN",       ("Microsoft YaHei", 12, "bold"))
    ENTRY = F.get("ENTRY",     ("Microsoft YaHei", 12))
    TREE  = F.get("TREE",      ("Microsoft YaHei", 11))
    THEAD = F.get("TREEHEAD",  ("Microsoft YaHei", 11, "bold"))

    # ---------- 1) tk 控件全局默认（覆盖显式 bg 之外的默认外观） ----------
    root.configure(bg=DARK["bg"])
    _opt = root.option_add
    _opt("*Background", DARK["bg"])
    _opt("*Foreground", DARK["text"])
    _opt("*Frame.Background", DARK["bg"])
    _opt("*Label.Background", DARK["bg"])
    _opt("*Label.Foreground", DARK["text"])
    _opt("*Button.Background", DARK["elevated"])
    _opt("*Button.Foreground", DARK["text"])
    _opt("*Button.HighlightBackground", DARK["border"])
    _opt("*Button.HighlightColor", DARK["accent"])
    _opt("*Entry.Background", DARK["input"])
    _opt("*Entry.Foreground", DARK["text"])
    _opt("*Entry.InsertBackground", DARK["text"])
    _opt("*Entry.HighlightBackground", DARK["border"])
    _opt("*Text.Background", DARK["input"])
    _opt("*Text.Foreground", DARK["text"])
    _opt("*Text.InsertBackground", DARK["text"])
    _opt("*Text.SelectBackground", DARK["accent"])
    _opt("*Text.SelectForeground", DARK["bg"])
    _opt("*Listbox.Background", DARK["input"])
    _opt("*Listbox.Foreground", DARK["text"])
    _opt("*Listbox.SelectBackground", DARK["accent"])
    _opt("*Listbox.SelectForeground", DARK["bg"])
    _opt("*Canvas.Background", DARK["bg"])
    _opt("*Labelframe.Background", DARK["bg"])
    _opt("*Labelframe.Foreground", DARK["text"])
    _opt("*Menu.Background", DARK["surface"])
    _opt("*Menu.Foreground", DARK["text"])
    _opt("*Menubutton.Background", DARK["surface"])
    _opt("*Menubutton.Foreground", DARK["text"])
    _opt("*Toplevel.Background", DARK["surface"])
    # Combobox 下拉列表（这是 tk 原生 listbox，需单独配）
    _opt("*TCombobox*Listbox.background", DARK["input"])
    _opt("*TCombobox*Listbox.foreground", DARK["text"])
    _opt("*TCombobox*Listbox.selectBackground", DARK["accent"])
    _opt("*TCombobox*Listbox.selectForeground", DARK["bg"])
    # 注意：font 必须传「元组字体规格」而非裸字体名字符串。
    # 裸字符串 "Microsoft YaHei" 会被 Tk 当作字体 spec 列表解析
    # （Microsoft=family, YaHei=size），导致 Post 下拉时抛
    # "expected integer but got YaHei"，下拉 listbox 创建失败 → 下拉无选项。
    # 传元组后 tkinter 会转成 "{Microsoft YaHei} 12" 这种合法 spec。
    _opt("*TCombobox*Listbox.font", ENTRY)

    # ---------- 2) ttk 样式（权威覆盖，含 Aurora.*） ----------
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # clam 支持 background/fieldbackground 等细粒度配置
    except tk.TclError:
        pass

    style.configure(".", background=DARK["bg"], foreground=DARK["text"],
                   font=BASE, borderwidth=0)
    style.configure("TFrame", background=DARK["bg"])
    style.configure("TLabel", background=DARK["bg"], foreground=DARK["text"], font=BASE)

    # 次按钮（默认）
    style.configure("TButton", background=DARK["elevated"], foreground=DARK["text"],
                    bordercolor=DARK["border"], lightcolor=DARK["elevated"],
                    darkcolor=DARK["elevated"], padding=(12, 6), font=BTN,
                    relief="solid", borderwidth=1)
    style.map("TButton",
              background=[("active", DARK["border"]), ("pressed", DARK["input"])],
              foreground=[("active", DARK["accent"]), ("pressed", DARK["text"])],
              bordercolor=[("active", DARK["accent"]), ("!active", DARK["border"])])

    # 主强调按钮
    style.configure("Accent.TButton", background=DARK["accent"], foreground=DARK["bg"],
                    borderwidth=0, padding=(14, 7), font=BTN, relief="flat")
    style.map("Accent.TButton",
              background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])

    # 危险按钮
    style.configure("Danger.TButton", background=DARK["error"], foreground=DARK["bg"],
                    borderwidth=0, padding=(12, 6), font=BTN, relief="flat")
    style.map("Danger.TButton",
              background=[("active", DARK["error_hover"]), ("pressed", DARK["error"])])

    # 覆盖 aurora 主题（确保深色为权威）
    for _name in ("Aurora.TButton", "Aurora.Purple.TButton"):
        style.configure(_name, background=DARK["elevated"], foreground=DARK["text"],
                        bordercolor=DARK["border"], lightcolor=DARK["elevated"],
                        darkcolor=DARK["elevated"], padding=(12, 6), font=BTN,
                        relief="solid", borderwidth=1)
        style.map(_name,
                  background=[("active", DARK["border"]), ("pressed", DARK["input"])],
                  foreground=[("active", DARK["accent"]), ("pressed", DARK["text"])])
    style.configure("Aurora.Primary.TButton", background=DARK["accent"],
                    foreground=DARK["bg"], borderwidth=0, relief="flat",
                    padding=(14, 7), font=BTN)
    style.map("Aurora.Primary.TButton",
              background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])
    style.configure("Aurora.BigAccent.TButton", background=DARK["accent"],
                    foreground=DARK["bg"], borderwidth=0, relief="flat",
                    padding=(16, 9), font=BTN)
    style.map("Aurora.BigAccent.TButton",
              background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])

    # 输入框 / 下拉
    style.configure("TEntry", fieldbackground=DARK["input"], foreground=DARK["text"],
                    bordercolor=DARK["border"], lightcolor=DARK["input"],
                    darkcolor=DARK["input"], padding=4, insertcolor=DARK["text"], font=ENTRY)
    style.configure("TCombobox", fieldbackground=DARK["input"], foreground=DARK["text"],
                    background=DARK["elevated"], bordercolor=DARK["border"],
                    arrowcolor=DARK["text"], padding=4, font=ENTRY)
    style.map("TCombobox", fieldbackground=[("readonly", DARK["input"])],
              foreground=[("readonly", DARK["text"])])

    # Notebook（兼容残留使用）
    style.configure("TNotebook", background=DARK["bg"], bordercolor=DARK["border"])
    style.configure("TNotebook.Tab", background=DARK["surface"],
                    foreground=DARK["text_secondary"], padding=(12, 6), font=BOLD)
    style.map("TNotebook.Tab", background=[("selected", DARK["accent"])],
              foreground=[("selected", DARK["bg"])])

    # 进度条
    style.configure("TProgressbar", troughcolor=DARK["elevated"], background=DARK["accent"],
                    borderwidth=0, thickness=8)

    # Treeview（文件列表 / 结果表）
    style.configure("Treeview", background=DARK["input"], foreground=DARK["text"],
                    fieldbackground=DARK["input"], bordercolor=DARK["border"],
                    rowheight=26, font=TREE)
    style.map("Treeview", background=[("selected", DARK["accent"])],
              foreground=[("selected", DARK["bg"])])
    style.configure("Treeview.Heading", background=DARK["surface"], foreground=DARK["text"],
                    bordercolor=DARK["border"], font=THEAD, relief="flat")
    style.map("Treeview.Heading", background=[("active", DARK["elevated"])])

    # 滚动条
    style.configure("TScrollbar", background=DARK["surface"], troughcolor=DARK["bg"],
                    bordercolor=DARK["border"], arrowcolor=DARK["text_secondary"],
                    relief="flat")
    style.map("TScrollbar", background=[("active", DARK["elevated"])])

    # 其他 ttk 控件
    style.configure("TLabelframe", background=DARK["bg"], foreground=DARK["text"], font=BOLD)
    style.configure("TLabelframe.Label", background=DARK["bg"], foreground=DARK["text"], font=BOLD)
    style.configure("TCheckbutton", background=DARK["bg"], foreground=DARK["text"], font=BASE)
    style.map("TCheckbutton", background=[("active", DARK["bg"])])
    style.configure("TRadiobutton", background=DARK["bg"], foreground=DARK["text"], font=BASE)
    style.configure("TScale", background=DARK["bg"], troughcolor=DARK["elevated"],
                    bordercolor=DARK["border"])
    style.configure("Horizontal.TScale", background=DARK["bg"],
                    troughcolor=DARK["elevated"], bordercolor=DARK["border"])
    style.configure("TSeparator", background=DARK["border"])


# ---------- 3) 可复用组件工厂（供页面构建统一风格） ----------
def dark_card(parent, **kw):
    """深色卡片容器：面板底 + 1px 边框 + 圆角观感（tk 无圆角，用细边框代替）。"""
    kw.setdefault("bg", DARK["surface"])
    kw.setdefault("bd", 1)
    kw.setdefault("relief", tk.SOLID)
    # 比 DARK["border"] 略亮，让卡片在深色背景上有更清晰的边界定义
    kw.setdefault("highlightbackground", "#2C3648")
    kw.setdefault("highlightthickness", 1)
    return tk.Frame(parent, **kw)


def section_title(parent, text, accent=None, **kw):
    """卡片内小标题：左侧青绿强调竖条 + 文字，统一字体与配色。

    返回外层 Frame（内部排布 [竖条][文字]），可直接 .grid()/.pack()，
    调用方无需改动（原实现返回 Label，外部也只用了 .grid/.pack）。
    """
    accent = accent or DARK["accent"]
    bg = kw.get("bg", DARK["surface"])
    fg = kw.get("fg", DARK["text"])
    font = kw.get("font", ("Microsoft YaHei", 13, "bold"))
    outer = tk.Frame(parent, bg=bg, bd=0, relief=tk.FLAT, highlightthickness=0)
    bar = tk.Frame(outer, width=4, bg=accent, bd=0, relief=tk.FLAT, highlightthickness=0)
    bar.grid(row=0, column=0, sticky="ns", padx=(0, 8))
    lbl = tk.Label(outer, text=text, bg=bg, fg=fg, font=font, anchor="w")
    lbl.grid(row=0, column=1, sticky="w")
    return outer


def primary_button(parent, text, command, **kw):
    """主强调按钮（青绿底深字）。"""
    kw.setdefault("bg", DARK["accent"])
    kw.setdefault("fg", DARK["bg"])
    kw.setdefault("activebackground", DARK["accent_hover"])
    kw.setdefault("activeforeground", DARK["bg"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 14)
    kw.setdefault("pady", 6)
    return tk.Button(parent, text=text, command=command, **kw)


def secondary_button(parent, text, command, **kw):
    """次按钮（深色底浅字，悬停转强调色边框）。"""
    kw.setdefault("bg", DARK["elevated"])
    kw.setdefault("fg", DARK["text"])
    kw.setdefault("activebackground", DARK["border"])
    kw.setdefault("activeforeground", DARK["accent"])
    kw.setdefault("relief", tk.SOLID)
    kw.setdefault("bd", 1)
    kw.setdefault("highlightbackground", DARK["border"])
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("font", ("Microsoft YaHei", 12))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    return tk.Button(parent, text=text, command=command, **kw)


def danger_button(parent, text, command, **kw):
    """危险按钮（红底深字）。"""
    kw.setdefault("bg", DARK["error"])
    kw.setdefault("fg", DARK["bg"])
    kw.setdefault("activebackground", DARK["error_hover"])
    kw.setdefault("activeforeground", DARK["bg"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    return tk.Button(parent, text=text, command=command, **kw)


def success_button(parent, text, command, **kw):
    """推荐/成功按钮（绿底深字，用于「一键修复」「加载」等高确定操作）。"""
    kw.setdefault("bg", DARK["success"])
    kw.setdefault("fg", DARK["bg"])
    kw.setdefault("activebackground", "#56D364")
    kw.setdefault("activeforeground", DARK["bg"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    return tk.Button(parent, text=text, command=command, **kw)


def warning_button(parent, text, command, **kw):
    """警告按钮（橙底深字，用于删除重复等需谨慎但非破坏操作）。"""
    kw.setdefault("bg", DARK["warning"])
    kw.setdefault("fg", DARK["bg"])
    kw.setdefault("activebackground", "#E3B341")
    kw.setdefault("activeforeground", DARK["bg"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    return tk.Button(parent, text=text, command=command, **kw)


def themed_button(parent, text, command, kind="secondary", **kw):
    """按语义类型返回对应工厂按钮：primary/secondary/danger/success/warning。"""
    _map = {
        "primary": primary_button,
        "secondary": secondary_button,
        "danger": danger_button,
        "success": success_button,
        "warning": warning_button,
    }
    return _map.get(kind, secondary_button)(parent, text, command, **kw)
