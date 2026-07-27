#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块
"""
import json
import os
import sys
from pathlib import Path
from logger import default_logger as logger


def _chmod_quiet(p: Path, mode: int) -> None:
    try:
        if hasattr(os, 'chmod'):
            os.chmod(p, mode)
    except OSError:
        # Windows 对某些路径可能拒绝 chmod，静默跳过（ACL 仍有效）
        pass


def _app_data_dir() -> Path:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA')
        if base:
            d = Path(base) / "MolManager"
        else:
            d = Path.home() / ".mol_manager"
    else:
        d = Path.home() / ".mol_manager"
    d.mkdir(parents=True, exist_ok=True)
    # M-3 / CWE-732 修复：仅当前用户可读取/进入该目录，防止同机其他用户嗅探
    _chmod_quiet(d, 0o700)
    return d


APP_DATA_DIR = _app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "mol_manager_config.json"
DEFAULT_CONFIG = {
    "work_dir": "output",
    "mapping_file": "",
    "ext_filter": ".mol,.xyz,.fchk,.out,.inp",
    "window_geometry": "1000x750",
    "psi4_config": {
        "last_method": "b3lyp",
        "last_basis": "6-31g*",
        "last_task": "energy"
    },
    "preview_before_operation": True,
    "recent_work_dirs": []
}
MAX_RECENT_DIRS = 10

def _deep_merge(target: dict, defaults: dict) -> dict:
    """递归合并 defaults 到 target（target 中的键优先），返回 target。"""
    for key, def_val in defaults.items():
        cur = target.get(key)
        if isinstance(def_val, dict):
            if not isinstance(cur, dict):
                target[key] = def_val.copy()
            else:
                _deep_merge(cur, def_val)
        elif cur is None:
            target[key] = def_val
    return target


def load_config():
    try:
        if CONFIG_FILE.exists():
            # 先补一次权限（兼容旧版本创建的 0o644 文件）
            _chmod_quiet(CONFIG_FILE, 0o600)
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if not isinstance(config, dict):
                    logger.warning("配置文件格式不是字典，使用默认配置")
                    return DEFAULT_CONFIG.copy()
                return _deep_merge(config, DEFAULT_CONFIG)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("加载配置文件失败，使用默认配置: %s", e)
    return DEFAULT_CONFIG.copy()

def save_config(config):
    tmp_path: Path | None = None
    try:
        # 写入方式采用「先写临时文件→重命名」+ chmod 0o600，
        # 同时避免 (a) 写一半崩溃导致配置损坏 (b) 创建后未立即 chmod 被其他用户读取
        tmp_path = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        # 先 chmod 再原子替换，防止竞态
        _chmod_quiet(tmp_path, 0o600)
        if hasattr(os, 'replace'):
            os.replace(tmp_path, CONFIG_FILE)
        else:
            tmp_path.rename(CONFIG_FILE)
        _chmod_quiet(CONFIG_FILE, 0o600)
    except OSError as e:
        logger.warning("保存配置文件失败: %s", e)
    finally:
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "APP_DATA_DIR",
    "CONFIG_FILE",
    "DEFAULT_CONFIG",
    "load_config",
    "save_config",
]
