#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IRC 模块 - 过渡态 IRC 轨迹生成
"""
import os
import re
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Any

from utils.logger import default_logger as logger, performance_timer
from .core import check_psi4_installed_simple, run_psi4_task, read_xyz_content


@performance_timer(name="psi4.run_irc_task", level=logging.DEBUG, min_ms=100.0)
def run_irc_task(
    ts_file: str,
    direction: str = "both",
    method: str = "b3lyp",
    basis: str = "6-31g*",
    output_prefix: str | None = None,
    preset_name: str | None = None,
    solvent: str | None = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = "4 GB",
    max_points: int = 20,
    step_size: float = 0.15,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    TS IRC：跑 freq → 尝试 IRC driver → 导出轨迹

    注意：如果 PSI4 未编译 IRC driver，会降级为仅返回 TS 结构。
    """
    if not os.path.exists(ts_file):
        return {"success": False, "error": f"TS 文件不存在: {ts_file}"}

    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "forward_xyz_frames": [],
        "backward_xyz_frames": [],
        "combined_trajectory_xyz": None,
    }

    if not check_psi4_installed_simple():
        result["error"] = "PSI4 未安装"
        return result

    tmp_dir = tempfile.mkdtemp(prefix="psi4_irc_")
    try:
        if output_prefix is None:
            output_prefix = os.path.join(
                os.path.dirname(os.path.abspath(ts_file)),
                os.path.splitext(os.path.basename(ts_file))[0] + "_irc"
            )

        def _report(p, m):
            if _progress_callback:
                try:
                    _progress_callback(p, m)
                except Exception:
                    pass

        # Step 1：频率计算
        _report(10, "TS 频率计算 / 预优化（获取 Hessian）")
        r_freq = run_psi4_task(
            ts_file, "frequency", method, basis,
            output_dir=tmp_dir,
            preset_name=preset_name, solvent=solvent, d3=d3,
            charge=charge, multiplicity=multiplicity, memory=memory,
            _progress_callback=None
        )

        if not r_freq.get("success"):
            result["error"] = f"TS freq 失败：{r_freq.get('error')}"
            if r_freq.get("optimized_xyz"):
                result["backward_xyz_frames"].append(r_freq["optimized_xyz"])
                result["forward_xyz_frames"].append(r_freq["optimized_xyz"])
                if r_freq["optimized_xyz"]:
                    traj = output_prefix + "_trajectory.xyz"
                    with open(traj, "w", encoding="utf-8") as f:
                        f.write(r_freq["optimized_xyz"])
                        if not r_freq["optimized_xyz"].endswith("\n\n"):
                            f.write("\n")
                    result["combined_trajectory_xyz"] = traj
                    result["success"] = True
            return result

        # Step 2：尝试 IRC
        wfn_final = None
        try:
            geom_txt = r_freq.get("optimized_xyz") or read_xyz_content(ts_file)
            if geom_txt:
                try:
                    import chem.psi4 as psi4
                    if hasattr(psi4, "geometry") and hasattr(psi4, "irc"):
                        psi4.set_memory(memory)
                        psi4.set_options({
                            "basis": basis,
                            "geom_maxiter": max_points,
                            "irc_step_size": step_size,
                            "irc_points": max_points,
                        })
                        if solvent:
                            try:
                                psi4.set_options({"solvent": solvent})
                            except Exception:
                                pass
                        if d3:
                            try:
                                psi4.set_options({"dft_dispersion": "d3"})
                            except Exception:
                                pass

                        charge_line = f"{charge} {multiplicity}\n"
                        lines = geom_txt.splitlines()
                        if len(lines) >= 2:
                            try:
                                _n = int(lines[0].strip())
                                lines_geom = [lines[0], charge_line.strip()] + lines[2:]
                            except ValueError:
                                lines_geom = lines
                        else:
                            lines_geom = lines

                        mol_obj = psi4.geometry("\n".join(lines_geom) + "\nunits angstrom\nno_reorient\nno_com\n")

                        direction_eff = (direction or "both").lower()
                        for d in (["forward", "backward"] if direction_eff == "both" else [direction_eff]):
                            try:
                                psi4.set_options({"irc_direction": d})
                                e_irc, wfn_irc = psi4.irc(
                                    method, molecule=mol_obj, return_wfn=True,
                                    step_size=step_size, max_points=max_points
                                )
                                if wfn_irc is not None:
                                    wfn_final = wfn_irc
                                try:
                                    m_end = wfn_irc.molecule()
                                    xyz_str = m_end.save_string_xyz()
                                except Exception:
                                    xyz_str = None

                                # 从 log 解析轨迹
                                frames_each = []
                                log_path = None
                                try:
                                    for o_file in r_freq.get("output_files", []):
                                        if str(o_file).endswith(".log"):
                                            log_path = o_file
                                            break
                                except Exception:
                                    pass
                                if log_path and os.path.exists(log_path):
                                    try:
                                        frames_each = _parse_irc_trajectory_from_log(log_path)
                                    except Exception:
                                        frames_each = []
                                if not frames_each and xyz_str:
                                    frames_each = [xyz_str]

                                if d == "forward":
                                    result["forward_xyz_frames"] = frames_each
                                else:
                                    result["backward_xyz_frames"] = frames_each
                            except Exception as e_irc:
                                logger.warning("IRC %s 失败：%s", d, e_irc)
                                if geom_txt:
                                    if d == "forward":
                                        result["forward_xyz_frames"].append(geom_txt)
                                    else:
                                        result["backward_xyz_frames"].append(geom_txt)
                except Exception as e_irc2:
                    logger.warning("IRC driver 无法调用：%s", e_irc2)
        except Exception as e_irc_all:
            logger.warning("IRC 总流程异常：%s", e_irc_all)

        # Step 3：组合 trajectory
        def _add_mid_if_empty(fwd, bwd):
            ts_xyz = r_freq.get("optimized_xyz") or read_xyz_content(ts_file)
            if not fwd and ts_xyz:
                fwd.append(ts_xyz)
            if not bwd and ts_xyz:
                bwd.append(ts_xyz)

        _add_mid_if_empty(result["forward_xyz_frames"], result["backward_xyz_frames"])

        combined = list(reversed(result["backward_xyz_frames"])) + result["forward_xyz_frames"]
        if combined:
            traj = output_prefix + "_trajectory.xyz"
            os.makedirs(os.path.dirname(os.path.abspath(traj)) or ".", exist_ok=True)
            with open(traj, "w", encoding="utf-8") as f:
                for s in combined:
                    f.write(s)
                    if not s.endswith("\n\n"):
                        f.write("\n")
            result["combined_trajectory_xyz"] = traj

        result["freq_task"] = {
            k: r_freq.get(k) for k in ("energy", "frequencies", "log_file", "success") if k in r_freq
        }
        result["success"] = True
        _report(100, "IRC 完成")

    except Exception as e:
        result["error"] = f"IRC 失败：{e}"
        logger.error("IRC 任务异常: %s", e, exc_info=True)
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    return result


def _parse_irc_trajectory_from_log(log_path: str) -> list[str]:
    """尝试从 PSI4 输出 log 中截取多个 XYZ 块"""
    frames = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return []

    pattern = re.compile(r"\n(\d+)\n([^\n]*)\n((?:\s*[A-Za-z][a-z]?(?:\s+[-+]?\d*\.?\d+){3}\s*\n)+)")
    for m in pattern.finditer(txt):
        try:
            n = int(m.group(1))
            body = m.group(3)
            atoms = body.splitlines()
            atoms = [x for x in atoms if x.strip()]
            if len(atoms) < n:
                continue
            block = f"{n}\nIRC frame\n" + "\n".join(atoms[:n]) + "\n"
            frames.append(block)
        except Exception:
            continue

    # 去重
    uniq = []
    for fr in frames:
        if not uniq or fr.splitlines()[2:] != uniq[-1].splitlines()[2:]:
            uniq.append(fr)
    return uniq