#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热化学模块 - 反应能垒图、Eyring 动力学
"""
import csv
import math
import os
import logging
from typing import Any

from utils.logger import default_logger as logger, performance_timer
from .core import run_psi4_task


@performance_timer(name="psi4.eyring_kinetics", level=logging.DEBUG, min_ms=1.0)
def eyring_kinetics(
    delta_G_double_dagger_kcal: float,
    T_K: float = 298.15,
    delta_H_double_dagger_kcal: float | None = None,
    delta_S_double_dagger_cal_molK: float | None = None,
) -> dict[str, Any]:
    """
    Eyring 过渡态理论：
      k_r = k_B T / h * exp(-ΔG‡ / (R T))
      t_{1/2} = ln 2 / k_r
    """
    import math as _m
    k_B = 1.380649e-23
    h_P = 6.62607015e-34
    R_J = 8.314462618

    dG = float(delta_G_double_dagger_kcal)
    T = float(T_K)
    prefactor = (k_B * T) / h_P
    exp_arg = -(dG * 1000.0 * 4.184) / (R_J * T)
    k_r = prefactor * _m.exp(exp_arg)
    ln2 = 0.69314718056
    t12_s = ln2 / k_r if k_r > 0 else float("inf")

    conv = [("s", 1.0), ("min", 60.0), ("hr", 3600.0), ("day", 86400.0), ("yr", 365.25 * 86400.0)]
    t12_pretty = {}
    for name, factor in conv:
        t12_pretty[name] = t12_s / factor

    best_unit = "s"
    best_v = t12_s
    for name, factor in conv:
        if t12_s / factor >= 1.0:
            best_unit = name
            best_v = t12_s / factor

    result = {
        "T_K": T,
        "delta_G_double_dagger_kcal_mol": dG,
        "k_r_s-1": k_r,
        "t_half_s": t12_s,
        "t_half_by_unit": t12_pretty,
        "t_half_pretty": f"{best_v:.3g} {best_unit}",
    }

    if delta_H_double_dagger_kcal is not None and delta_S_double_dagger_cal_molK is None:
        dH = float(delta_H_double_dagger_kcal)
        dS_cal = (dH - dG) * 1000.0 / T
        result["delta_H_double_dagger_kcal_mol"] = dH
        result["derived_delta_S_cal_mol_K"] = dS_cal
    elif delta_H_double_dagger_kcal is not None and delta_S_double_dagger_cal_molK is not None:
        dH = float(delta_H_double_dagger_kcal)
        dS_cal = float(delta_S_double_dagger_cal_molK)
        dG_check = dH - T * (dS_cal / 1000.0)
        result["delta_H_double_dagger_kcal_mol"] = dH
        result["delta_S_double_dagger_cal_mol_K"] = dS_cal
        result["dG_from_HS_kcal_mol"] = dG_check
        result["dG_discrepancy_kcal_mol"] = dG_check - dG

    return result


@performance_timer(name="psi4.run_reaction_energy_profile", level=logging.DEBUG, min_ms=100.0)
def run_reaction_energy_profile(
    reactant_file: str,
    ts_file: str,
    product_file: str,
    method: str = "b3lyp",
    basis: str = "6-31g*",
    output_prefix: str | None = None,
    preset_name: str | None = None,
    solvent: str | None = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = "4 GB",
    include_frequency: bool = True,
    T_K: float = 298.15,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    反应能垒图：R → optimize → freq → G_R
                TS → optimize → freq → G_TS
                P  → optimize → freq → G_P
    """
    if not os.path.exists(reactant_file) or not os.path.exists(ts_file) or not os.path.exists(product_file):
        return {"success": False, "error": "R / TS / P 三个 xyz/mol 文件中至少有一个不存在"}

    def _report(p, m):
        if _progress_callback:
            try:
                _progress_callback(p, m)
            except Exception as _rp:
                logger.debug("_progress_callback 失败: %s", _rp)

    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "energies_E_h": {},
        "energies_G_kcal_mol": {},
        "barriers": {},
        "summary_csv": None,
        "profile_png": None,
    }

    if output_prefix is None:
        parent = os.path.dirname(os.path.abspath(reactant_file))
        output_prefix = os.path.join(parent, "reaction_profile")
    os.makedirs(os.path.dirname(os.path.abspath(output_prefix)) or ".", exist_ok=True)

    tasks = [("R", reactant_file), ("TS", ts_file), ("P", product_file)]
    energies_E = {}
    energies_G = {}
    H_to_KCAL = 627.5094740631

    for idx, (label, fp) in enumerate(tasks, 1):
        _report(int(90 * (idx - 1) / 3), f"{label}: optimize + (freq)")
        prefix = output_prefix + f"_{label}"

        if include_frequency:
            r = run_psi4_task(
                fp, "thermo", method, basis,
                output_dir=os.path.dirname(prefix),
                preset_name=preset_name, solvent=solvent, d3=d3,
                charge=charge, multiplicity=multiplicity, memory=memory
            )
            if not r.get("success"):
                result["error"] = f"{label} thermo 失败：{r.get('error')}"
                return result
            energies_E[label] = float(r.get("energy"))

            gibbs = None
            try:
                import chem.psi4 as psi4
                for key in ("Gibbs Free Energy", "GIBBS FREE ENERGY", "G(T)"):
                    try:
                        v = psi4.core.variable(key)
                        if v is not None:
                            gibbs = float(v)
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            if gibbs is None and isinstance(r.get("thermo"), list):
                try:
                    gibbs = float(r["thermo"][-1])
                except Exception:
                    pass
            energies_G[label] = (gibbs if gibbs is not None else energies_E[label]) * H_to_KCAL
        else:
            r = run_psi4_task(
                fp, "optimize", method, basis,
                output_dir=os.path.dirname(prefix),
                preset_name=preset_name, solvent=solvent, d3=d3,
                charge=charge, multiplicity=multiplicity, memory=memory
            )
            if not r.get("success"):
                result["error"] = f"{label} optimize 失败：{r.get('error')}"
                return result
            energies_E[label] = float(r.get("energy"))
            energies_G[label] = energies_E[label] * H_to_KCAL

    result["energies_E_h"] = energies_E
    base = energies_G.get("R", 0.0)
    rel = {k: v - base for k, v in energies_G.items()}
    result["energies_G_kcal_mol"] = rel

    result["barriers"] = {
        "forward_dG_double_dagger_kcal": (rel.get("TS", 0.0) - rel.get("R", 0.0)),
        "reverse_dG_double_dagger_kcal": (rel.get("TS", 0.0) - rel.get("P", 0.0)),
        "reaction_dG_r_kcal": (rel.get("P", 0.0) - rel.get("R", 0.0)),
    }

    # Eyring
    try:
        dGf = result["barriers"]["forward_dG_double_dagger_kcal"]
        dGr = result["barriers"]["reverse_dG_double_dagger_kcal"]
        result["kinetics_forward"] = eyring_kinetics(dGf, T=T_K)
        result["kinetics_reverse"] = eyring_kinetics(dGr, T=T_K)
    except Exception as e:
        result["kinetics_error"] = str(e)
        logger.debug("Eyring 动力学计算失败: %s", e)

    # 写 CSV
    csv_path = output_prefix + "_profile.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["label", "E_Hartree", "rel_G_kcal_mol", "note"])
        wr.writerow(["R", energies_E.get("R"), rel.get("R", 0.0), "反应物 / Reactants"])
        wr.writerow(["TS", energies_E.get("TS"), rel.get("TS", 0.0), "过渡态 / Transition State"])
        wr.writerow(["P", energies_E.get("P"), rel.get("P", 0.0), "产物 / Products"])
        wr.writerow([])
        wr.writerow(["barrier", "value_kcal_mol"])
        for k, v in result["barriers"].items():
            wr.writerow([k, f"{v:.3f}"])
    result["summary_csv"] = csv_path

    # 画能垒图 PNG
    try:
        png_path = output_prefix + "_profile.png"
        xs_step = [0.0, 0.8, 1.0, 2.0, 2.2, 3.0]
        ys_step = [
            rel.get("R", 0.0), rel.get("R", 0.0),
            rel.get("TS", 0.0), rel.get("TS", 0.0),
            rel.get("P", 0.0), rel.get("P", 0.0)
        ]

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

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(xs_step, ys_step, color="#1f77b4", linewidth=2.4, marker="o", markersize=8)
        ax.fill_between(xs_step, ys_step, min(ys_step) - max(1.0, abs(max(ys_step)-min(ys_step))*0.15),
                        color="#aec7e8", alpha=0.3)

        for xi, yi, lab in zip([0.4, 1.5, 2.6],
                               [rel.get("R", 0.0), rel.get("TS", 0.0), rel.get("P", 0.0)],
                               ["Reactants\n(R)", "Transition State\n(TS)", "Products\n(P)"]):
            ax.text(xi, yi + (max(ys_step)-min(ys_step))*0.03,
                    lab, ha="center", va="bottom", fontsize=10)
            ax.text(xi, yi - (max(ys_step)-min(ys_step))*0.03,
                    f"{yi:.2f} kcal/mol", ha="center", va="top", fontsize=9, color="darkred")

        try:
            dGf = result["barriers"]["forward_dG_double_dagger_kcal"]
            dGr = result["barriers"]["reverse_dG_double_dagger_kcal"]
            dGrn = result["barriers"]["reaction_dG_r_kcal"]
            # 箭头标注可简化，这里只加文字
            ax.text(1.5, min(ys_step) - (max(ys_step)-min(ys_step))*0.05,
                    f"ΔG‡_fwd = {dGf:.2f} kcal/mol    ΔG‡_rev = {dGr:.2f}    ΔG_r = {dGrn:+.2f}",
                    ha="center", fontsize=10, color="#2ca02c", fontweight="bold")
        except Exception:
            pass

        ax.set_xticks([])
        ax.set_ylabel("Gibbs Free Energy / 相对自由能 (kcal/mol)")
        ax.set_title(f"Reaction Energy Profile / 反应能垒图  (T={T_K:.2f} K)")
        yspan = max(ys_step) - min(ys_step)
        ax.set_ylim(min(ys_step) - yspan*0.3, max(ys_step) + yspan*0.3)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(png_path, dpi=150)
        plt.close(fig)
        if os.path.exists(png_path):
            result["profile_png"] = png_path
    except Exception as e_plt:
        logger.debug("画能垒图失败: %s", e_plt)

    result["success"] = True
    _report(100, f"Done: ΔG‡_fwd={result['barriers']['forward_dG_double_dagger_kcal']:.2f} kcal/mol")
    return result