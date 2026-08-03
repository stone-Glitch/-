#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构象搜索模块 - OB Confab + MMFF94 预优化 + 可选 PSI4 高精度精修
"""
import os
import csv
import random
import tempfile
import logging
from pathlib import Path
from typing import Any

from logger import default_logger as logger, performance_timer
import openbabel_utils as ob_utils
from .core import read_xyz_content, run_psi4_task
from .utils import _parse_xyz, _write_xyz


@performance_timer(name="psi4.conformer_search_ensemble", level=logging.DEBUG, min_ms=100.0)
def conformer_search_ensemble(
    input_file: str,
    output_dir: str | os.PathLike[str] | None = None,
    n_confs_total: int = 80,
    top_n: int = 5,
    psi4_method: str = "b3lyp",
    psi4_basis: str = "6-31g*",
    psi4_preset_name: str | None = None,
    solvent: str | None = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = "4 GB",
    psi4_high_precision: bool = False,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    构象搜索：
      1. OBabel 系统搜索（weighted rotor 搜索 / MMFF94 快速优化）
         → 取 MMFF94 最低能量 top_n
      2. 依次跑 PSI4 optimize（可选）
         → 输出每个构象的最终能量（Hartree）、排序、CSV、PNG 能量棒图
    """
    from pathlib import Path as _Path

    def _report(perc: int, msg: str):
        if _progress_callback:
            try:
                _progress_callback(perc, msg)
            except Exception:
                pass
        logger.debug("[conformer_search] %3d%% %s", perc, msg)

    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "mmff_top": [],
        "psi4_results": [],
        "summary_csv": None,
        "ensemble_energy_png": None,
        "output_dir": None,
    }
    if not ob_utils.PYBEL_AVAILABLE:
        result["error"] = "需要 pybel/OpenBabel Python 包做构象搜索"
        return result

    src = read_xyz_content(input_file) if str(input_file).lower().endswith(".xyz") else None
    if src is None:
        result["error"] = f"无法读取 {input_file}"
        return result

    if output_dir is None:
        output_dir = Path(input_file).parent / f"{Path(input_file).stem}_conformers"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result["output_dir"] = str(out_dir)

    # Step A：OpenBabel Confab 搜索
    _report(5, "OpenBabel Confab 系统构象搜索（MMFF94 优化）")
    confabsdf = str(out_dir / "mmff_conformers.sdf")
    n_confs_total = max(10, int(n_confs_total))
    top_n = max(1, min(top_n, n_confs_total))

    # 尝试 CLI 方式
    from .core import _run_process_with_timeout
    cmd = [
        "obabel", input_file, "-O", confabsdf,
        "--confab", "--confab_options",
        f"--nconf {n_confs_total} --energy 50.0 --rmsd 0.5",
    ]
    rc = _run_process_with_timeout(cmd, cwd=str(out_dir), timeout=300)
    if rc != 0 or not os.path.exists(confabsdf) or os.path.getsize(confabsdf) == 0:
        # Fallback：用 pybel 做 systematic rotor search
        try:
            r = ob_utils._read_molecules(input_file, os.path.splitext(input_file)[1][1:].lower())
            if not r:
                result["error"] = "OpenBabel 未读到任何分子"
                return result
            base = r[0]
            obmol = base.OBMol
            rotor_bonds = []
            try:
                for b in obmol.GetBonds():
                    try:
                        if b.IsRotor():
                            rotor_bonds.append(b)
                    except Exception:
                        continue
            except Exception:
                rotor_bonds = []
            seen = set()
            out_mols = []
            for _ in range(n_confs_total):
                for b in rotor_bonds:
                    ang = random.uniform(0, 360)
                    try:
                        b.SetTorsion(ang)
                    except Exception:
                        continue
                try:
                    ff = ob_utils.ob.OBForceField.FindForceField("MMFF94") or ob_utils.ob.OBForceField.FindForceField("UFF")
                    if ff and ff.Setup(obmol):
                        try:
                            ff.ConjugateGradients(200, 1.0e-4)
                        except Exception:
                            pass
                        try:
                            ff.GetCoordinates(obmol)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    conv = ob_utils.ob.OBConversion()
                    conv.SetOutFormat("smi")
                    smi = conv.WriteString(obmol).strip()
                    key2 = smi
                except Exception:
                    key2 = str(id(obmol))
                if key2 not in seen:
                    seen.add(key2)
                    dup = ob_utils.ob.OBMol()
                    dup.Assign(obmol)
                    out_mols.append(ob_utils.pybel.Molecule(dup))
                if len(out_mols) >= n_confs_total:
                    break
            if out_mols:
                conv = ob_utils.ob.OBConversion()
                conv.SetOutFormat("sdf")
                with open(confabsdf, "wb") as f:
                    for m in out_mols:
                        f.write(conv.WriteString(m.OBMol).encode("utf-8", errors="replace"))
        except Exception as _e_fb:
            result["error"] = f"Confab + Fallback 都失败：{_e_fb}"
            return result

    # Step B：从 SDF 读出每个构象的 MMFF 能量并排序
    if not os.path.exists(confabsdf) or os.path.getsize(confabsdf) == 0:
        result["error"] = "构象搜索没有产生任何构象 SDF"
        return result

    mols_list = ob_utils._read_molecules(confabsdf, "sdf") or []
    if not mols_list:
        result["error"] = "无法读取生成的构象 SDF"
        return result

    def _energy_of(m):
        try:
            return float(m.energy)
        except Exception:
            try:
                txt = m.write("sdf")
                lines = txt.splitlines()
                for idx, line in enumerate(lines):
                    if ">  <Energy>" in line and idx + 1 < len(lines):
                        try:
                            return float(lines[idx + 1].strip())
                        except (ValueError, IndexError):
                            continue
            except Exception:
                pass
        try:
            ff = ob_utils.ob.OBForceField.FindForceField("MMFF94") or ob_utils.ob.OBForceField.FindForceField("UFF")
            if ff and ff.Setup(m.OBMol):
                return float(ff.Energy(False))
        except Exception:
            pass
        return 0.0

    with_e = []
    for mol in mols_list:
        with_e.append((_energy_of(mol), mol))
    with_e.sort(key=lambda x: x[0])
    top_mols = with_e[:top_n]

    mmff_top = []
    for rank, (e, mol) in enumerate(top_mols, 1):
        xyz_path = str(out_dir / f"conf_{rank:02d}_mmff.xyz")
        try:
            mol.write("xyz", xyz_path, overwrite=True)
        except Exception:
            continue
        mmff_top.append({"rank": rank, "energy_kcal_mol": float(e), "xyz": xyz_path})
    result["mmff_top"] = mmff_top

    if not psi4_high_precision:
        # 仅 MMFF
        csv_path = str(out_dir / "summary_mmff.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["rank", "energy_kcal_mol (relative)", "xyz_file"])
            base = mmff_top[0]["energy_kcal_mol"] if mmff_top else 0.0
            for c in mmff_top:
                wr.writerow([c["rank"], f"{c['energy_kcal_mol'] - base:.3f}", c["xyz"]])
        result["summary_csv"] = csv_path
        result["success"] = True
        _report(100, f"Done（仅 MMFF，共 {len(mmff_top)} 构象）")
        return result

    # Step C：PSI4 optimize 每个 Top 构象
    psi4_results = []
    total_c = len(mmff_top)
    for i, c in enumerate(mmff_top, 1):
        _report(10 + int(85 * (i - 1) / max(1, total_c)),
                f"PSI4 optimize 构象 {i}/{total_c}  rank={c['rank']}")
        prefix = str(out_dir / f"conf_{c['rank']:02d}_psi4")
        r = run_psi4_task(
            c["xyz"], "optimize", psi4_method, psi4_basis,
            output_dir=str(out_dir), preset_name=psi4_preset_name,
            solvent=solvent, d3=d3, charge=charge, multiplicity=multiplicity,
            memory=memory, _progress_callback=None
        )
        if r.get("success"):
            psi4_results.append({
                "rank_mmff": c["rank"],
                "energy_h": r.get("energy"),
                "opt_xyz": r.get("optimized_xyz"),
                "fchk": r.get("fchk_file"),
                "props": r.get("properties"),
            })

    # 按 PSI4 能量重排
    psi4_results.sort(key=lambda x: x["energy_h"] if isinstance(x["energy_h"], (int, float)) else 1e30)
    for j, c in enumerate(psi4_results, 1):
        c["rank_psi4"] = j
    result["psi4_results"] = psi4_results

    csv_path = str(out_dir / "summary_psi4.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank_psi4", "rank_mmff", "energy_Hartree", "rel_kcal_mol", "opt_xyz"])
        base = psi4_results[0]["energy_h"] if psi4_results and isinstance(psi4_results[0]["energy_h"], (int, float)) else 0.0
        H_to_KCAL = 627.5094740631
        for c in psi4_results:
            eh = c["energy_h"]
            rel = (eh - base) * H_to_KCAL if isinstance(eh, (int, float)) else float("nan")
            wr.writerow([c["rank_psi4"], c["rank_mmff"], eh, f"{rel:.3f}", c.get("opt_xyz") or ""])
    result["summary_csv"] = csv_path

    # 画能量棒图 PNG
    try:
        png_path = str(out_dir / "ensemble_relative_energy.png")
        xs = [c["rank_psi4"] for c in psi4_results]
        ys_rel = []
        base = psi4_results[0]["energy_h"] if psi4_results and isinstance(psi4_results[0]["energy_h"], (int, float)) else 0.0
        for c in psi4_results:
            eh = c["energy_h"]
            ys_rel.append((eh - base) * H_to_KCAL if isinstance(eh, (int, float)) else float("nan"))

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            try:
                if os.name == "nt":
                    for _cand in ("Microsoft YaHei", "SimHei", "SimSun"):
                        try:
                            plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams.get("font.sans-serif", []))
                            break
                        except Exception:
                            continue
                plt.rcParams["axes.unicode_minus"] = False
            except Exception:
                pass
            fig, ax = plt.subplots(figsize=(8, 4.5))
            bars = ax.bar([str(x) for x in xs], ys_rel, color="#7f7f7f", edgecolor="black")
            for b, y in zip(bars, ys_rel):
                ax.text(b.get_x() + b.get_width()/2, y + max(ys_rel or [0.0])*0.01,
                        f"{y:.1f}", ha="center", va="bottom", fontsize=8)
            ax.set_xlabel("Conformer / 构象 (rank by PSI4)")
            ax.set_ylabel("Relative Energy / 相对能量 (kcal/mol)")
            ax.set_title(f"Conformer Ensemble / 构象系综 (Top-{len(xs)})")
            ax.grid(True, axis="y", alpha=0.3)
            fig.tight_layout()
            fig.savefig(png_path, dpi=130)
            plt.close(fig)
        except Exception:
            try:
                from PIL import Image, ImageDraw
                W, H = 1200, 640
                img = Image.new("RGB", (W, H), "white")
                d = ImageDraw.Draw(img)
                pad_l, pad_r, pad_t, pad_b = 80, 30, 50, 70
                x0, x1 = pad_l, W - pad_r
                y0, y1 = pad_t, H - pad_b
                ymax = max(ys_rel or [1.0]) * 1.15 if ys_rel else 1.0
                if ymax <= 0:
                    ymax = 1.0
                bw = (x1 - x0) / max(1, len(xs)) * 0.6
                bx0 = pad_l + (x1 - x0) / max(1, len(xs)) * 0.2
                d.rectangle([x0, y0, x1, y1], outline="black")
                for i, y in enumerate(ys_rel):
                    L = bx0 + i * (x1 - x0) / max(1, len(xs))
                    R = L + bw
                    T = y1 - (y / ymax) * (y1 - y0)
                    d.rectangle([L, T, R, y1], fill="#9ecae1", outline="black")
                    try:
                        d.text((L + 3, T - 16), f"{y:.1f}", fill="black")
                    except Exception:
                        pass
                d.text((W // 2 - 120, H - 40), "Conformer / 构象 (按 PSI4 排序)", fill="black")
                d.text((10, H // 2 - 40), "Rel. Energy (kcal/mol)", fill="black")
                d.text((W // 2 - 180, 10), f"Conformer Ensemble (Top-{len(xs)})", fill="black")
                img.save(png_path, "PNG")
            except Exception:
                png_path = None
        if png_path and os.path.exists(png_path):
            result["ensemble_energy_png"] = png_path
    except Exception as _e_png:
        logger.debug("画构象能量图失败: %s", _e_png)

    result["success"] = True
    _report(100, f"Done: MMFF top={len(mmff_top)} → PSI4 opt success={len(psi4_results)}")
    return result