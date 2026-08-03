#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预设管理器 - 保存/加载用户自定义配置预设
用于反应动画、PSI4计算等模块的参数模板管理
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from logger import default_logger as logger


class PresetManager:
    """管理用户预设的增删改查、导入导出"""

    def __init__(self, app_name: str = "MolManager"):
        self.app_name = app_name
        self.preset_dir = self._get_preset_dir()
        self.preset_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _get_preset_dir(self) -> Path:
        """获取预设存储目录（跨平台）"""
        import sys
        if sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming'))
        else:
            base = Path.home() / '.config'
        return base / self.app_name / 'presets'

    def _load_all(self) -> None:
        """加载所有预设到内存缓存"""
        self._cache.clear()
        for f in self.preset_dir.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                    self._cache[f.stem] = data
            except Exception as e:
                logger.debug("加载预设 %s 失败: %s", f.name, e)

    def list_presets(self) -> List[str]:
        """返回所有预设名称列表"""
        return sorted(self._cache.keys())

    def get_preset(self, name: str) -> Dict[str, Any]:
        """获取指定预设的数据（只读）"""
        return self._cache.get(name, {}).copy()

    def save_preset(self, name: str, data: Dict[str, Any], overwrite: bool = True) -> bool:
        """
        保存预设。若已存在且 overwrite=False 则返回 False。
        自动添加时间戳和版本信息。
        """
        if not overwrite and name in self._cache:
            return False
        # 添加元数据
        to_save = data.copy()
        to_save['_meta'] = {
            'name': name,
            'saved_at': datetime.now().isoformat(),
            'version': '1.0',
        }
        try:
            path = self.preset_dir / f"{name}.json"
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, ensure_ascii=False, indent=2)
            self._cache[name] = to_save
            logger.info("预设 '%s' 已保存", name)
            return True
        except Exception as e:
            logger.error("保存预设 '%s' 失败: %s", name, e)
            return False

    def delete_preset(self, name: str) -> bool:
        """删除预设"""
        try:
            path = self.preset_dir / f"{name}.json"
            if path.exists():
                path.unlink()
            self._cache.pop(name, None)
            logger.info("预设 '%s' 已删除", name)
            return True
        except Exception as e:
            logger.error("删除预设 '%s' 失败: %s", name, e)
            return False

    def export_preset(self, name: str, export_path: str) -> bool:
        """导出预设为 JSON 文件（分享用）"""
        data = self.get_preset(name)
        if not data:
            return False
        try:
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error("导出预设 '%s' 失败: %s", name, e)
            return False

    def import_preset(self, import_path: str, new_name: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """
        从 JSON 文件导入预设。
        若 new_name 未指定，则使用文件名（不含扩展名）作为预设名。
        返回 (预设名, 数据字典)，失败抛出 ValueError。
        """
        try:
            with open(import_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise ValueError(f"读取文件失败: {e}")

        # 如果导入的数据包含 _meta.name，优先使用，否则用文件名
        meta_name = data.get('_meta', {}).get('name')
        if meta_name:
            name = new_name or meta_name
        else:
            name = new_name or Path(import_path).stem

        # 若名称已存在，自动添加后缀
        base_name = name
        counter = 1
        while name in self._cache:
            name = f"{base_name}_{counter}"
            counter += 1

        self.save_preset(name, data, overwrite=False)
        return name, data

    def get_preset_meta(self, name: str) -> Dict[str, Any]:
        """获取预设的元数据（保存时间等）"""
        data = self.get_preset(name)
        return data.get('_meta', {})

    def clear_all(self) -> int:
        """删除所有预设，返回删除数量"""
        count = len(self._cache)
        for name in list(self._cache.keys()):
            self.delete_preset(name)
        return count


# 全局单例（延迟初始化）
_preset_manager: Optional[PresetManager] = None

def get_preset_manager() -> PresetManager:
    global _preset_manager
    if _preset_manager is None:
        _preset_manager = PresetManager()
    return _preset_manager