"""
.slack-off-police.yaml の読み書きを担当するモジュール。
"""
import os
import yaml

CONFIG_FILENAME = ".slack-off-police.yaml"

DEFAULTS = {
    "progress_method": "git_diff",
    "interval_min": 15,
    "work_theme": "",
    "use_llm": True,
    "warn_first": True,
    "min_lines": 3,
}


def config_path_for(dir_path):
    return os.path.join(dir_path, CONFIG_FILENAME)


def save_config(dir_path, progress_method, interval_min, targets,
                work_theme="", use_llm=True, warn_first=True, min_lines=3):
    """
    設定を対象ディレクトリ直下に保存する。既存ファイルがあれば上書きする。
    """
    config = {
        "dir": os.path.abspath(dir_path),
        "progress_method": progress_method,
        "interval_min": interval_min,
        "targets": targets,
        "work_theme": work_theme,
        "use_llm": use_llm,
        "warn_first": warn_first,
        "min_lines": min_lines,
    }
    path = config_path_for(dir_path)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    return path


def load_config(dir_path):
    """
    設定を読み込む。存在しなければ None を返す。
    古い設定ファイルに無いキーは既定値で埋める。
    """
    path = config_path_for(dir_path)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        return None
    for key, value in DEFAULTS.items():
        config.setdefault(key, value)
    return config
