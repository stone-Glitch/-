#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pKa 预测模块 - SMD 热力学循环
"""
import os
import tempfile
import logging
from typing import Any

from utils.logger import default_logger as logger, performance_timer
from .core import run_psi4_task
import chem.openbabel_utils as ob_utils


@performance_timer(name="psi4.run_pka_prediction", level=logging.DEBUG, min_ms=100.0)
def run_pka_prediction(
    ha_file: str,
    a_minus_file: str | None = None,
    method: str = "M06-2X",
    basis: str = "def2-TZVP",
    output_prefix: str | None = None,
    solvent_model: str = "smd",
    solvent_name: str = "water",
    d3: bool = True,
    memory: str = "8 GB",
    Hplus_aq_freeEnergy_kcal: float = -265.9,
    T_K: float = 298.15,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    热力学循环 pKa 预测：
      HA(aq) ⇌ A⁻(aq) + H⁺(aq)
      pKa = (ΔE_sol(HA→A⁻ + H⁺) + ΔG_sol(H⁺,emp)) / (2.303 R T)
    """
    if not os.path.exists(ha_file):
        return {"success": False, "error": f"HA 文件不存在: {ha_file}"}

    result: dict[str, Any] = {"success": False, "error": None}

    def _report(p, m):
        if _progress_callback:
            try:
                _progress_callback(p, m)
            except Exception as _rp:
                logger.debug("_progress_callback 失败: %s", _rp)

    if output_prefix is None:
        parent = os.path.dirname(os.path.abspath(ha_file))
        output_prefix = os.path.join(parent, os.path.splitext(os.path.basename(ha_file))[0] + "_pka")

    _auto_Aminus_tmp: str | None = None
    try:
        # 若没给 A⁻，用 OB -p 12 自动去质子化
        if a_minus_file is None:
            try:
                with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as _tmp_fp:
                    a_guess = _tmp_fp.name
                _auto_Aminus_tmp = a_guess
                ob_utils.protonate_ph(ha_file, a_guess, ph=12.0)
                if os.path.exists(a_guess):
                    a_minus_file = a_guess
                    result["auto_generated_Aminus"] = a_guess
                else:
                    result["error"] = "未提供 A⁻ 文件且 OB -p 12.0 无法自动生成"
                    return result
            except Exception as e:
                result["error"] = f"A⁻ 结构猜测失败：{e}"
                return result

        # 4 个任务：HA gas、A⁻ gas、HA aq、A⁻ aq
        sub: dict[str, tuple[str, str, int, int]] = {
            "HA_gas":   (ha_file,      None,    0, 1),
            "Am_gas":   (a_minus_file, None,   -1, 1),
            "HA_aq":    (ha_file,      solvent_name, 0, 1),
            "Am_aq":    (a_minus_file, solvent_name, -1, 1),
        }
        sub_r: dict[str, dict] = {}

        for i, (key, (fp, sol, ch, mul)) in enumerate(sub.items(), 1):
            _report(int(85 * (i - 1) / 4), f"跑单点 {key}  {method}/{basis}{' '+sol if sol else ''}")
            r = run_psi4_task(
                fp, "energy", method, basis,
                output_dir=os.path.dirname(output_prefix),
                preset_name=None,
                solvent=sol, d3=d3, charge=ch, multiplicity=mul, memory=memory
            )
            if not r.get("success"):
                result["error"] = f"{key} 失败：{r.get('error')}"
                return result
            sub_r[key] = r

        H_to_KCAL = 627.5094740631
        E_HA_g = sub_r["HA_gas"]["energy"] * H_to_KCAL
        E_Am_g = sub_r["Am_gas"]["energy"] * H_to_KCAL
        E_HA_aq = sub_r["HA_aq"]["energy"] * H_to_KCAL
        E_Am_aq = sub_r["Am_aq"]["energy"] * H_to_KCAL

        dG_sol_HA = E_HA_aq - E_HA_g
        dG_sol_Am = E_Am_aq - E_Am_g
        dE_gas = E_Am_g - E_HA_g
        dE_aq_cycle = dE_gas + dG_sol_Am + Hplus_aq_freeEnergy_kcal - dG_sol_HA

        R_cal = 1.987204259e-3
        RT = R_cal * T_K
        pka = dE_aq_cycle / (2.302585093 * RT)

        result.update({
            "energies_kcal_mol": {
                "HA_gas": E_HA_g, "Am_gas": E_Am_g,
                "HA_aq": E_HA_aq, "Am_aq": E_Am_aq,
            },
            "solvation_kcal_mol": {"HA": dG_sol_HA, "A_minus": dG_sol_Am},
            "deltaE_gas_kcal_mol": dE_gas,
            "deltaG_cycle_kcal_mol": dE_aq_cycle,
            "Hplus_aq_empirical_kcal": Hplus_aq_freeEnergy_kcal,
            "T_K": T_K,
            "pKa_estimate": float(pka),
            "note": "经验估计 ±2 左右；更准建议加 explicit waters 或 COSMO-RS",
        })
        result["success"] = True
        _report(100, f"Done: pKa ≈ {pka:.2f}")
        return result

    finally:
        # 清理自动生成的 A⁻ 临时文件
        if _auto_Aminus_tmp and os.path.exists(_auto_Aminus_tmp):
            try:
                os.unlink(_auto_Aminus_tmp)
            except Exception as _del_err:
                logger.debug("清理 pKa A⁻ 临时文件失败 %s: %s", _auto_Aminus_tmp, _del_err)