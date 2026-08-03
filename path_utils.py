#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路径工具模块 - 集中管理所有路径安全、目录解析、跨平台兼容等公共函数

重构说明：
  本模块从以下文件中提取了重复代码：
  - config.py / logger.py: _app_data_dir(), _chmod_quiet()
  - main.py / model.py: _is_windows_junction()
  - model.py: enforce_no_symlink_target(), resolve_secure_output_path_external()
  - psi4_compute.py / reaction_animation.py: _secure_output_path(), _default_base_dir_from_input()

所有函数保持原有行为不变，仅做命名空间统一。
"""
import os
import sys
import stat
import tempfile
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, os.PathLike]


# ==================== 目录权限 ====================

def chmod_quiet(p: Path, mode: int) -> None:
    """静默设置文件/目录权限，失败不报错（Windows 下某些路径会拒绝）。"""
    try:
        if hasattr(os, 'chmod'):
            os.chmod(p, mode)
    except OSError:
        # Windows 对某些路径可能拒绝 chmod，静默跳过（ACL 仍有效）
        pass


# ==================== 应用数据目录 ====================

def get_app_data_dir() -> Path:
    """
    获取应用数据目录（跨平台）。
    - Windows: %APPDATA%/MolManager 或 ~/.mol_manager
    - macOS/Linux: ~/.mol_manager
    目录不存在时自动创建，并设置为仅当前用户可访问（0o700）。
    """
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA')
        if base:
            d = Path(base) / "MolManager"
        else:
            d = Path.home() / ".mol_manager"
    else:
        d = Path.home() / ".mol_manager"
    d.mkdir(parents=True, exist_ok=True)
    # CWE-732 修复：仅当前用户可读取/进入该目录，防止同机其他用户嗅探
    chmod_quiet(d, 0o700)
    return d


# ==================== Windows Junction 检测 ====================

def is_windows_junction(path: PathLike, *, raise_on_junction: bool = False) -> bool:
    """
    检测路径是否为 Windows NTFS Junction / ReparsePoint。
    非 Windows 平台直接返回 False。

    参数:
        path: 要检测的路径
        raise_on_junction: 为 True 时，检测到 junction 会抛出 ValueError 而非返回 True

    返回:
        True = 是 junction / reparse point；False = 不是或非 Windows 平台
    """
    if os.name != "nt":
        return False
    try:
        p = Path(path)
        if not p.exists():
            return False
        try:
            st = os.lstat(p)
        except OSError:
            return False
        FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
        if ((st.st_mode & stat.S_IFMT) == stat.S_IFDIR
                and (st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)):
            if raise_on_junction:
                raise ValueError(
                    f"检测到 Windows Junction / ReparsePoint 目录，拒绝跟随操作: {os.fspath(p)!r}"
                )
            return True
    except ValueError:
        raise
    except Exception:
        return False
    return False


# ==================== 符号链接 / Junction 安全检查 ====================

def enforce_no_symlink_target(
    path: PathLike,
    *,
    allow_nonexistent: bool = True,
    _level: str = "leaf",
) -> None:
    """
    安全检查：确保路径不是符号链接或 Windows Junction。
    路径不存在时，若 allow_nonexistent=True 则静默通过。

    抛出:
        ValueError: 检测到 symlink / junction 时
    """
    p = Path(path)
    if not p.exists() and allow_nonexistent:
        return
    try:
        if p.is_symlink():
            raise ValueError(f"检测到符号链接（symlink），拒绝操作: {os.fspath(p)!r}")
    except OSError as exc:
        raise ValueError(f"无法判定是否为符号链接: {os.fspath(p)!r} ({exc})") from exc
    if os.name == "nt":
        is_windows_junction(p, raise_on_junction=True)


# ==================== 安全输出路径解析 ====================

def resolve_secure_output_path(
    requested_path,
    *,
    base_dir,
    is_dir: bool = False,
    default_name=None,
    allow_outside: bool = False,
    create_parent: bool = False,
) -> Path:
    """
    安全解析输出路径（路径遍历防护 + symlink/junction 检测）。

    安全特性:
      - 禁止路径中包含 '..' 段
      - 默认限制输出在 base_dir 范围内（allow_outside=False）
      - 路径链上的每一级都检查 symlink / junction
      - 解析后真实路径仍需在 base_dir 内（防 symlink 穿透）

    参数:
        requested_path: 用户请求的输出路径（相对或绝对）
        base_dir: 允许的根目录（必须已存在）
        is_dir: 输出目标是否为目录（影响父目录创建逻辑）
        default_name: requested_path 为空时使用的默认名称
        allow_outside: 是否允许输出到 base_dir 之外
        create_parent: 是否自动创建父目录

    返回:
        规范化后的 Path 对象

    抛出:
        ValueError: 路径非法 / 越界 / 含 symlink 等
    """
    if not base_dir:
        raise ValueError("base_dir 不能为空")
    base_p = Path(base_dir)
    if not base_p.is_dir():
        raise ValueError(f"base_dir 必须是已存在的目录: {os.fspath(base_p)!r}")
    base_real = base_p.resolve(strict=True)

    # --- 规范化输入路径 ---
    raw = ""
    if requested_path is None:
        raw = ""
    elif isinstance(requested_path, bytes):
        raw = requested_path.decode("utf-8", "replace")
    else:
        raw = os.fspath(requested_path)
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw and default_name:
        raw = str(default_name)
    if not raw:
        raise ValueError("输出路径为空且未提供 default_name")

    # --- 禁止 '..' 段 ---
    raw_slashed = raw.replace("\\", "/")
    raw_segs = [s for s in raw_slashed.split("/") if s != ""]
    if any(seg == ".." for seg in raw_segs):
        raise ValueError(f"输出路径禁止包含 '..' 段: {raw!r}")

    # --- 拼出绝对路径 ---
    p = Path(raw)
    if not p.is_absolute():
        p = base_real / p

    # --- commonpath 范围检查 ---
    norm_abs = os.path.normpath(os.fspath(p))
    base_norm = os.path.normpath(os.fspath(base_real))
    if not allow_outside:
        try:
            common = os.path.commonpath([base_norm, norm_abs])
            if os.path.normcase(common) != os.path.normcase(base_norm):
                raise ValueError(
                    f"输出路径越出允许范围（commonpath 判定）：请求 {norm_abs!r}，允许根 {base_norm!r}"
                )
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"输出路径规范化失败: {raw!r}") from exc
    cand = Path(norm_abs)

    # --- 路径链 symlink/junction 逐段检查 ---
    def _walk_chain(target: Path, base: Path) -> None:
        try:
            rel = target.resolve(strict=False).relative_to(base.resolve(strict=False))
            parts_a = list(rel.parts)
        except (OSError, ValueError):
            parts_a = list(target.parts)
        cur = base
        for part in parts_a:
            cur = cur / part
            if not cur.exists():
                continue
            enforce_no_symlink_target(cur, allow_nonexistent=True, _level="chain")
        if target.exists():
            enforce_no_symlink_target(target, allow_nonexistent=True, _level="leaf")

    try:
        _walk_chain(cand, base_real)
    except ValueError as exc:
        raise ValueError(f"输出路径链中存在符号链接 / Junction，拒绝写入: {raw!r} ({exc})") from exc

    # --- 解析后真实路径范围检查（防 symlink 穿透）---
    if not allow_outside:
        try:
            if cand.exists() or cand.parent.exists():
                resolved = cand.resolve(strict=False)
            else:
                resolved = cand
            resolved.relative_to(base_real)
        except (OSError, ValueError) as exc:
            raise ValueError(f"解析后真实路径超出允许范围（含 symlink 穿透）: {raw!r}") from exc

    # --- 自动创建父目录 ---
    if create_parent:
        parent = cand if is_dir else cand.parent
        try:
            if not allow_outside:
                _ = Path(os.path.normpath(os.fspath(parent))).relative_to(
                    Path(os.path.normpath(os.fspath(base_real))))
            parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法为输出路径创建父目录: {os.fspath(cand)!r} ({exc})") from exc

    return cand


# ==================== 默认 base_dir 推断 ====================

def default_base_dir_from_input(
    *inputs: Optional[PathLike],
    fallback: Optional[PathLike] = None,
) -> Path:
    """
    从输入文件/目录推断默认的 base_dir。
    优先级：第一个存在的输入的父目录 → fallback → cwd → tempdir。

    目的：避免用户随便输相对路径时跑到 cwd 下。
    """
    for inp in inputs:
        if inp is None:
            continue
        try:
            p = Path(inp)
            if p.is_dir():
                return p.resolve()
            if p.parent.is_dir():
                return p.parent.resolve()
        except Exception:
            continue
    if fallback is not None:
        try:
            pf = Path(fallback)
            if pf.is_dir():
                return pf.resolve()
            if pf.parent.is_dir():
                return pf.parent.resolve()
        except Exception:
            pass
    try:
        cwd = Path.cwd()
        if cwd.is_dir():
            return cwd.resolve()
    except Exception:
        pass
    return Path(tempfile.gettempdir()).resolve()


# ==================== 便捷封装 ====================

def secure_output_path(
    requested_path,
    *,
    is_dir: bool = False,
    default_name=None,
    base_dir=None,
    allow_outside: bool = False,
    create_parent: bool = True,
) -> Path:
    """
    resolve_secure_output_path 的便捷封装：
    - base_dir 为 None 时自动推断（cwd → tempdir）
    - 默认 create_parent=True（更常用）
    """
    if base_dir is None:
        try:
            cwd = Path.cwd()
            if cwd.is_dir():
                base_dir = cwd
            else:
                raise RuntimeError
        except Exception:
            base_dir = Path(tempfile.gettempdir())
    return resolve_secure_output_path(
        requested_path,
        base_dir=base_dir,
        is_dir=is_dir,
        default_name=default_name,
        allow_outside=allow_outside,
        create_parent=create_parent,
    )
