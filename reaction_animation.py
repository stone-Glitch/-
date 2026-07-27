from __future__ import annotations

import csv
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import logging
from logger import default_logger as logger, performance_timer
from psi4_compute import _lerp_coords as _base_lerp_coords, _parse_xyz, _write_xyz
import openbabel_utils as ob_utils


try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
    Image = ImageDraw = ImageFont = None  # type: ignore


RESOLUTIONS = {
    "sd": (640, 480),
    "hd": (1280, 720),
    "fullhd": (1920, 1080),
}


_FONT_CACHE: dict[int, Any] = {}


def _pick_font_cached(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    if PIL_AVAILABLE:
        for candidate in (
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ):
            try:
                font = ImageFont.truetype(candidate, size)
                break
            except Exception:
                continue
        if font is None:
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
    _FONT_CACHE[size] = font
    return font


_LERP_COORDS_CACHE_MAX = 2048
_LERP_COORDS_CACHE: dict[Any, list[list[float]]] = {}


def _lerp_coords(R, P, t: float):
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


def _cosine_ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 0.5 * (1.0 - math.cos(math.pi * t))


def _expand_timeline(steps: int, mode: str, smooth: bool) -> list[float]:
    if steps < 2:
        steps = 2
    ts = [i / (steps - 1) for i in range(steps)]
    if mode == "bounce":
        ts = ts + ts[-2:0:-1]
    if smooth:
        return [_cosine_ease(t) for t in ts]
    return ts


def _read_energy_csv(path: str | os.PathLike[str]) -> list[float] | None:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return None
            energies: list[float] = []
            key_candidates = [k for k in reader.fieldnames if "energy" in k.lower() or "hartree" in k.lower()]
            key = key_candidates[0] if key_candidates else reader.fieldnames[-1]
            for row in reader:
                try:
                    energies.append(float(row[key]))
                except Exception:
                    pass
        return energies or None
    except Exception as e:
        logger.warning("读取能量 CSV 失败: %s", e)
        return None


def _overlay_caption(png_in: Path, png_out: Path, title: str, sub_title: str,
                     reaction_pos: float, energy: float | None,
                     energy_series: list[float] | None) -> bool:
    if not PIL_AVAILABLE:
        if png_in != png_out:
            shutil.copy2(png_in, png_out)
        return True
    try:
        with Image.open(png_in) as im:
            canvas = im.convert("RGBA")
            W, H = canvas.size
            draw = ImageDraw.Draw(canvas)
            pad = int(max(16, W * 0.02))
            font_title = _pick_font_cached(max(18, int(W * 0.028)))
            font_sub = _pick_font_cached(max(14, int(W * 0.020)))

            banner_h = int(H * 0.10)
            draw.rectangle([(0, 0), (W, banner_h)], fill=(20, 24, 36, 230))
            draw.text((pad, pad // 2), title, fill=(240, 240, 240, 255), font=font_title)
            if sub_title:
                draw.text((pad, pad // 2 + int(H * 0.045)), sub_title,
                          fill=(180, 220, 255, 255), font=font_sub)

            axis_h = int(H * 0.14)
            axis_top = H - axis_h
            draw.rectangle([(0, axis_top), (W, H)], fill=(20, 24, 36, 230))

            x0 = pad
            x1 = W - pad
            cy = axis_top + axis_h // 2
            draw.line([(x0, cy), (x1, cy)], fill=(200, 200, 200, 255), width=2)
            for tick_x in (x0, (x0 + x1) // 2, x1):
                draw.line([(tick_x, cy - 6), (tick_x, cy + 6)], fill=(200, 200, 200, 255), width=2)
            draw.text((x0, cy + 9), "R (反应物)", fill=(250, 150, 150, 255), font=font_sub)
            mid_label = "反应坐标 →"
            bb = draw.textbbox((0, 0), mid_label, font=font_sub)
            draw.text((((x0 + x1) // 2) - (bb[2] - bb[0]) // 2, cy + 9),
                      mid_label, fill=(230, 230, 230, 255), font=font_sub)
            draw.text((x1 - 150, cy + 9), "P (产物)", fill=(150, 250, 170, 255), font=font_sub)

            cursor_x = x0 + int((x1 - x0) * max(0.0, min(1.0, reaction_pos)))
            r = max(8, int(W * 0.012))
            draw.ellipse([(cursor_x - r, cy - r), (cursor_x + r, cy + r)],
                         fill=(255, 90, 90, 255), outline=(255, 255, 255, 255), width=2)

            if energy is not None or energy_series:
                plot_left = int(W * 0.58)
                plot_right = W - pad
                plot_top = axis_top + pad // 3
                plot_bottom = axis_top + axis_h - pad // 3
                draw.rectangle([(plot_left, plot_top), (plot_right, plot_bottom)],
                               outline=(120, 120, 120, 255), width=1)
                series = energy_series if energy_series else ([energy] if energy is not None else [])
                if len(series) >= 2:
                    emin, emax = min(series), max(series)
                    span = max(1e-9, emax - emin)
                    pts = []
                    for i, e in enumerate(series):
                        tx = plot_left + (plot_right - plot_left) * (i / (len(series) - 1))
                        ty = plot_bottom - (plot_bottom - plot_top) * ((e - emin) / span)
                        pts.append((tx, ty))
                    for p0, p1 in zip(pts, pts[1:]):
                        draw.line([p0, p1], fill=(110, 200, 255, 255), width=2)
                    if energy is not None:
                        ty_curr = plot_bottom - (plot_bottom - plot_top) * ((energy - emin) / span)
                        curr_x = plot_left + (plot_right - plot_left) * max(0.0, min(1.0, reaction_pos))
                        r2 = max(4, int(W * 0.006))
                        draw.ellipse([(curr_x - r2, ty_curr - r2), (curr_x + r2, ty_curr + r2)],
                                     fill=(255, 220, 80, 255))
                if energy is not None:
                    draw.text((plot_left + 4, plot_top + 2), f"E = {energy:.6f} Ha",
                              fill=(255, 230, 120, 255), font=font_sub)

            canvas.convert("RGB").save(png_out, format="PNG", optimize=True)
        return True
    except Exception as e:
        logger.warning("叠加字幕失败，回退原图: %s", e)
        try:
            if png_in != png_out:
                shutil.copy2(png_in, png_out)
        except Exception:
            pass
        return False


def _find_energy_for_frame(idx: int, total_frames: int, energies: list[float] | None,
                           one_way_steps: int, mode: str) -> float | None:
    """
    将动画 timeline 第 idx 帧映射到 energies[n]（长度为 n=one_way_steps 的扫描能量数组）。

    bounce 模式 timeline = [0..1] + (1..0) 去掉首尾：
        idx ∈ [0, one_way_steps) → 正向 0→1，取 energies[0..n-1]
        idx ∈ [one_way_steps, 2*one_way_steps-2) → 反向 1→0（不含最后一帧=energies[0]），
            rev = idx - one_way_steps ∈ [0, one_way_steps-3]
            应取 energies[(n-1) - (rev+1)]，即 [n-2, n-3, ..., 1]，
            这样整体 timeline 才是 [0..n-1] + [n-2..1]，首尾 energies[0] 只出现一次。
    """
    if not energies:
        return None
    n = len(energies)
    if n == 0:
        return None
    if mode == "forward":
        j = int((idx / max(1, total_frames - 1)) * (n - 1))
    else:
        if idx < one_way_steps:
            j = int((idx / max(1, one_way_steps - 1)) * (n - 1))
        else:
            rev = idx - one_way_steps
            # rev ∈ [0, one_way_steps - 3]（因为 bounce 去首尾，total = 2*one_way_steps - 2）
            j = (n - 2) - int((rev / max(1, one_way_steps - 3)) * (n - 2)) if one_way_steps >= 3 else 0
    return energies[max(0, min(n - 1, j))]


def _plot_energy_profile(timeline: list[float], energies: list[float] | None,
                         one_way_steps: int, mode: str, out_png: Path | str) -> str | None:
    """
    X4：导出 能量 vs 反应进度 的 PNG 曲线图（"双屏联动"的一半：动画和能量图放在一起）。
    - Hartree → kcal/mol 单位化（1 E_h = 627.509474 kcal/mol）更化学生友好
    - bounce 模式正向段画蓝色，反向段画虚线橙色
    - matplotlib 优先，Pillow 直画兜底
    """
    import os as _os
    out_png = str(out_png)
    if not timeline:
        return None
    total = len(timeline)
    y_vals: list[float] = []
    has_valid_energy = False
    for idx in range(total):
        e = _find_energy_for_frame(idx, total, energies, one_way_steps, mode)
        if e is None:
            y_vals.append(float("nan"))
        else:
            y_vals.append(e * 627.509474)  # Hartree → kcal/mol
            has_valid_energy = True
    if not has_valid_energy:
        return None
    # 归一化 y：相对最小值（化学生想看的是能量差而不是绝对值）
    import math as _math
    finite = [v for v in y_vals if _math.isfinite(v)]
    if not finite:
        return None
    y_min = min(finite)
    y_rel = [(v - y_min) if _math.isfinite(v) else None for v in y_vals]
    xs_s = timeline  # 0..1 (bounce 会回到 0)
    # 正向/反向段索引分界
    fwd_end = min(total, one_way_steps)
    rev_start = fwd_end if mode == "bounce" and fwd_end < total else total

    # ---- A. matplotlib ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            if _os.name == "nt":
                for _cand in ("Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"):
                    try:
                        from matplotlib import font_manager as _fm  # noqa: F401
                        plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams.get("font.sans-serif", []))
                        break
                    except Exception:
                        continue
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
        # Forward: solid blue
        fwd_x = xs_s[:fwd_end]
        fwd_y = [y_rel[i] for i in range(fwd_end)]
        if fwd_x and any(v is not None for v in fwd_y):
            ax.plot(fwd_x, fwd_y, color="#1f77b4", marker="o", markersize=3, linewidth=1.6, label="R→P (forward)")
        # Reverse: dashed orange
        if rev_start < total:
            rev_x = xs_s[rev_start:]
            rev_y = [y_rel[i] for i in range(rev_start, total)]
            if rev_x and any(v is not None for v in rev_y):
                ax.plot(rev_x, rev_y, color="#ff7f0e", marker="s", markersize=3, linewidth=1.6,
                        linestyle="--", label="P→R (reverse, bounce)")
        # 端点高亮
        def _find_first_valid(arr):
            for i, v in enumerate(arr):
                if v is not None:
                    return i, v
            return None
        first = _find_first_valid(y_rel)
        last = None
        for i in range(len(y_rel) - 1, -1, -1):
            if y_rel[i] is not None:
                last = (i, y_rel[i]); break
        if first is not None:
            ax.scatter([xs_s[first[0]]], [first[1]], s=100, c="#2ca02c", zorder=5, label=f"R 反应物 = {first[1]:.2f} kcal/mol")
        if last is not None and last[0] != first[0] if first is not None else True:
            if last is not None:
                ax.scatter([xs_s[last[0]]], [last[1]], s=100, c="#d62728", zorder=5,
                           marker="*", label=f"P 产物 = {last[1]:.2f} kcal/mol")
        ax.set_xlabel("Reaction coordinate s / 反应进度  (0=反应物, 1=产物)")
        ax.set_ylabel("Relative Energy / 相对能量 (kcal/mol)")
        title = f"Energy Profile / 能量曲线  ({len(xs_s)} 帧)"
        if first is not None and last is not None:
            delta = last[1] - first[1]
            title += f"     ΔE(P-R) = {delta:+.2f} kcal/mol"
        ax.set_title(title)
        ax.grid(True, alpha=0.3, linestyle="--")
        if fwd_end != total or rev_start != total:
            ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        fig.savefig(out_png, dpi=130, bbox_inches="tight")
        plt.close(fig)
        if _os.path.exists(out_png):
            return out_png
    except Exception as _e_mp:
        logger.debug("matplotlib 画能量曲线失败，尝试 Pillow: %s", _e_mp)

    # ---- B. Pillow 直画 PNG ----
    try:
        if not PIL_AVAILABLE:
            return None
        W, H = 1400, 700
        img = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(img)
        pad_l, pad_r, pad_t, pad_b = 90, 30, 60, 80
        x0, x1 = pad_l, W - pad_r
        y0, y1 = pad_t, H - pad_b
        y_valid = [v for v in y_rel if v is not None]
        if not y_valid:
            return None
        ymin = min(y_valid) - 1.0
        ymax = max(y_valid) + 1.0
        if ymax - ymin < 1e-6:
            ymax = ymin + 1.0
        def _X(s): return int(x0 + s * (x1 - x0))
        def _Y(v):
            if v is None: return None
            return int(y1 - (v - ymin) / (ymax - ymin) * (y1 - y0))
        # 边框 + 网格
        draw.rectangle([x0, y0, x1, y1], outline="black", width=1)
        for i in range(5):
            pct = i / 4.0
            tx = int(x0 + pct * (x1 - x0))
            draw.line([(tx, y0, tx, y1)], fill="#ddd")
            draw.text((tx - 20, y1 + 10), f"{pct:.2f}", fill="black")
            ty = int(y1 - pct * (y1 - y0))
            vv = ymin + pct * (ymax - ymin)
            draw.line([(x0, ty, x1, ty)], fill="#ddd")
            draw.text((x0 - 80, ty - 8), f"{vv:+.1f}", fill="black")
        # Forward: blue
        pts_f = []
        for i in range(fwd_end):
            x = _X(xs_s[i]); y = _Y(y_rel[i])
            if y is not None: pts_f.append((x, y))
        if len(pts_f) >= 2: draw.line(pts_f, fill="#1f77b4", width=3)
        # Reverse: dashed orange (用短线段模拟)
        if rev_start < total:
            pts_r = [(_X(xs_s[i]), _Y(y_rel[i])) for i in range(rev_start, total) if _Y(y_rel[i]) is not None]
            for a, b in zip(pts_r, pts_r[1:]):
                draw.line([a, b], fill="#ff7f0e", width=3)
        # 端点标注
        first = None
        for i, v in enumerate(y_rel):
            if v is not None: first = (i, v); break
        last = None
        for i in range(len(y_rel) - 1, -1, -1):
            if y_rel[i] is not None: last = (i, y_rel[i]); break
        if first is not None:
            i, v = first
            cx = _X(xs_s[i]); cy = _Y(v)
            draw.ellipse((cx-8, cy-8, cx+8, cy+8), fill="#2ca02c", outline="black")
            draw.text((cx + 12, cy - 20), f"R 反应物 = {v:.2f} kcal/mol", fill="#2ca02c")
        if last is not None and (first is None or last[0] != first[0]):
            i, v = last
            cx = _X(xs_s[i]); cy = _Y(v)
            draw.ellipse((cx-10, cy-10, cx+10, cy+10), fill="#d62728", outline="black")
            draw.text((cx - 200, cy - 26), f"P 产物 = {v:.2f} kcal/mol", fill="#d62728")
        if first is not None and last is not None:
            delta = last[1] - first[1]
            draw.text((W // 2 - 180, 10), f"ΔE(P-R) = {delta:+.2f} kcal/mol   ({len(xs_s)} 帧)", fill="black")
        draw.text((W // 2 - 120, H - 40), "Reaction coordinate s / 反应进度 (0=反应物, 1=产物)", fill="black")
        draw.text((20, H // 2 - 40), "Relative Energy / 相对能量 (kcal/mol)", fill="black")
        img.save(out_png, format="PNG")
        if _os.path.exists(out_png):
            return out_png
    except Exception as _e_pil:
        logger.debug("Pillow 画能量曲线失败：%s", _e_pil)
    return None


def generate_xyz_trajectory(
    reactant_xyz: str | os.PathLike[str],
    product_xyz: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    steps: int = 30,
    mode: str = "bounce",
    smooth: bool = True,
    trajectory_format: str = "xyz",
    energy_csv: str | os.PathLike[str] | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "output": None,
        "n_frames": 0,
        "energies_written": False,
        "error": None,
    }
    try:
        r_p = Path(reactant_xyz)
        p_p = Path(product_xyz)
        if not r_p.exists():
            result["error"] = f"反应物文件不存在: {r_p}"; return result
        if not p_p.exists():
            result["error"] = f"产物文件不存在: {p_p}"; return result
        n_r, atoms_r, R = _parse_xyz(r_p.read_text(encoding="utf-8"))
        n_p, atoms_p, P = _parse_xyz(p_p.read_text(encoding="utf-8"))
        if n_r != n_p or atoms_r != atoms_p:
            result["error"] = f"原子顺序不一致 (R:{n_r} P:{n_p})，请先做分子叠加"; return result
        mode = mode if mode in ("bounce", "forward") else "bounce"
        trajectory_format = trajectory_format if trajectory_format in ("xyz", "sdf") else "xyz"
        timeline = _expand_timeline(int(steps), mode, bool(smooth))
        one_way_steps = max(2, int(steps))
        total = len(timeline)

        energies = _read_energy_csv(energy_csv) if energy_csv else None
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ext = "." + trajectory_format
        if not out.suffix or out.suffix.lower() != ext:
            out = out.with_suffix(ext)

        if trajectory_format == "xyz":
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                for idx, t in enumerate(timeline):
                    if progress_callback and idx % max(1, total // 100 + 1) == 0:
                        progress_callback(10 + 80 * idx / max(1, total),
                                          f"写入 XYZ 帧 {idx+1}/{total}")
                    coords = _lerp_coords(R, P, t)
                    e = _find_energy_for_frame(idx, total, energies, one_way_steps, mode)
                    comment_tokens = []
                    if mode == "forward":
                        rp = t
                    else:
                        rp = t if idx < one_way_steps else (2.0 - t)
                        rp = max(0.0, min(1.0, rp))
                    comment_tokens.append(f"frame={idx+1}/{total}")
                    comment_tokens.append(f"t={rp:.4f}")
                    if e is not None:
                        comment_tokens.append(f"E={e:.8f}")
                        result["energies_written"] = True
                    comment = "  ".join(comment_tokens)
                    f.write(f"{n_r}\n")
                    f.write(f"{comment}\n")
                    for sym, xyz in zip(atoms_r, coords):
                        f.write(f"{sym:<3s} {xyz[0]:15.10f} {xyz[1]:15.10f} {xyz[2]:15.10f}\n")
        else:
            try:
                import openbabel_utils as _obu
            except Exception:
                _obu = None
            with tempfile.TemporaryDirectory(prefix="traj_sdf_") as td:
                tmpdir = Path(td)
                written = 0
                for idx, t in enumerate(timeline):
                    if progress_callback and idx % max(1, total // 100 + 1) == 0:
                        progress_callback(10 + 70 * idx / max(1, total),
                                          f"生成 SDF 中间帧 {idx+1}/{total}")
                    coords = _lerp_coords(R, P, t)
                    e = _find_energy_for_frame(idx, total, energies, one_way_steps, mode)
                    xyz_text = _write_xyz(n_r, atoms_r, coords)
                    xyz_fp = tmpdir / f"f_{idx:05d}.xyz"
                    xyz_fp.write_text(xyz_text, encoding="utf-8")
                    sdf_fp = tmpdir / f"f_{idx:05d}.sdf"
                    if _obu is not None:
                        conv = _obu.convert_file(str(xyz_fp), str(sdf_fp), "sdf")
                        if not (conv and conv.get("success") and sdf_fp.exists()):
                            continue
                        if e is not None:
                            try:
                                body = sdf_fp.read_text(encoding="utf-8")
                                tag = f">  <Energy>\n{e:.8f}\n\n"
                                parts = body.split("$$$$")
                                parts = [p for p in parts if p.strip()]
                                if not parts:
                                    continue
                                new_body = parts[0].rstrip() + "\n" + tag + "$$$$\n"
                                sdf_fp.write_text(new_body, encoding="utf-8")
                                result["energies_written"] = True
                            except Exception:
                                pass
                    else:
                        if not sdf_fp.exists():
                            continue
                    written += 1
                if written == 0:
                    result["error"] = "SDF 输出需要 OpenBabel (pybel / obabel)"; return result
                # written 是成功写入的中间帧数量，glob 后按名字顺序读，实际写出的帧数 = written；
                # 若中间帧有部分失败，需以实际 written 为准，避免 n_frames 虚报。
                sdf_frames: list[str] = []
                for fp in sorted(tmpdir.glob("f_*.sdf")):
                    body = fp.read_text(encoding="utf-8")
                    if not body.endswith("\n"):
                        body += "\n"
                    sdf_frames.append(body)
                if len(sdf_frames) != written:
                    logger.warning("SDF 中间帧 glob 数量与 written 计数不一致: glob=%s written=%s",
                                   len(sdf_frames), written)
                real_total = min(written, len(sdf_frames))
                with open(out, "w", encoding="utf-8", newline="\n") as target:
                    target.write("".join(sdf_frames))

        if progress_callback:
            progress_callback(100, "轨迹文件写入完成")
        result["success"] = out.exists() and out.stat().st_size > 0
        result["output"] = str(out)
        if trajectory_format == "sdf":
            # SDF 可能有部分帧转换失败，报告实际成功的帧数（否则 n_frames 与 $$$$ 分隔符数不一致）
            try:
                _actual_sdf = out.read_text(encoding="utf-8").count("$$$$")
                if _actual_sdf > 0:
                    result["n_frames"] = _actual_sdf
                else:
                    result["n_frames"] = total
            except OSError:
                result["n_frames"] = total
        else:
            result["n_frames"] = total
        return result
    except Exception as e:
        logger.exception("生成轨迹文件异常")
        result["error"] = str(e)
        return result


def _concat_xyz_files(paths: list[str | os.PathLike[str]],
                      translate_spacing: float = 6.0) -> tuple[int, list[str], list[list[float]]]:
    all_atoms: list[str] = []
    all_coords: list[list[float]] = []
    offset = 0.0
    for p in paths:
        fp = Path(p)
        if not fp.exists():
            raise FileNotFoundError(f"文件不存在: {fp}")
        n, atoms, coords = _parse_xyz(fp.read_text(encoding="utf-8"))
        for (sym, xyz) in zip(atoms, coords):
            all_atoms.append(sym)
            all_coords.append([xyz[0] + offset, xyz[1], xyz[2]])
        xs = [c[0] for c in coords]
        span = (max(xs) - min(xs)) if xs else 0.0
        offset += span + float(translate_spacing)
    return len(all_atoms), all_atoms, all_coords


def _auto_reorder_atoms(atoms_R: list[str], coords_R: list[list[float]],
                        atoms_P: list[str], coords_P: list[list[float]]
                        ) -> tuple[list[str], list[list[float]]]:
    from collections import Counter, defaultdict
    if len(atoms_R) != len(atoms_P) or Counter(atoms_R) != Counter(atoms_P):
        raise ValueError(
            f"反应物和产物原子组成不一致（原子守恒失败）：R Counter={Counter(atoms_R)} P Counter={Counter(atoms_P)}"
        )
    n = len(atoms_R)
    idxs_by_elem_R: dict[str, list[int]] = defaultdict(list)
    idxs_by_elem_P: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(atoms_R):
        idxs_by_elem_R[a].append(i)
    for i, a in enumerate(atoms_P):
        idxs_by_elem_P[a].append(i)

    perm: list[int] = [-1] * n
    for elem, r_ids in idxs_by_elem_R.items():
        p_ids = list(idxs_by_elem_P[elem])
        dists: list[tuple[float, int, int]] = []
        for ri in r_ids:
            rxyz = coords_R[ri]
            for pj in p_ids:
                pxyz = coords_P[pj]
                d = sum((rxyz[k] - pxyz[k]) ** 2 for k in range(3))
                dists.append((d, ri, pj))
        dists.sort()
        used_r: set[int] = set()
        used_p: set[int] = set()
        for _d, ri, pj in dists:
            if ri in used_r or pj in used_p:
                continue
            perm[ri] = pj
            used_r.add(ri); used_p.add(pj)
    if -1 in perm or len(set(perm)) != n:
        raise RuntimeError("产物原子顺序重排失败")
    new_atoms = [atoms_P[perm[i]] for i in range(n)]
    new_coords = [list(coords_P[perm[i]]) for i in range(n)]
    return new_atoms, new_coords


def generate_reaction_multispecies(
    reactant_files: list[str | os.PathLike[str]],
    product_files: list[str | os.PathLike[str]],
    output_path: str | os.PathLike[str],
    *,
    steps: int = 30,
    mode: str = "bounce",
    smooth: bool = True,
    trajectory_format: str = "xyz",
    energy_csv: str | os.PathLike[str] | None = None,
    translate_spacing: float = 6.0,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"success": False, "output": None, "n_frames": 0,
                              "energies_written": False, "error": None}
    try:
        if len(reactant_files) == 0 or len(product_files) == 0:
            result["error"] = "请至少提供 1 个反应物和 1 个产物文件"; return result
        if progress_callback:
            progress_callback(2, "拼接反应物分子...")
        _, atoms_R, coords_R = _concat_xyz_files(reactant_files, translate_spacing=translate_spacing)
        if progress_callback:
            progress_callback(8, "拼接产物分子...")
        _, atoms_P, coords_P = _concat_xyz_files(product_files, translate_spacing=translate_spacing)
        if progress_callback:
            progress_callback(14, "自动对齐产物原子顺序（同元素最近邻贪心）...")
        atoms_P_sorted, coords_P_sorted = _auto_reorder_atoms(atoms_R, coords_R, atoms_P, coords_P)
        with tempfile.TemporaryDirectory(prefix="ms_xyz_") as td:
            td_path = Path(td)
            r_combined = td_path / "R_combined.xyz"
            p_combined = td_path / "P_combined.xyz"
            r_combined.write_text(_write_xyz(len(atoms_R), atoms_R, coords_R), encoding="utf-8")
            p_combined.write_text(_write_xyz(len(atoms_P_sorted), atoms_P_sorted, coords_P_sorted), encoding="utf-8")
            sub = generate_xyz_trajectory(
                str(r_combined), str(p_combined), output_path,
                steps=steps, mode=mode, smooth=smooth,
                trajectory_format=trajectory_format, energy_csv=energy_csv,
                progress_callback=progress_callback,
            )
        return sub
    except Exception as e:
        logger.exception("多分子反应轨迹生成异常")
        result["error"] = str(e)
        return result


def _frames_to_gif(frames: list[Path], out_gif: Path, duration_ms: int) -> bool:
    if not PIL_AVAILABLE:
        return False
    images: list[Any] = []
    first_im: Any | None = None
    try:
        for fp in frames:
            try:
                with Image.open(fp) as _im:
                    im = _im.convert("RGB")
                    im.load()
                if first_im is None:
                    first_im = im
                else:
                    images.append(im)
            except Exception as e:
                logger.warning("GIF 读取帧失败 %s: %s", fp, e)
        if first_im is None:
            return False
        out_gif.parent.mkdir(parents=True, exist_ok=True)
        first_im.save(out_gif, format="GIF", save_all=True,
                      append_images=images, duration=max(20, duration_ms),
                      loop=0, optimize=True, disposal=2)
        return True
    except Exception as e:
        logger.warning("合成 GIF 失败: %s", e)
        return False
    finally:
        for _img in images:
            try:
                _img.close()
            except Exception:
                pass
        if first_im is not None:
            try:
                first_im.close()
            except Exception:
                pass


def _resolve_ffmpeg_exe(name_or_path: str) -> str:
    """
    安全解析 ffmpeg 可执行文件的绝对路径。
    防止 CWE-426 / B607：相对名 + PATH 搜索 + Windows CreateProcess 先搜当前目录，
    导致工作目录同名 ffmpeg.exe 被错误执行。
    """
    import shutil as _shutil
    candidate = str(name_or_path).strip() or "ffmpeg"
    if os.sep in candidate or (os.altsep and os.altsep in candidate) or Path(candidate).is_absolute():
        abs_path = Path(candidate).expanduser()
        try:
            abs_path = abs_path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"ffmpeg 路径不存在或不可读: {candidate}") from exc
        if abs_path.is_symlink() or not abs_path.is_file():
            raise RuntimeError(f"ffmpeg 路径必须是真实可执行文件而非链接: {abs_path}")
        return str(abs_path)
    resolved = _shutil.which(candidate)
    if not resolved:
        raise RuntimeError(
            f"未在 PATH 中找到 ffmpeg（当前输入: {candidate!r}），请安装并添加到 PATH，"
            "或在对话框中指定 ffmpeg 绝对路径（已拒绝使用相对名执行，防止工作目录同名恶意可执行劫持）。"
        )
    abs_resolved = Path(resolved)
    try:
        abs_resolved = abs_resolved.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"ffmpeg 解析出的路径不存在: {resolved}") from exc
    if abs_resolved.is_symlink() or not abs_resolved.is_file():
        raise RuntimeError(f"ffmpeg 路径必须是真实文件: {abs_resolved}")
    return str(abs_resolved)


def _frames_to_mp4(frames: list[Path], out_mp4: Path, fps: int, ffmpeg_path: str) -> bool:
    if not frames:
        return False
    try:
        resolved = _resolve_ffmpeg_exe(ffmpeg_path)
        probe = subprocess.run([resolved, "-version"], capture_output=True, text=True, timeout=15)
        if probe.returncode != 0:
            return False
    except Exception as e:
        logger.warning("找不到 ffmpeg: %s", e)
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="anim_concat_") as td:
            td_path = Path(td)
            for i, fp in enumerate(frames):
                shutil.copy2(fp, td_path / f"seq_{i:05d}{fp.suffix.lower()}")
            pattern = str(td_path / "seq_%05d.") + (frames[0].suffix.lower().lstrip(".") or "png")
            out_mp4.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                resolved, "-y",
                "-framerate", str(max(1, int(fps))),
                "-i", pattern,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(out_mp4),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                logger.warning("ffmpeg 失败 stderr: %s", (r.stderr or "")[-800:])
                return False
            return out_mp4.exists() and out_mp4.stat().st_size > 0
    except Exception as e:
        logger.warning("合成 MP4 失败: %s", e)
        return False


@performance_timer(name="ra.generate_reaction_animation", level=logging.DEBUG, min_ms=100.0)
def generate_reaction_animation(
    reactant_xyz: str | os.PathLike[str],
    product_xyz: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    steps: int = 30,
    mode: str = "bounce",
    smooth: bool = True,
    fmt: str = "gif",
    resolution: str = "hd",
    energy_csv: str | os.PathLike[str] | None = None,
    ffmpeg_path: str = "ffmpeg",
    fps: int = 15,
    title_prefix: str = "",
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "output": None,
        "frames_dir": None,
        "n_frames": 0,
        "error": None,
    }
    frames_root: Path | None = None
    try:
        r_p = Path(reactant_xyz)
        p_p = Path(product_xyz)
        if not r_p.exists():
            result["error"] = f"反应物文件不存在: {r_p}"
            return result
        if not p_p.exists():
            result["error"] = f"产物文件不存在: {p_p}"
            return result
        n_r, atoms_r, R = _parse_xyz(r_p.read_text(encoding="utf-8"))
        n_p, atoms_p, P = _parse_xyz(p_p.read_text(encoding="utf-8"))
        if n_r != n_p or atoms_r != atoms_p:
            result["error"] = f"原子顺序不一致 (R:{n_r} P:{n_p})，请先做分子叠加"
            return result

        mode = mode if mode in ("bounce", "forward") else "bounce"
        res_key = resolution if resolution in RESOLUTIONS else "hd"
        width, height = RESOLUTIONS[res_key]
        energies = _read_energy_csv(energy_csv) if energy_csv else None
        timeline = _expand_timeline(int(steps), mode, bool(smooth))
        one_way_steps = max(2, int(steps))
        total = len(timeline)

        out_dir = Path(output_path).parent if fmt != "png_dir" else Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames_root = Path(tempfile.mkdtemp(prefix="reaction_anim_frames_"))
        xyz_dir = frames_root / "xyz"
        raw_dir = frames_root / "raw"
        final_dir = frames_root / "final"
        xyz_dir.mkdir(); raw_dir.mkdir(); final_dir.mkdir()

        final_frames: list[Path] = []
        for idx, t in enumerate(timeline):
            if progress_callback:
                progress_callback(5 + idx / max(1, total) * 85, f"渲染 {idx+1}/{total}")
            coords = _lerp_coords(R, P, t)
            xyz_text = _write_xyz(n_r, atoms_r, coords)
            xyz_fp = xyz_dir / f"frame_{idx:04d}.xyz"
            xyz_fp.write_text(xyz_text, encoding="utf-8")
            raw_fp = raw_dir / f"frame_{idx:04d}.png"
            r = ob_utils.render_png_2d(str(xyz_fp), str(raw_fp), width=width, height=height)
            if not (r and r.get("success") and raw_fp.exists()):
                continue
            final_fp = final_dir / f"frame_{idx:04d}.png"
            title = f"{title_prefix}帧 {idx+1:03d} / {total:03d}".strip()
            sub = "cosine 缓动" if smooth else "线性插值"
            if mode == "bounce":
                if idx < one_way_steps:
                    sub += " · R → P"
                else:
                    sub += " · P → R"
            energy_here = _find_energy_for_frame(idx, total, energies, one_way_steps, mode)
            if mode == "forward":
                reaction_pos = t
            else:
                reaction_pos = t if idx < one_way_steps else (2.0 - t)
                reaction_pos = max(0.0, min(1.0, reaction_pos))
            _overlay_caption(raw_fp, final_fp, title, sub, reaction_pos, energy_here, energies)
            if final_fp.exists():
                final_frames.append(final_fp)

        if not final_frames:
            result["error"] = "未能生成任何有效帧，请确认 OpenBabel 可用"
            return result
        result["frames_dir"] = str(final_dir)
        result["n_frames"] = len(final_frames)

        # ============ X4：统一能量曲线图（双屏联动 PNG 导出 ============
        def _export_energy_profile(out_path_obj):
            try:
                ep_png = _plot_energy_profile(
                    timeline, energies, one_way_steps, mode,
                    Path(out_path_obj).with_name(Path(out_path_obj).stem + "_energy_profile.png"),
                )
                if ep_png:
                    result["energy_profile_png"] = ep_png
                    result["output_files"] = result.get("output_files", []) + [ep_png]
            except Exception as _e_x4:
                logger.debug("X4 能量曲线图导出失败: %s", _e_x4)

        fmt = fmt.lower()
        output = Path(output_path)
        if fmt == "png_dir":
            if output != final_dir:
                try:
                    if output.exists():
                        shutil.rmtree(output)
                    shutil.copytree(final_dir, output)
                except Exception as e:
                    result["error"] = f"复制 PNG 目录失败: {e}"
                    return result
            result["output"] = str(output)
            result["success"] = True
            _export_energy_profile(output)
            return result

        duration_ms = int(round(1000.0 / max(1, int(fps))))
        if fmt == "gif":
            if not output.suffix or output.suffix.lower() != ".gif":
                output = output.with_suffix(".gif")
            ok = _frames_to_gif(final_frames, output, duration_ms)
            if not ok:
                result["error"] = "Pillow 合成 GIF 失败（可能未安装 Pillow），已保留 PNG 帧目录"
                return result
            result["output"] = str(output)
            result["success"] = True
            _export_energy_profile(output)
            return result

        if fmt == "mp4":
            if not output.suffix or output.suffix.lower() != ".mp4":
                output = output.with_suffix(".mp4")
            ok = _frames_to_mp4(final_frames, output, int(fps), ffmpeg_path)
            if not ok:
                ok2 = _frames_to_gif(final_frames, output.with_suffix(".gif"), duration_ms)
                if ok2:
                    result["output"] = str(output.with_suffix(".gif"))
                    result["error"] = "ffmpeg 不可用，已自动降级为 GIF 输出"
                    result["success"] = True
                    _export_energy_profile(output.with_suffix(".gif"))
                    return result
                result["error"] = "ffmpeg 不可用且 GIF 回退失败，已保留 PNG 帧目录"
                return result
            result["output"] = str(output)
            result["success"] = True
            _export_energy_profile(output)
            return result

        result["error"] = f"未知输出格式: {fmt}"
        return result
    except Exception as e:
        logger.exception("生成反应动画异常")
        result["error"] = str(e)
        return result
    finally:
        if frames_root and frames_root.exists():
            try:
                shutil.rmtree(frames_root, ignore_errors=True)
            except Exception as _cleanup_err:
                logger.debug("清理 frames_root 临时目录失败: %s", _cleanup_err)


__all__ = ["generate_reaction_animation", "generate_xyz_trajectory",
           "generate_reaction_multispecies", "RESOLUTIONS", "PIL_AVAILABLE"]
