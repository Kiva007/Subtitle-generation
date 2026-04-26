import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox,
    QProgressBar, QPlainTextEdit, QFileDialog, QGroupBox,
    QMessageBox, QStatusBar, QSpinBox
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, pyqtSignal
from PyQt6.QtGui import QFont

from gui_config import ConfigManager


class SubtitleGeneratorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = ConfigManager()
        self.worker = None
        self.available_models = []
        self.process = None
        self.saved_translation_model = self.config.default_translation_model  # 使用配置文件中的默认翻译模型

        self.init_ui()
        self.load_settings()
        self.refresh_model_list()

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("Subtitle Generator v1.0")
        self.setMinimumSize(800, 700)
        self.resize(800, 700)

        # 主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 主布局
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)

        # 添加各个组件
        main_layout.addWidget(self._create_file_selection_group())
        main_layout.addWidget(self._create_model_config_group())
        main_layout.addWidget(self._create_output_format_group())
        main_layout.addWidget(self._create_control_buttons())
        main_layout.addWidget(self._create_progress_section())
        main_layout.addWidget(self._create_log_section())

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def _create_file_selection_group(self):
        """创建文件选择区域"""
        group = QGroupBox("文件选择")
        layout = QVBoxLayout()

        # 文件选择按钮和路径显示
        file_layout = QHBoxLayout()
        self.file_button = QPushButton("选择视频/音频文件")
        self.file_button.clicked.connect(self.select_file)
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)

        file_layout.addWidget(self.file_button)
        file_layout.addWidget(self.file_path_edit)
        layout.addLayout(file_layout)

        # 输出目录选择
        output_layout = QHBoxLayout()
        output_label = QLabel("输出目录:")
        self.output_button = QPushButton("选择输出目录")
        self.output_button.clicked.connect(self.select_output_dir)
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setPlaceholderText("默认为输入文件所在目录")

        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_button)
        output_layout.addWidget(self.output_path_edit)
        layout.addLayout(output_layout)

        group.setLayout(layout)
        return group

    def _create_model_config_group(self):
        """创建模型配置区域"""
        group = QGroupBox("模型配置")
        layout = QVBoxLayout()

        # Whisper模型
        whisper_layout = QHBoxLayout()
        whisper_label = QLabel("Whisper模型:")
        self.whisper_model_edit = QLineEdit("kotoba-tech/kotoba-whisper-v2.1")
        whisper_layout.addWidget(whisper_label)
        whisper_layout.addWidget(self.whisper_model_edit)
        layout.addLayout(whisper_layout)

        # 翻译模型选择
        trans_layout = QHBoxLayout()
        trans_label = QLabel("翻译模型:")
        self.trans_model_combo = QComboBox()
        self.trans_model_combo.setMinimumWidth(300)
        self.refresh_models_button = QPushButton("刷新模型列表")
        self.refresh_models_button.clicked.connect(self.refresh_model_list)

        trans_layout.addWidget(trans_label)
        trans_layout.addWidget(self.trans_model_combo)
        trans_layout.addWidget(self.refresh_models_button)
        layout.addLayout(trans_layout)

        # LM Studio URL
        url_layout = QHBoxLayout()
        url_label = QLabel("LM Studio:")
        self.lm_url_edit = QLineEdit("http://127.0.0.1:1234/v1")
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.lm_url_edit)
        layout.addLayout(url_layout)

        # 批量翻译大小
        batch_layout = QHBoxLayout()
        batch_label = QLabel("批量翻译大小:")
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setMinimum(7)
        self.batch_size_spin.setMaximum(150)
        self.batch_size_spin.setValue(70)
        self.batch_size_spin.setToolTip("一次翻译的字幕条数，更大的值提供更好的上下文但可能影响速度")
        batch_layout.addWidget(batch_label)
        batch_layout.addWidget(self.batch_size_spin)
        layout.addLayout(batch_layout)

        group.setLayout(layout)
        return group

    def _create_output_format_group(self):
        """创建输出格式选择区域"""
        group = QGroupBox("输出格式")
        layout = QVBoxLayout()

        # 字幕格式选择
        format_layout = QHBoxLayout()
        self.original_checkbox = QCheckBox("原文字幕 (日语)")
        self.translated_checkbox = QCheckBox("中文字幕")
        self.bilingual_checkbox = QCheckBox("双语字幕")

        # 默认选中
        self.original_checkbox.setChecked(True)
        self.translated_checkbox.setChecked(True)
        self.bilingual_checkbox.setChecked(True)

        format_layout.addWidget(self.original_checkbox)
        format_layout.addWidget(self.translated_checkbox)
        format_layout.addWidget(self.bilingual_checkbox)
        layout.addLayout(format_layout)

        # 语气词过滤选项
        filter_layout = QHBoxLayout()
        self.filter_mood_checkbox = QCheckBox("过滤无意义语气词")
        self.filter_mood_checkbox.setChecked(True)
        self.filter_mood_checkbox.setToolTip("自动去除如'啊、哦、嗯'等无意义的语气词，保持字幕简洁")
        filter_layout.addWidget(self.filter_mood_checkbox)
        layout.addLayout(filter_layout)

        group.setLayout(layout)
        return group

    def _create_control_buttons(self):
        """创建控制按钮区域"""
        group = QGroupBox("操作")
        layout = QHBoxLayout()

        self.start_button = QPushButton("开始处理 🚀")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.start_processing)

        self.stop_button = QPushButton("停止 ⏹️")
        self.stop_button.setMinimumHeight(40)
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setEnabled(False)

        self.reset_button = QPushButton("重置 🔄")
        self.reset_button.setMinimumHeight(40)
        self.reset_button.clicked.connect(self.reset_ui)

        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.reset_button)

        group.setLayout(layout)
        return group

    def _create_progress_section(self):
        """创建进度显示区域"""
        group = QGroupBox("进度")
        layout = QVBoxLayout()

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # 状态标签
        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

        group.setLayout(layout)
        return group

    def _create_log_section(self):
        """创建日志显示区域"""
        group = QGroupBox("处理日志")
        layout = QVBoxLayout()

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        self.log_text.setFont(QFont("Courier New", 9))

        layout.addWidget(self.log_text)
        group.setLayout(layout)
        return group

    def select_file(self):
        """选择输入文件"""
        file_filter = "媒体文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.wav *.mp3 *.m4a *.flac);;视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv);;音频文件 (*.wav *.mp3 *.m4a *.flac);;所有文件 (*.*)"
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频/音频文件", "", file_filter)

        if file_path:
            self.file_path_edit.setText(file_path)
            self.config.update_last_file(file_path)

            # 如果输出目录为空，设置为输入文件所在目录
            if not self.output_path_edit.text():
                output_dir = str(Path(file_path).parent)
                self.output_path_edit.setText(output_dir)

    def select_output_dir(self):
        """选择输出目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_path:
            self.output_path_edit.setText(dir_path)

    def refresh_model_list(self):
        """从LM Studio获取模型列表"""
        self.status_bar.showMessage("正在获取模型列表...")
        self.log_message("连接LM Studio获取模型列表...")

        try:
            lm_url = self.lm_url_edit.text().rstrip('/v1')
            response = requests.get(f"{lm_url}/api/v1/models", timeout=5)

            if response.status_code == 200:
                data = response.json()
                # LM Studio API返回格式: { "models": [...] }
                models_list = data.get('models', [])
                self.available_models = [model.get('key', model.get('id', str(model))) for model in models_list if model.get('type') == 'llm']

                self.trans_model_combo.clear()
                if self.available_models:
                    self.trans_model_combo.addItems(self.available_models)

                    # 尝试选择之前保存的翻译模型
                    if hasattr(self, 'saved_translation_model') and self.saved_translation_model in self.available_models:
                        index = self.available_models.index(self.saved_translation_model)
                        self.trans_model_combo.setCurrentIndex(index)
                        self.log_message(f"获取到 {len(self.available_models)} 个翻译模型，选择: {self.saved_translation_model}")
                        self.status_bar.showMessage(f"成功获取 {len(self.available_models)} 个模型，当前选择: {self.saved_translation_model}")
                    else:
                        # 如果保存的模型不在列表中，使用默认选择第一个
                        if hasattr(self, 'saved_translation_model'):
                            self.log_message(f"获取到 {len(self.available_models)} 个翻译模型，保存的模型 {self.saved_translation_model} 不在列表中，使用第一个模型")
                        else:
                            self.log_message(f"获取到 {len(self.available_models)} 个翻译模型")
                        self.status_bar.showMessage(f"成功获取 {len(self.available_models)} 个模型")
                else:
                    self.trans_model_combo.addItem(self.config.default_translation_model)
                    self.log_message("未获取到模型列表，使用默认值")
                    self.status_bar.showMessage("使用默认模型")
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

        except Exception as e:
            self.log_message(f"获取模型列表失败: {e}")
            self.trans_model_combo.clear()
            self.trans_model_combo.addItem(self.config.default_translation_model)
            self.status_bar.showMessage("连接失败，使用默认模型")

    def start_processing(self):
        """开始处理任务"""
        # 验证输入
        input_file = self.file_path_edit.text()
        if not input_file:
            QMessageBox.warning(self, "警告", "请先选择输入文件！")
            return

        if not os.path.exists(input_file):
            QMessageBox.warning(self, "警告", "选择的文件不存在！")
            return

        # 验证输出格式选择
        if not any([self.original_checkbox.isChecked(),
                   self.translated_checkbox.isChecked(),
                   self.bilingual_checkbox.isChecked()]):
            QMessageBox.warning(self, "警告", "请至少选择一种输出格式！")
            return

        # 确定输出目录
        output_dir = self.output_path_edit.text() or str(Path(input_file).parent)
        os.makedirs(output_dir, exist_ok=True)

        # 准备参数
        params = {
            'input_file': input_file,
            'output_dir': output_dir,
            'whisper_model': self.whisper_model_edit.text(),
            'translation_model': self.trans_model_combo.currentText(),
            'lm_studio_url': self.lm_url_edit.text(),
            'batch_size': self.batch_size_spin.value(),
            'filter_mood_words': self.filter_mood_checkbox.isChecked(),
            'output_formats': {
                'original': self.original_checkbox.isChecked(),
                'translated': self.translated_checkbox.isChecked(),
                'bilingual': self.bilingual_checkbox.isChecked()
            }
        }

        # 保存配置
        self.config.update_model_settings(
            self.whisper_model_edit.text(),
            self.trans_model_combo.currentText(),
            self.lm_url_edit.text()
        )
        self.config.update_output_settings(
            output_dir,
            self.original_checkbox.isChecked(),
            self.translated_checkbox.isChecked(),
            self.bilingual_checkbox.isChecked(),
            self.filter_mood_checkbox.isChecked()
        )

        # 创建并启动子进程处理
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.handle_process_output)
        self.process.readyReadStandardError.connect(self.handle_process_error)
        self.process.finished.connect(self.on_process_finished)

        # 准备参数
        import json
        params_json = json.dumps(params, ensure_ascii=False)

        # 启动子进程
        script_path = Path(__file__).parent / "subprocess_processor.py"

        # 设置UTF-8编码环境变量
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        env.insert('PYTHONUNBUFFERED', '1')  # 禁用输出缓冲
        self.process.setProcessEnvironment(env)

        # 启动进程
        self.process.start(sys.executable, [str(script_path), "--params", params_json])

        # 更新UI状态
        self.set_processing_state(True)
        self.log_message("开始处理任务...")

    def stop_processing(self):
        """停止当前任务"""
        if hasattr(self, 'process') and self.process.state() == QProcess.ProcessState.Running:
            self.process.kill()
            self.log_message("正在停止任务...")
            self.stop_button.setEnabled(False)

    def handle_process_output(self):
        """处理子进程的标准输出"""
        data = self.process.readAllStandardOutput()

        try:
            # 尝试UTF-8解码，如果失败则尝试其他编码
            output = bytes(data).decode('utf-8')
        except UnicodeDecodeError:
            try:
                # 如果UTF-8失败，尝试系统默认编码
                output = bytes(data).decode('gbk', errors='ignore')
            except Exception:
                # 如果都失败，使用错误忽略模式
                output = bytes(data).decode('utf-8', errors='ignore')

        lines = output.strip().split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 解析进度信息
            import re
            # 匹配翻译阶段进度：[翻译] [1/587]
            translate_match = re.search(r'\[翻译\]\s*\[(\d+)/(\d+)\]', line)
            if translate_match:
                current = int(translate_match.group(1))
                total = int(translate_match.group(2))
                # 翻译阶段占总进度的60%（20%-80%）
                progress = 20 + int((current / total) * 60)
                self.update_progress(progress, f"翻译中... [{current}/{total}]")
                continue

            # 匹配输出阶段进度
            if '[输出]' in line or '生成字幕文件' in line:
                self.update_progress(80, "生成字幕文件...")
                continue

            # 匹配转录阶段进度：[转录] 格式
            if '[转录]' in line or '转录' in line:
                self.update_progress(min(15, 15), "正在转录...")
                continue

            # 匹配旧格式的进度信息：[1/587] 格式（兼容性处理）
            transcribe_match = re.search(r'(?<!\[翻译\]|\[转录\])\[(\d+)/(\d+)\]', line)
            if transcribe_match:
                current = int(transcribe_match.group(1))
                total = int(transcribe_match.group(2))
                progress = int((current / total) * 15)  # 15% for transcription
                self.update_progress(progress, f"处理中... [{current}/{total}]")

            self.log_message(line)

    def handle_process_error(self):
        """处理子进程的错误输出"""
        data = self.process.readAllStandardError()

        try:
            # 尝试UTF-8解码，如果失败则尝试其他编码
            error = bytes(data).decode('utf-8')
        except UnicodeDecodeError:
            try:
                # 如果UTF-8失败，尝试系统默认编码
                error = bytes(data).decode('gbk', errors='ignore')
            except Exception:
                # 如果都失败，使用错误忽略模式
                error = bytes(data).decode('utf-8', errors='ignore')

        lines = error.strip().split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 过滤掉一些常见的警告信息，避免干扰
            if 'HF_TOKEN' in line or 'HF Hub' in line:
                continue  # HF Token警告不影响功能
            if 'Loading weights' in line:
                continue  # 模型加载进度
            if 'transformers' in line and 'deprecated' in line:
                continue  # 库的废弃警告
            if 'attention_mask' in line:
                continue  # 注意力掩码警告
            if 'logits_process' in line:
                continue  # logits处理器警告
            if '_readerthread' in line or 'UnicodeDecodeError' in line:
                continue  # 忽略编码相关的内部错误

            self.log_message(f"[错误] {line}")

    def on_process_finished(self, exit_code, exit_status):
        """子进程完成处理"""
        self.set_processing_state(False)

        # 获取最后的输出
        if exit_code == 0:
            self.progress_bar.setValue(100)
            self.status_label.setText("完成!")
            QMessageBox.information(self, "完成", "字幕生成完成!")
            self.status_bar.showMessage("任务完成")
        else:
            self.log_message(f"任务异常结束，退出码: {exit_code}")
            QMessageBox.warning(self, "完成", f"任务异常结束，退出码: {exit_code}")
            self.status_bar.showMessage("任务结束")

    def reset_ui(self):
        """重置界面"""
        self.file_path_edit.clear()
        self.output_path_edit.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText("就绪")
        self.log_text.clear()
        self.set_processing_state(False)
        self.status_bar.showMessage("就绪")

    def update_progress(self, progress: int, status: str):
        """更新进度显示（用于手动更新）"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(status)

    def log_message(self, message: str):
        """添加日志消息，带时间戳"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_text.appendPlainText(f"[{timestamp}] {message}")
        # 自动滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_processing_state(self, processing: bool):
        """设置处理状态"""
        self.start_button.setEnabled(not processing)
        self.stop_button.setEnabled(processing)
        self.reset_button.setEnabled(not processing)
        self.file_button.setEnabled(not processing)
        self.output_button.setEnabled(not processing)
        self.refresh_models_button.setEnabled(not processing)

    def load_settings(self):
        """加载保存的设置"""
        # 加载模型设置
        self.whisper_model_edit.setText(self.config.get('Model', 'whisper_model', 'kotoba-tech/kotoba-whisper-v2.1'))
        # 翻译模型在refresh_model_list()之后设置，因为需要等待模型列表加载
        self.saved_translation_model = self.config.get('Model', 'translation_model', self.config.default_translation_model)
        self.lm_url_edit.setText(self.config.get('Model', 'lm_studio_url', 'http://127.0.0.1:1234/v1'))

        # 加载输出设置
        output_dir = self.config.get('Output', 'output_dir', '')
        if output_dir:
            self.output_path_edit.setText(output_dir)

        self.original_checkbox.setChecked(self.config.getboolean('Output', 'original_subtitle', True))
        self.translated_checkbox.setChecked(self.config.getboolean('Output', 'translated_subtitle', True))
        self.bilingual_checkbox.setChecked(self.config.getboolean('Output', 'bilingual_subtitle', True))
        self.filter_mood_checkbox.setChecked(self.config.getboolean('Output', 'filter_mood_words', True))

        # 加载UI设置
        last_file = self.config.get('UI', 'last_input_file', '')
        if last_file and os.path.exists(last_file):
            self.file_path_edit.setText(last_file)

        width = self.config.getint('UI', 'window_width', 800)
        height = self.config.getint('UI', 'window_height', 700)
        self.resize(width, height)

    def closeEvent(self, event):
        """窗口关闭事件"""
        # 保存窗口大小
        self.config.update_window_size(self.width(), self.height())
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = SubtitleGeneratorGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()