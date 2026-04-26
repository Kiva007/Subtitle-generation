import configparser
import os
from pathlib import Path


class ConfigManager:
    def __init__(self, config_file: str = "gui_config.ini"):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self.config_dir = Path(__file__).parent
        self.config_path = self.config_dir / config_file
        self._load_config()

    def _load_config(self):
        """加载配置文件，如果不存在则创建默认配置"""
        default_config = {
            'Model': {
                'whisper_model': 'kotoba-tech/kotoba-whisper-v2.1',
                'translation_model': 'hy-mt1.5-1.8b',
                'lm_studio_url': 'http://127.0.0.1:1234/v1',
            },
            'Output': {
                'output_dir': '',
                'original_subtitle': 'True',
                'translated_subtitle': 'True',
                'bilingual_subtitle': 'True',
                'filter_mood_words': 'True',
            },
            'UI': {
                'window_width': '800',
                'window_height': '600',
                'last_input_file': '',
            }
        }

        if self.config_path.exists():
            self.config.read(self.config_path, encoding='utf-8')
        else:
            self._create_default_config(default_config)

    def _create_default_config(self, default_config):
        """创建默认配置文件"""
        for section, settings in default_config.items():
            self.config[section] = settings
        self.save_config()

    def save_config(self):
        """保存配置到文件"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def get(self, section: str, key: str, fallback: str = '') -> str:
        """获取配置值"""
        return self.config.get(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        """获取布尔值配置"""
        try:
            return self.config.getboolean(section, key, fallback=fallback)
        except (configparser.NoSectionError, ValueError):
            return fallback

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        """获取整数值配置"""
        try:
            return self.config.getint(section, key, fallback=fallback)
        except (configparser.NoSectionError, ValueError):
            return fallback

    def set(self, section: str, key: str, value):
        """设置配置值"""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = str(value)
        self.save_config()

    def update_window_size(self, width: int, height: int):
        """更新窗口大小配置"""
        self.set('UI', 'window_width', width)
        self.set('UI', 'window_height', height)

    def update_last_file(self, file_path: str):
        """更新最后选择的文件路径"""
        self.set('UI', 'last_input_file', file_path)

    def update_model_settings(self, whisper_model: str, translation_model: str, lm_url: str):
        """更新模型设置"""
        self.set('Model', 'whisper_model', whisper_model)
        self.set('Model', 'translation_model', translation_model)
        self.set('Model', 'lm_studio_url', lm_url)

    def update_output_settings(self, output_dir: str, original: bool, translated: bool, bilingual: bool, filter_mood: bool):
        """更新输出设置"""
        self.set('Output', 'output_dir', output_dir)
        self.set('Output', 'original_subtitle', str(original))
        self.set('Output', 'translated_subtitle', str(translated))
        self.set('Output', 'bilingual_subtitle', str(bilingual))
        self.set('Output', 'filter_mood_words', str(filter_mood))