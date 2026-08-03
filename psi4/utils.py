#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 工具函数 - XYZ 解析、坐标插值、IR 绘图、二面角设置等
"""
import csv
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from logger import default_logger as logger
import openbabel_utils as ob_utils


def _parse_xyz(text: str) -> tuple[int, list[str], list[list[float]]]:
    """
    解析 XYZ 文本内容
    返回: (原子数, 原子符号列表, 坐标列表)
    """
    lines = text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ 内容不足 2 行")
    n = int(lines[0].strip())
    atoms: list[str] = []
    coords: list[list[float]] = []
    atoms_append = atoms.append
    coords_append = coords.append
    end = min(2 + n, len(lines))
    for line in lines[2:end]:
        parts = line.split()
        if len(parts) < 4:
            continue
        atoms_append(parts[0])
        coords_append([float(parts[1]), float(parts[2]), float(parts[3])])
    return n, atoms, coords


def _write_xyz(n: int, atoms: list[str], coords: list[list[float]]) -> str:
    """
    生成 XYZ 格式文本
    """
    lines: list[str] = [str(n), ""]
    lines_append = lines.append
    for sym, xyz in zip(atoms, coords):
        x0, x1, x2 = xyz[0], xyz[1], xyz[2]
        lines_append(f"{sym:<3s} {x0:15.10f} {x1:15.10f} {x2:15.10f}")
    return "\n".join(lines) + "\n"


_LERP_COORDS_CACHE_MAX = 2048
_LERP_COORDS_CACHE: dict[Any, list[list[float]]] = {}


def _lerp_coords(R: list[list[float]], P: list[list[float]], t: float) -> list[list[float]]:
    """
    线性插值坐标
    R: 起点坐标列表
    P: 终点坐标列表
    t: 插值参数 (0~1)
    """
    one_minus_t = 1.0 - t
    n = len(R)
    try:
        key = (id(R), id(P), t)
    except TypeError:
        key = None
    if key is not None and key in _LERP_COORDS_CACHE:
        return _LERP_COORDS_CACHE[key]
    result = [[one_minus_t * R[i][0] + t * P[i][0],
               one_minus_t * R[i][1] + t * P[i][1],
               one_minus_t * R[i][2] + t * P[i][2]] for i in range(n)]
    if key is not None:
        if len(_LERP_COORDS_CACHE) >= _LERP_COORDS_CACHE_MAX:
            try:
                k = next(iter(_LERP_COORDS_CACHE))
                del _LERP_COORDS_CACHE[k]
            except StopIteration:
                pass
        _LERP_COORDS_CACHE[key] = result
    return result


def _plot_ir(freqs_cm: list[float], intensities: list[float], out_png: str,
             fwhm: float = 10.0, vmin: float = 400.0, vmax: float = 4000.0, npts: int = 1600) -> bool:
    """
    P3：洛伦兹展宽画一张模拟 IR 光谱 PNG
    有 matplotlib 就用；没有就退化为 Pillow，都没有返回 False
    """
    if not freqs_cm:
        return False
    xs: list[float] = [vmin + (vmax - vmin) * i / max(1, npts - 1) for i in range(npts)]
    ys: list[float] = [0.0 for _ in xs]
    half = fwhm / 2.0
    g = half ** 2
    for v, I in zip(freqs_cm, intensities):
        if v <= 0:
            continue
        iI = I if I > 0 else 1.0
        imin = max(0, int((v - fwhm * 4 - vmin) / (vmax - vmin) * npts))
        imax = min(npts - 1, int((v + fwhm * 4 - vmin) / (vmax - vmin) * npts) + 1)
        for i in range(imin, imax + 1):
            d = xs[i] - v
            ys[i] += iI * g / (d * d + g)
    y_max = max(ys) if ys else 0.0
    if y_max > 0:
        ys = [y / y_max for y in ys]
    ys_abs = [1.0 - y for y in ys]

    # A. matplotlib
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            if os.name == "nt":
                from matplotlib import font_manager as _fm
                for _cand in ("Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"):
                    try:
                        plt.rcParams["font.sans-serif"] = [_cand] + list(
                            plt.rcParams.get("font.sans-serif", []))
                        break
                    except Exception:
                        continue
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
        ax.plot(xs, ys_abs, color="#1f77b4", linewidth=1.2)
        for v, I in zip(freqs_cm, intensities):
            if v <= 0:
                continue
            h = (I if I > 0 else 1.0) / y_max if y_max > 0 else 0.0
            ax.plot([v, v], [1.0, 1.0 - h], color="#d62728", linewidth=0.8, alpha=0.6)
        ax.set_xlim(vmax, vmin)
        ax.set_ylim(-0.05, 1.1)
        ax.set_xlabel("Wavenumber (cm-1) / 波数")
        ax.set_ylabel("Absorbance (norm) / 吸光度")
        ax.set_title("Simulated IR Spectrum / 模拟红外光谱")
        ax.grid(True, alpha=0.3, linestyle="--")
        fig.tight_layout()
        fig.savefig(out_png, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return os.path.exists(out_png)
    except Exception as _e_mpl:
        logger.debug("matplotlib 画 IR 失败，尝试 Pillow: %s", _e_mpl)

    # B. Pillow
    try:
        from PIL import Image as _PIL_Image, ImageDraw as _ImageDraw
        W, H = 1200, 600
        img = _PIL_Image.new("RGB", (W, H), "white")
        draw = _ImageDraw.Draw(img)
        pad_l, pad_r, pad_t, pad_b = 80, 30, 40, 60
        x0, x1 = pad_l, W - pad_r
        y0, y1 = pad_t, H - pad_b

        def _X(v: float) -> int:
            return int(x1 - (v - vmin) / (vmax - vmin) * (x1 - x0))

        def _Y(a: float) -> int:
            return int(y0 + (1.0 - a) * (y1 - y0))

        draw.rectangle([x0, y0, x1, y1], outline="black", width=1)
        for tick_pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            tx = int(x0 + tick_pct * (x1 - x0))
            vv = vmax - tick_pct * (vmax - vmin)
            draw.line([(tx, y0, tx, y1)], fill="#cccccc")
            draw.text((tx - 20, y1 + 8), f"{int(vv)}", fill="black")
        for pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            ty = int(y1 - pct * (y1 - y0))
            draw.line([(x0, ty, x1, ty)], fill="#cccccc")
            draw.text((x0 - 55, ty - 8), f"{1.0 - pct:.2f}", fill="black")
        pts: list[tuple[int, int]] = [(_X(vmin), _Y(1.0))]
        for xv, yv in zip(xs, ys_abs):
            pts.append((_X(xv), _Y(yv)))
        pts.append((_X(vmax), _Y(1.0)))
        draw.polygon(pts, outline="#1f77b4", fill="#e3f2fd")
        for v, I in zip(freqs_cm, intensities):
            if v <= 0:
                continue
            h = (I if I > 0 else 1.0) / y_max if y_max > 0 else 0.0
            xt = _X(v)
            draw.line([(xt, _Y(1.0), xt, _Y(1.0 - h))], fill="#d62728", width=1)
        try:
            draw.text((W // 2 - 90, H - 28), "Wavenumber / 波数 (cm-1)", fill="black")
            draw.text((8, H // 2 - 40), "Absorbance / 吸光度", fill="black")
            draw.text((W // 2 - 140, 10), "Simulated IR Spectrum / 模拟红外光谱", fill="black")
        except Exception:
            pass
        img.save(out_png, format="PNG")
        return os.path.exists(out_png)
    except Exception as _e_fb:
        logger.debug("Pillow 画 IR 也失败: %s", _e_fb)
    return False


def _set_dihedral_and_write(n: int, atoms: list[str], coords: list[list[float]],
                            i: int, j: int, k: int, l: int, angle_deg: float,
                            out_path: str) -> bool:
    """
    用 OpenBabel --tor 对单个分子设置二面角后输出
    参数:
        n: 原子数
        atoms: 原子符号列表
        coords: 坐标列表
        i, j, k, l: 二面角原子索引 (0-based)
        angle_deg: 目标二面角角度 (度)
        out_path: 输出文件路径
    返回: 是否成功
    """
    tmp_in: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xyz", delete=False, encoding='utf-8') as f:
            tmp_in = f.name
            f.write(_write_xyz(n, atoms, coords))
        exe = ob_utils._resolve_obabel_cli()
        import subprocess as _sp
        import sys as _sys
        if _sys.platform == "win32":
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            kw = {'startupinfo': si, 'creationflags': _sp.CREATE_NO_WINDOW}
        else:
            kw = {}
        cmd = [exe, tmp_in, "-O", out_path,
               "--tor", f"{i+1},{j+1},{k+1},{l+1},{angle_deg:.4f}"]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=120, **kw)
        return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logger.debug("设置二面角失败: %s", e)
        return False
    finally:
        if tmp_in and os.path.exists(tmp_in):
            try:
                os.unlink(tmp_in)
            except OSError:
                pass