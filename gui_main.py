import os
import sys
import requests
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from gui_config import ConfigManager
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox,
    QProgressBar, QPlainTextEdit, QFileDialog, QGroupBox,
    QMessageBox, QStatusBar, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QInputDialog
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, pyqtSignal, QThread, QObject, QSize
from PyQt6.QtGui import QFont, QColor, QDragEnterEvent, QDropEvent

@dataclass
class TaskItem:
    """批量处理任务项"""
    file_path: str
    output_dir: str
    status: str = 'pending'  # pending/processing/completed/failed/paused
    progress: float = 0.0
    error_message: str = ''
    process_handle: Optional[subprocess.Popen] = None
    working_dir: str = ''
    retry_count: int = 0
    max_retries: int = 3


class BatchProcessor(QObject):
    """批量处理核心类，负责管理任务队列和调度"""

    # 信号定义
    task_started = pyqtSignal(int, str)          # index, file_path
    task_progress = pyqtSignal(int, float, str)  # index, progress, message
    task_finished = pyqtSignal(int, bool, str)  # index, success, error_msg
    batch_progress = pyqtSignal(int, int)       # completed, total
    batch_finished = pyqtSignal(dict)           # summary
    log_message = pyqtSignal(str, str)           # message, level

    def __init__(self, config):
        super().__init__()
        self.tasks: list[TaskItem] = []
        self.current_index = -1
        self.state = 'IDLE'  # IDLE/RUNNING/PAUSED/STOPPING
        self.config = config
        self._paused = False
        self._current_thread = None  # 当前运行的线程
        self._waiting_for_thread = False  # 是否正在等待线程完成

    def add_files(self, file_paths: list, output_dir: str):
        """添加文件到处理队列"""
        for file_path in file_paths:
            if self._is_valid_media(file_path):
                # 如果没有指定输出目录，使用文件所在目录
                task_output_dir = output_dir or str(Path(file_path).parent)
                task = TaskItem(
                    file_path=file_path,
                    output_dir=task_output_dir,
                    working_dir=str(Path(file_path).parent / f"temp_{uuid.uuid4().hex[:8]}")
                )
                self.tasks.append(task)
                self.log_message.emit(f"已添加文件: {Path(file_path).name}", "info")

    def remove_files(self, indices: list):
        """从队列中移除指定索引的文件"""
        # 按降序排列以避免索引变化
        for index in sorted(indices, reverse=True):
            if 0 <= index < len(self.tasks):
                # 如果正在处理这个文件，先停止
                if index == self.current_index and self.state == 'RUNNING':
                    self.stop_current_task()
                self.tasks.pop(index)

    def reorder_queue(self, new_order: list):
        """重新排列任务顺序"""
        if len(new_order) == len(self.tasks):
            new_tasks = [self.tasks[i] for i in new_order]
            self.tasks = new_tasks

    def start(self):
        """开始批量处理"""
        if self.state != 'IDLE':
            self.log_message.emit(f"批量处理器状态不是IDLE，当前状态: {self.state}", "warning")
            return

        if not self.tasks:
            self.log_message.emit("没有待处理的任务", "warning")
            return

        self.state = 'RUNNING'
        self._paused = False
        self.log_message.emit(f"开始批量处理，共 {len(self.tasks)} 个任务...", "info")

        # 找到第一个待处理的任务
        self.current_index = -1
        self._process_next()

    def pause(self):
        """暂停处理"""
        if self.state == 'RUNNING':
            self.state = 'PAUSED'
            self._paused = True
            self.log_message.emit("批量处理已暂停", "warning")
            if self.current_index >= 0 and self.tasks[self.current_index].status == 'processing':
                self.stop_current_task()
                self.tasks[self.current_index].status = 'paused'

    def resume(self):
        """恢复处理"""
        if self.state == 'PAUSED':
            self.state = 'RUNNING'
            self._paused = False
            self.log_message.emit("恢复批量处理...", "info")

            # 查找待处理的任务
            if self.current_index >= 0 and self.tasks[self.current_index].status == 'paused':
                self.tasks[self.current_index].status = 'pending'
            self._process_next()

    def stop(self):
        """停止所有处理"""
        if self.state in ('RUNNING', 'PAUSED'):
            self.state = 'STOPPING'
            self.stop_current_task()

            # 等待线程完成
            if self._current_thread and self._current_thread.isRunning():
                self._current_thread.quit()
                self._current_thread.wait()
                self._current_thread.deleteLater()
                self._current_thread = None
                self._waiting_for_thread = False

            # 清除剩余任务
            for task in self.tasks[self.current_index + 1:]:
                task.status = 'pending'  # 重置为待处理状态

            self.log_message.emit("批量处理已停止", "warning")
            self.state = 'IDLE'

    def retry_failed(self):
        """重试所有失败的任务"""
        retry_count = 0
        for task in self.tasks:
            if task.status == 'failed' and task.retry_count < task.max_retries:
                task.status = 'pending'
                task.error_message = ''
                retry_count += 1

        if retry_count > 0:
            self.log_message.emit(f"已重置 {retry_count} 个失败任务", "info")
        else:
            self.log_message.emit("没有可重试的失败任务", "info")

    def _process_next(self):
        """处理下一个任务"""
        # 等待上一个线程完成
        if self._waiting_for_thread:
            self.log_message.emit("正在等待上一个任务完成...", "warning")
            return

        # 查找下一个待处理的任务
        next_index = -1
        for i in range(self.current_index + 1, len(self.tasks)):
            if self.tasks[i].status == 'pending':
                next_index = i
                break

        if next_index == -1:
            # 没有待处理的任务，完成批量处理
            self._on_batch_finished()
            return

        self.current_index = next_index
        self._start_task(next_index)

    def _start_task(self, index: int):
        """启动指定索引的任务"""
        task = self.tasks[index]
        task.status = 'processing'
        task.progress = 0.0
        self.log_message.emit(f"开始处理文件 ({index + 1}/{len(self.tasks)}): {Path(task.file_path).name}", "info")
        self.task_started.emit(index, task.file_path)

        # 清理之前的线程
        if self._current_thread and self._current_thread.isRunning():
            self._current_thread.quit()
            self._current_thread.wait()
            self._current_thread.deleteLater()

        # 在子线程中启动子进程
        self._current_thread = TaskThread(task, self.config)
        self._current_thread.progress.connect(lambda msg: self._on_task_progress(index, msg))
        self._current_thread.finished.connect(lambda success, error: self._on_task_finished(index, success, error))
        self._current_thread.log.connect(lambda msg: self.log_message.emit(msg, "info"))
        self._waiting_for_thread = True
        self._current_thread.start()

    def _on_task_progress(self, index: int, message: str):
        """处理任务进度更新"""
        if index < len(self.tasks):
            task = self.tasks[index]

            # 解析进度信息
            import re

            # 转录阶段：0-20%
            if '[转录]' in message or '转录' in message:
                task.progress = min(task.progress + 5, 20)
                self.task_progress.emit(index, task.progress, "正在转录...")

            # 翻译阶段：20-80%
            translate_match = re.search(r'\[翻译\]\s*\[(\d+)/(\d+)\]', message)
            if translate_match:
                current = int(translate_match.group(1))
                total = int(translate_match.group(2))
                progress = 20 + int((current / total) * 60)
                task.progress = progress
                self.task_progress.emit(index, progress, f"翻译中... [{current}/{total}]")

            # 输出阶段：80-100%
            if '[输出]' in message or '生成字幕文件' in message:
                task.progress = 80
                self.task_progress.emit(index, 80, "生成字幕文件...")

    def _on_task_finished(self, index: int, success: bool, error: str):
        """处理任务完成"""
        # 等待线程完成
        if self._current_thread:
            self._current_thread.quit()
            self._current_thread.wait()
            self._current_thread.deleteLater()
            self._current_thread = None
            self._waiting_for_thread = False

        if index < len(self.tasks):
            task = self.tasks[index]

            if success:
                task.status = 'completed'
                task.progress = 100.0
                task.error_message = ''
                self.log_message.emit(f"文件处理完成: {Path(task.file_path).name}", "success")
                self.task_progress.emit(index, 100.0, "完成")
            else:
                task.status = 'failed'
                task.error_message = error
                self.log_message.emit(f"文件处理失败: {Path(task.file_path).name} - {error}", "error")

            self.task_finished.emit(index, success, error)
            self._update_batch_progress()

            # 处理下一个任务（如果未停止）
            if self.state == 'RUNNING' and not self._paused:
                self._process_next()

    def stop_current_task(self):
        """停止当前正在执行的任务"""
        if self.current_index >= 0 and self.current_index < len(self.tasks):
            task = self.tasks[self.current_index]
            if task.process_handle and task.process_handle.poll() is None:
                try:
                    task.process_handle.terminate()
                    import time
                    time.sleep(1)  # 等待进程优雅退出
                    if task.process_handle.poll() is None:
                        task.process_handle.kill()
                except Exception as e:
                    self.log_message.emit(f"停止任务时出错: {e}", "error")

    def _update_batch_progress(self):
        """更新批量处理进度"""
        completed = sum(1 for t in self.tasks if t.status in ('completed', 'failed'))
        total = len(self.tasks)
        self.batch_progress.emit(completed, total)

    def _on_batch_finished(self):
        """批量处理完成"""
        self.state = 'IDLE'

        # 生成汇总信息
        summary = {
            'total': len(self.tasks),
            'completed': sum(1 for t in self.tasks if t.status == 'completed'),
            'failed': sum(1 for t in self.tasks if t.status == 'failed'),
            'failed_files': [
                {
                    'file': Path(t.file_path).name,
                    'error': t.error_message
                }
                for t in self.tasks if t.status == 'failed'
            ]
        }

        self.log_message.emit(f"批量处理完成! 成功: {summary['completed']}, 失败: {summary['failed']}", "info")
        self.batch_finished.emit(summary)

    def _is_valid_media(self, file_path: str) -> bool:
        """检查是否为有效的媒体文件"""
        valid_extensions = {
            '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv',  # 视频
            '.wav', '.mp3', '.m4a', '.flac',  # 音频
        }
        return Path(file_path).suffix.lower() in valid_extensions


class TaskThread(QThread):
    """任务执行线程"""

    # 信号定义
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)

    def __init__(self, task: TaskItem, config):
        super().__init__()
        self.task = task
        self.config = config

    def run(self):
        """执行任务"""
        try:
            import json
            import sys

            # 准备参数
            params = {
                'input_file': self.task.file_path,
                'output_dir': self.task.output_dir,
                'whisper_model': self.config.get('Model', 'whisper_model', 'kotoba-tech/kotoba-whisper-v2.1'),
                'translation_model': self.config.get('Model', 'translation_model', 'sakura-galtransl-7b-v3.7'),
                'lm_studio_url': self.config.get('Model', 'lm_studio_url', 'http://127.0.0.1:1234/v1'),
                'batch_size': 70,  # 默认批量大小
                'filter_mood_words': self.config.getboolean('Output', 'filter_mood_words', True),
                'debug_mode': self.config.getboolean('Output', 'debug_mode', True),
                'output_formats': {
                    'original': self.config.getboolean('Output', 'original_subtitle', True),
                    'translated': self.config.getboolean('Output', 'translated_subtitle', True),
                    'bilingual': self.config.getboolean('Output', 'bilingual_subtitle', True)
                }
            }

            params_json = json.dumps(params, ensure_ascii=False)
            script_path = Path(__file__).parent / "subprocess_processor.py"

            # 创建工作目录
            os.makedirs(self.task.working_dir, exist_ok=True)

            # 启动子进程
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUNBUFFERED'] = '1'

            self.task.process_handle = subprocess.Popen(
                [sys.executable, str(script_path), "--params", params_json],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore',
                env=env
            )

            # 读取输出
            while True:
                try:
                    output = self.task.process_handle.stdout.readline()
                    if output == '' and self.task.process_handle.poll() is not None:
                        break
                    if output:
                        output = output.strip()
                        self.progress.emit(output)
                        self.log.emit(output)
                except Exception as e:
                    self.log.emit(f"读取输出时出错: {e}")
                    break

            # 读取错误输出
            _, stderr = self.task.process_handle.communicate()

            # 检查退出状态
            exit_code = self.task.process_handle.returncode
            if exit_code == 0:
                self.finished.emit(True, "")
            else:
                # 尝试解析错误信息
                try:
                    if stderr:
                        for line in stderr.split('\n'):
                            line = line.strip()
                            if line and line.startswith('{"success"'):
                                result = json.loads(line)
                                if not result.get('success', True):
                                    self.finished.emit(False, result.get('error', '未知错误'))
                                    return
                except json.JSONDecodeError:
                    pass

                self.finished.emit(False, f"子进程异常退出，退出码: {exit_code}")

        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            # 清理工作目录
            try:
                if os.path.exists(self.task.working_dir):
                    import shutil
                    shutil.rmtree(self.task.working_dir)
            except Exception as e:
                self.log.emit(f"清理临时目录失败: {e}")

            # 确保进程被终止
            if hasattr(self, 'task') and self.task.process_handle:
                try:
                    if self.task.process_handle.poll() is None:
                        self.task.process_handle.terminate()
                        import time
                        time.sleep(0.5)
                        if self.task.process_handle.poll() is None:
                            self.task.process_handle.kill()
                except Exception:
                    pass


class SubtitleGeneratorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            self.config = ConfigManager()
            self.worker = None
            self.available_models = []
            self.saved_translation_model = self.config.default_translation_model  # 使用配置文件中的默认翻译模型

            # 初始化批量处理器
            self.batch_processor = BatchProcessor(self.config)
            self._connect_batch_processor_signals()

            self.init_ui()
            self.load_settings()
            self.refresh_model_list()

            print("GUI初始化完成")
        except Exception as e:
            print(f"GUI初始化失败: {e}")
            import traceback
            traceback.print_exc()

    def _connect_batch_processor_signals(self):
        """连接批量处理器的信号"""
        self.batch_processor.task_started.connect(self.on_task_started)
        self.batch_processor.task_progress.connect(self.on_task_progress)
        self.batch_processor.task_finished.connect(self.on_task_finished)
        self.batch_processor.batch_progress.connect(self.on_batch_progress)
        self.batch_processor.batch_finished.connect(self.on_batch_finished)
        self.batch_processor.log_message.connect(self.log_message)

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("字幕生成器 v2.0")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # 加载样式表
        self.load_stylesheet()

        # 主窗口部件
        main_widget = QWidget()
        main_widget.setObjectName("main_container")
        self.setCentralWidget(main_widget)

        # 主布局：左右分栏
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_widget.setLayout(main_layout)

        # 左侧设置面板
        self.sidebar_panel = self._create_sidebar_panel()
        main_layout.addWidget(self.sidebar_panel)

        # 右侧工作区
        self.main_content = self._create_main_content()
        main_layout.addWidget(self.main_content)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def _create_file_selection_group(self):
        """创建文件选择区域"""
        group = QGroupBox("文件管理")
        layout = QVBoxLayout()

        # 操作按钮行
        button_layout = QHBoxLayout()
        self.select_files_button = QPushButton("选择文件（多选）")
        self.select_files_button.clicked.connect(self.select_files)
        self.add_files_button = QPushButton("添加文件")
        self.add_files_button.clicked.connect(self.select_files)
        self.clear_list_button = QPushButton("清空列表")
        self.clear_list_button.clicked.connect(self.clear_file_list)

        button_layout.addWidget(self.select_files_button)
        button_layout.addWidget(self.add_files_button)
        button_layout.addWidget(self.clear_list_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # 拖拽提示
        drag_hint = QLabel("📁 拖拽文件/文件夹到此处添加")
        drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drag_hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(drag_hint)

        # 文件列表表格
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(6)
        self.file_table.setHorizontalHeaderLabels(["序号", "文件名", "大小", "状态", "进度", "操作"])
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.file_table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.file_table.setAcceptDrops(True)
        self.file_table.setDropIndicatorShown(True)
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        # 设置表格拖拽
        self.file_table.dragEnterEvent = self.table_drag_enter_event
        self.file_table.dropEvent = self.table_drop_event

        # 右键菜单
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(self.show_context_menu)

        layout.addWidget(self.file_table)

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

        # 统计信息
        self.stats_label = QLabel("统计：共0个 | ⏳0  🔄0  ✅0  ❌0")
        layout.addWidget(self.stats_label)

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

        # Debug模式选项
        self.debug_mode_checkbox = QCheckBox("Debug模式")
        self.debug_mode_checkbox.setChecked(True)
        self.debug_mode_checkbox.setToolTip("开启时保留所有中间文件（音频、转录文本），关闭时仅保留最终字幕文件")

        filter_layout.addWidget(self.filter_mood_checkbox)
        filter_layout.addWidget(self.debug_mode_checkbox)
        filter_layout.addStretch()
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

        self.pause_button = QPushButton("暂停 ⏸️")
        self.pause_button.setMinimumHeight(40)
        self.pause_button.clicked.connect(self.pause_processing)
        self.pause_button.setEnabled(False)

        self.stop_button = QPushButton("停止 ⏹️")
        self.stop_button.setMinimumHeight(40)
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setEnabled(False)

        self.retry_button = QPushButton("重试失败项 🔄")
        self.retry_button.setMinimumHeight(40)
        self.retry_button.clicked.connect(self.retry_failed)

        layout.addWidget(self.start_button)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.retry_button)

        group.setLayout(layout)
        return group

    def _create_progress_section(self):
        """创建进度显示区域"""
        section = QWidget()
        section.setObjectName("progress_section")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        section.setLayout(layout)

        # 总体进度
        overall_item = QWidget()
        overall_layout = QHBoxLayout()
        overall_layout.setContentsMargins(0, 0, 0, 0)
        overall_item.setLayout(overall_layout)

        overall_label = QLabel("总体进度")
        overall_label.setObjectName("progress_label")
        overall_layout.addWidget(overall_label)

        self.overall_progress_label = QLabel("0/0 (0%)")
        self.overall_progress_label.setObjectName("progress_value")
        overall_layout.addStretch()
        overall_layout.addWidget(self.overall_progress_label)
        layout.addWidget(overall_item)

        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setTextVisible(False)
        layout.addWidget(self.overall_progress_bar)

        # 当前文件进度
        current_item = QWidget()
        current_layout = QHBoxLayout()
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_item.setLayout(current_layout)

        self.current_file_label = QLabel("当前文件: 无")
        self.current_file_label.setObjectName("progress_label")
        current_layout.addWidget(self.current_file_label)

        self.current_progress_label = QLabel("处理中 (0%)")
        self.current_progress_label.setObjectName("progress_value")
        current_layout.addStretch()
        current_layout.addWidget(self.current_progress_label)
        layout.addWidget(current_item)

        self.current_progress_bar = QProgressBar()
        self.current_progress_bar.setTextVisible(False)
        layout.addWidget(self.current_progress_bar)

        # 状态文字
        self.status_label = QLabel("📌 状态: 就绪")
        self.status_label.setObjectName("progress_label")
        layout.addWidget(self.status_label)

        return section

    def _create_log_section(self):
        """创建日志显示区域"""
        section = QWidget()
        section.setObjectName("log_area")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        section.setLayout(layout)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(180)
        self.log_text.setFont(QFont("Consolas", 10))

        layout.addWidget(self.log_text)
        return section

    def _create_sidebar_panel(self):
        """创建左侧设置面板"""
        sidebar = QWidget()
        sidebar.setObjectName("sidebar_panel")
        sidebar.setFixedWidth(320)

        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(20, 20, 20, 20)
        sidebar_layout.setSpacing(15)
        sidebar.setLayout(sidebar_layout)

        # 标题
        title_label = QLabel("⚙️ 配置设置")
        title_label.setObjectName("sidebar_title")
        sidebar_layout.addWidget(title_label)

        # AI模型配置区域
        sidebar_layout.addWidget(self._create_ai_model_section())

        # 输出格式区域
        sidebar_layout.addWidget(self._create_output_format_section())

        # 输出设置区域
        sidebar_layout.addWidget(self._create_output_settings_section())

        sidebar_layout.addStretch()

        return sidebar

    def _create_ai_model_section(self):
        """创建AI模型配置区域"""
        section = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        section.setLayout(layout)

        # 标题
        title = QLabel("🤖 AI 模型")
        title.setObjectName("group_title")
        layout.addWidget(title)

        # Whisper模型
        whisper_label = QLabel("Whisper 模型")
        whisper_label.setObjectName("setting_label")
        layout.addWidget(whisper_label)

        self.whisper_model_edit = QLineEdit("kotoba-tech/kotoba-whisper-v2.1")
        self.whisper_model_edit.setObjectName("tech_input")
        self.whisper_model_edit.setReadOnly(True)
        layout.addWidget(self.whisper_model_edit)

        # 翻译模型
        trans_label = QLabel("翻译模型")
        trans_label.setObjectName("setting_label")
        layout.addWidget(trans_label)

        self.trans_model_combo = QComboBox()
        self.trans_model_combo.setObjectName("tech_input")
        self.trans_model_combo.addItems(["sakura-galtransl-7b-v3.7", "qwen2.5-7b-instruct", "hy-mt1.5-1.8b"])
        layout.addWidget(self.trans_model_combo)

        self.refresh_models_button = QPushButton("刷新模型列表")
        self.refresh_models_button.clicked.connect(self.refresh_model_list)
        layout.addWidget(self.refresh_models_button)

        # LM Studio URL
        url_label = QLabel("LM Studio 地址")
        url_label.setObjectName("setting_label")
        layout.addWidget(url_label)

        self.lm_url_edit = QLineEdit("http://127.0.0.1:1234/v1")
        self.lm_url_edit.setObjectName("tech_input")
        layout.addWidget(self.lm_url_edit)

        # 批量大小
        batch_label = QLabel("批量大小 (7-150)")
        batch_label.setObjectName("setting_label")
        layout.addWidget(batch_label)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setMinimum(7)
        self.batch_size_spin.setMaximum(150)
        self.batch_size_spin.setValue(70)
        self.batch_size_spin.setObjectName("tech_input")
        self.batch_size_spin.setToolTip("一次翻译的字幕条数，更大的值提供更好的上下文但可能影响速度")
        layout.addWidget(self.batch_size_spin)

        return section

    def _create_output_format_section(self):
        """创建输出格式区域"""
        section = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        section.setLayout(layout)

        # 标题
        title = QLabel("📄 输出格式")
        title.setObjectName("group_title")
        layout.addWidget(title)

        # 字幕格式复选框
        self.original_checkbox = QCheckBox("原文字幕")
        self.original_checkbox.setChecked(True)
        layout.addWidget(self.original_checkbox)

        self.translated_checkbox = QCheckBox("中文字幕")
        self.translated_checkbox.setChecked(True)
        layout.addWidget(self.translated_checkbox)

        self.bilingual_checkbox = QCheckBox("双语字幕")
        self.bilingual_checkbox.setChecked(False)
        layout.addWidget(self.bilingual_checkbox)

        self.filter_mood_checkbox = QCheckBox("过滤语气词")
        self.filter_mood_checkbox.setChecked(True)
        self.filter_mood_checkbox.setToolTip("自动去除如'啊、哦、嗯'等无意义的语气词")
        layout.addWidget(self.filter_mood_checkbox)

        self.debug_mode_checkbox = QCheckBox("调试模式")
        self.debug_mode_checkbox.setChecked(True)
        self.debug_mode_checkbox.setToolTip("开启时保留所有中间文件")
        layout.addWidget(self.debug_mode_checkbox)

        return section

    def _create_output_settings_section(self):
        """创建输出设置区域"""
        section = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        section.setLayout(layout)

        # 标题
        title = QLabel("📁 输出设置")
        title.setObjectName("group_title")
        layout.addWidget(title)

        # 输出目录
        output_label = QLabel("输出目录")
        output_label.setObjectName("setting_label")
        layout.addWidget(output_label)

        output_layout = QHBoxLayout()
        self.output_path_edit = QLineEdit("D:\\字幕输出")
        self.output_path_edit.setObjectName("tech_input")
        self.output_path_edit.setPlaceholderText("默认为输入文件所在目录")

        self.output_button = QPushButton("选择")
        self.output_button.clicked.connect(self.select_output_dir)
        self.output_button.setMaximumWidth(60)

        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_button)
        layout.addLayout(output_layout)

        # 选项
        self.keep_temp_checkbox = QCheckBox("保留临时文件")
        self.keep_temp_checkbox.setChecked(False)
        layout.addWidget(self.keep_temp_checkbox)

        self.recursive_scan_checkbox = QCheckBox("递归扫描文件夹")
        self.recursive_scan_checkbox.setChecked(True)
        layout.addWidget(self.recursive_scan_checkbox)

        return section

    def _create_main_content(self):
        """创建右侧主要工作区"""
        content = QWidget()
        content.setObjectName("main_content")

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(25, 20, 25, 20)
        content_layout.setSpacing(20)
        content.setLayout(content_layout)

        # 头部标题
        header = self._create_content_header()
        content_layout.addWidget(header)

        # 文件队列区域
        content_layout.addWidget(self._create_file_queue_section())

        # 控制按钮区域
        content_layout.addWidget(self._create_control_panel())

        # 进度显示区域
        content_layout.addWidget(self._create_progress_section())

        # 日志输出区域
        content_layout.addWidget(self._create_log_section())

        content_layout.addStretch()

        return content

    def _create_content_header(self):
        """创建内容头部"""
        header = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        header.setLayout(layout)

        # 收缩按钮
        self.toggle_button = QPushButton("◀")
        self.toggle_button.setObjectName("toggle_button")
        self.toggle_button.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.toggle_button)

        # 标题组
        title_group = QWidget()
        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(5)
        title_group.setLayout(title_layout)

        app_title = QLabel("字幕生成器 v2.0")
        app_title.setObjectName("app_title")
        title_layout.addWidget(app_title)

        app_subtitle = QLabel("专业级日语字幕批量处理系统")
        app_subtitle.setObjectName("app_subtitle")
        title_layout.addWidget(app_subtitle)

        layout.addWidget(title_group)
        layout.addStretch()

        return header

    def _create_file_queue_section(self):
        """创建文件队列区域"""
        section = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        section.setLayout(layout)

        # 标题和按钮
        header = QHBoxLayout()
        title = QLabel("📋 文件队列")
        title.setObjectName("section_title")
        header.addWidget(title)

        button_layout = QHBoxLayout()
        self.select_files_button = QPushButton("📁 选择文件")
        self.select_files_button.clicked.connect(self.select_files)
        self.add_folder_button = QPushButton("📂 添加文件夹")
        self.add_folder_button.clicked.connect(self.select_folder)
        self.clear_list_button = QPushButton("🗑️ 清空列表")
        self.clear_list_button.clicked.connect(self.clear_file_list)

        button_layout.addWidget(self.select_files_button)
        button_layout.addWidget(self.add_folder_button)
        button_layout.addWidget(self.clear_list_button)
        header.addLayout(button_layout)

        layout.addLayout(header)

        # 文件列表表格
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(6)
        self.file_table.setHorizontalHeaderLabels(["序号", "文件名", "大小", "状态", "进度", "操作"])
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.file_table.setAcceptDrops(True)
        self.file_table.setDropIndicatorShown(True)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.verticalHeader().setVisible(False)

        # 设置表格拖拽
        self.file_table.dragEnterEvent = self.table_drag_enter_event
        self.file_table.dropEvent = self.table_drop_event

        # 右键菜单
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(self.show_context_menu)

        layout.addWidget(self.file_table)

        # 统计信息
        self.stats_label = QLabel("总计: 0  |  已完成: 0  |  处理中: 0  |  等待中: 0  |  失败: 0")
        self.stats_label.setObjectName("stats_bar")
        layout.addWidget(self.stats_label)

        return section

    def _create_control_panel(self):
        """创建控制面板"""
        panel = QWidget()
        panel.setObjectName("control_panel")
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        panel.setLayout(layout)

        self.start_button = QPushButton("🚀 开始处理")
        self.start_button.setObjectName("primary_button")
        self.start_button.clicked.connect(self.start_processing)
        layout.addWidget(self.start_button)

        self.pause_button = QPushButton("⏸️ 暂停")
        self.pause_button.clicked.connect(self.pause_processing)
        self.pause_button.setEnabled(False)
        layout.addWidget(self.pause_button)

        self.stop_button = QPushButton("⏹️ 停止")
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)

        self.retry_button = QPushButton("🔄 重试失败")
        self.retry_button.clicked.connect(self.retry_failed)
        layout.addWidget(self.retry_button)

        return panel

    def toggle_sidebar(self):
        """切换侧边栏显示"""
        if self.sidebar_panel.width() > 0:
            self.sidebar_panel.setFixedWidth(0)
            self.toggle_button.setText("▶")
        else:
            self.sidebar_panel.setFixedWidth(320)
            self.toggle_button.setText("◀")

    def select_folder(self):
        """选择文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if folder:
            output_dir = self.output_path_edit.text()
            # 递归扫描文件夹中的媒体文件
            files = self._scan_media_files(folder, self.recursive_scan_checkbox.isChecked())
            self.batch_processor.add_files(files, output_dir)
            self.update_file_table()

    def _scan_media_files(self, folder: str, recursive: bool = True) -> list:
        """扫描文件夹中的媒体文件"""
        media_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv',
                          '.wav', '.mp3', '.m4a', '.flac', '.aac'}
        files = []

        if recursive:
            for root, _, filenames in os.walk(folder):
                for filename in filenames:
                    if Path(filename).suffix.lower() in media_extensions:
                        files.append(str(Path(root) / filename))
        else:
            for item in Path(folder).iterdir():
                if item.is_file() and item.suffix.lower() in media_extensions:
                    files.append(str(item))

        return files

    def select_files(self):
        """选择多个输入文件"""
        file_filter = "媒体文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.wav *.mp3 *.m4a *.flac);;视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv);;音频文件 (*.wav *.mp3 *.m4a *.flac);;所有文件 (*.*)"
        files, _ = QFileDialog.getOpenFileNames(self, "选择视频/音频文件（多选）", "", file_filter)

        if files:
            output_dir = self.output_path_edit.text()
            self.batch_processor.add_files(files, output_dir)
            self.update_file_table()

    def clear_file_list(self):
        """清空文件列表"""
        if self.batch_processor.state != 'IDLE':
            QMessageBox.warning(self, "警告", "请先停止处理再清空列表！")
            return

        self.batch_processor.tasks.clear()
        self.update_file_table()
        self.log_message("文件列表已清空")

    def load_stylesheet(self):
        """加载样式表"""
        try:
            with open('gui_styles.qss', 'r', encoding='utf-8') as f:
                self.setStyleSheet(f.read())
        except Exception as e:
            print(f"加载样式表失败: {e}")

    def table_drag_enter_event(self, event: QDragEnterEvent):
        """表格拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def table_drop_event(self, event: QDropEvent):
        """表格拖拽放下事件"""
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        all_files = []

        for path in paths:
            if os.path.isdir(path):
                # 递归扫描文件夹
                all_files.extend(self._scan_media_files(path))
            elif self.batch_processor._is_valid_media(path):
                all_files.append(path)

        if all_files:
            output_dir = self.output_path_edit.text()
            self.batch_processor.add_files(all_files, output_dir)
            self.update_file_table()

    def _scan_media_files(self, directory: str) -> list:
        """扫描文件夹中的媒体文件"""
        media_files = []
        valid_extensions = {
            '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv',
            '.wav', '.mp3', '.m4a', '.flac'
        }

        for root, _, files in os.walk(directory):
            for file in files:
                if Path(file).suffix.lower() in valid_extensions:
                    media_files.append(os.path.join(root, file))

        return media_files

    def show_context_menu(self, position):
        """显示右键菜单"""
        if not self.file_table.itemAt(position):
            return

        menu = QMenu()
        remove_action = menu.addAction("移除")
        open_folder_action = menu.addAction("打开所在文件夹")
        copy_path_action = menu.addAction("复制路径")

        action = menu.exec_(self.file_table.mapToGlobal(position))

        current_row = self.file_table.currentRow()
        if current_row < 0 or current_row >= len(self.batch_processor.tasks):
            return

        task = self.batch_processor.tasks[current_row]

        if action == remove_action:
            if task.status == 'processing':
                QMessageBox.warning(self, "警告", "无法移除正在处理的文件！")
                return
            self.batch_processor.remove_files([current_row])
            self.update_file_table()

        elif action == open_folder_action:
            import subprocess
            subprocess.run(['explorer', '/select,', task.file_path])

        elif action == copy_path_action:
            clipboard = QApplication.clipboard()
            clipboard.setText(task.file_path)

    def update_file_table(self):
        """更新文件列表显示"""
        self.file_table.setRowCount(len(self.batch_processor.tasks))

        for i, task in enumerate(self.batch_processor.tasks):
            # 序号
            item_num = QTableWidgetItem(str(i + 1))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(i, 0, item_num)

            # 文件名
            file_name = Path(task.file_path).name
            item_name = QTableWidgetItem(file_name)
            item_name.setToolTip(task.file_path)
            self.file_table.setItem(i, 1, item_name)

            # 文件大小（处理文件不存在的情况）
            try:
                if os.path.exists(task.file_path):
                    size_mb = os.path.getsize(task.file_path) / (1024 * 1024)
                    item_size = QTableWidgetItem(f"{size_mb:.1f} MB")
                else:
                    item_size = QTableWidgetItem("文件不存在")
            except Exception as e:
                item_size = QTableWidgetItem("未知大小")

            item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(i, 2, item_size)

            # 状态
            status_text = {
                'pending': '等待中',
                'processing': '处理中',
                'completed': '已完成',
                'failed': '失败',
                'paused': '已暂停'
            }
            item_status = QTableWidgetItem(status_text.get(task.status, task.status))
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 设置状态样式
            if task.status == 'completed':
                item_status.setBackground(QColor(34, 197, 94, 50))  # 绿色背景
                item_status.setForeground(QColor(34, 197, 94))      # 绿色文字
            elif task.status == 'processing':
                item_status.setBackground(QColor(96, 165, 250, 50)) # 蓝色背景
                item_status.setForeground(QColor(96, 165, 250))     # 蓝色文字
            elif task.status == 'pending':
                item_status.setBackground(QColor(251, 191, 36, 50)) # 黄色背景
                item_status.setForeground(QColor(251, 191, 36))     # 黄色文字
            elif task.status == 'failed':
                item_status.setBackground(QColor(239, 68, 68, 50))   # 红色背景
                item_status.setForeground(QColor(239, 68, 68))       # 红色文字

            self.file_table.setItem(i, 3, item_status)

            # 进度
            item_progress = QTableWidgetItem(f"{task.progress:.0f}%")
            item_progress.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(i, 4, item_progress)

            # 操作
            item_action = QTableWidgetItem("操作")
            item_action.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(i, 5, item_action)

        # 更新统计信息
        total = len(self.batch_processor.tasks)
        pending = sum(1 for t in self.batch_processor.tasks if t.status == 'pending')
        processing = sum(1 for t in self.batch_processor.tasks if t.status == 'processing')
        completed = sum(1 for t in self.batch_processor.tasks if t.status == 'completed')
        failed = sum(1 for t in self.batch_processor.tasks if t.status == 'failed')

        self.stats_label.setText(f"统计：共{total}个 | 待处理:{pending}  处理中:{processing}  已完成:{completed}  失败:{failed}")

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
        """开始批量处理任务"""
        # 检查是否有文件
        if not self.batch_processor.tasks:
            QMessageBox.warning(self, "警告", "请先添加文件到列表！")
            return

        # 检查批量处理器状态
        if self.batch_processor.state != 'IDLE':
            QMessageBox.warning(self, "警告", "批量处理器正在运行，请先停止或等待完成！")
            return

        # 验证输出格式选择
        if not any([self.original_checkbox.isChecked(),
                   self.translated_checkbox.isChecked(),
                   self.bilingual_checkbox.isChecked()]):
            QMessageBox.warning(self, "警告", "请至少选择一种输出格式！")
            return

        # 检查文件是否存在
        for task in self.batch_processor.tasks:
            if not os.path.exists(task.file_path):
                QMessageBox.warning(self, "警告", f"文件不存在: {task.file_path}")
                return

        # 保存配置
        try:
            self.config.update_model_settings(
                self.whisper_model_edit.text(),
                self.trans_model_combo.currentText(),
                self.lm_url_edit.text()
            )
            self.config.update_output_settings(
                self.output_path_edit.text(),
                self.original_checkbox.isChecked(),
                self.translated_checkbox.isChecked(),
                self.bilingual_checkbox.isChecked(),
                self.filter_mood_checkbox.isChecked(),
                self.debug_mode_checkbox.isChecked()
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败: {e}")
            return

        # 启动批量处理
        try:
            self.batch_processor.start()
            self.update_button_states()
            self.log_message("批量处理已启动")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动批量处理失败: {e}")
            self.log_message(f"启动失败: {e}")

    def pause_processing(self):
        """暂停批量处理"""
        if self.batch_processor.state == 'RUNNING':
            self.batch_processor.pause()
            self.start_button.setText("继续 ▶️")
            self.pause_button.setEnabled(False)
        elif self.batch_processor.state == 'PAUSED':
            self.batch_processor.resume()
            self.start_button.setText("开始处理 🚀")
            self.pause_button.setEnabled(True)

    def stop_processing(self):
        """停止批量处理"""
        self.batch_processor.stop()
        self.start_button.setText("开始处理 🚀")
        self.update_button_states()

    def retry_failed(self):
        """重试失败的任务"""
        self.batch_processor.retry_failed()
        self.update_file_table()

    def stop_processing(self):
        """停止批量处理"""
        self.batch_processor.stop()
        self.start_button.setText("开始处理 🚀")
        self.update_button_states()

    def retry_failed(self):
        """重试失败的任务"""
        self.batch_processor.retry_failed()
        self.update_file_table()

    def on_task_started(self, index: int, file_path: str):
        """任务开始时的处理"""
        self.current_file_label.setText(Path(file_path).name)
        self.status_label.setText(f"正在处理: {Path(file_path).name} ({index + 1}/{len(self.batch_processor.tasks)})")
        self.update_file_table()

    def on_task_progress(self, index: int, progress: float, message: str):
        """任务进度更新"""
        if index < len(self.batch_processor.tasks):
            self.batch_processor.tasks[index].progress = progress
            self.current_progress_bar.setValue(int(progress))
            self.status_label.setText(message)

            # 更新表格中的进度
            progress_item = self.file_table.item(index, 4)
            if progress_item:
                progress_item.setText(f"{progress:.0f}%")

    def on_task_finished(self, index: int, success: bool, error_msg: str):
        """任务完成时的处理"""
        self.update_file_table()

        if not success:
            self.log_message(f"任务失败: {error_msg}")

    def on_batch_progress(self, completed: int, total: int):
        """批量处理进度更新"""
        progress = int((completed / total) * 100) if total > 0 else 0
        self.overall_progress_bar.setValue(progress)
        self.overall_progress_label.setText(f"({completed}/{total})")

    def on_batch_finished(self, summary: dict):
        """批量处理完成时的处理"""
        self.update_button_states()

        # 显示汇总信息
        message = (
            f"批量处理完成！\n\n"
            f"总文件数: {summary['total']}\n"
            f"成功: {summary['completed']}\n"
            f"失败: {summary['failed']}\n"
        )

        if summary['failed'] > 0:
            failed_files = "\n".join([f"- {f['file']}: {f['error']}" for f in summary['failed_files']])
            message += f"\n\n失败文件:\n{failed_files}"

        QMessageBox.information(self, "处理完成", message)

    def update_button_states(self):
        """更新按钮状态"""
        state = self.batch_processor.state

        if state == 'IDLE':
            self.start_button.setEnabled(True)
            self.start_button.setText("开始处理 🚀")
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.file_table.setEnabled(True)
        elif state == 'RUNNING':
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.file_table.setEnabled(False)
        elif state == 'PAUSED':
            self.start_button.setEnabled(True)
            self.start_button.setText("继续 ▶️")
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.file_table.setEnabled(False)
        elif state == 'STOPPING':
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.file_table.setEnabled(False)

    def update_progress(self, progress: int, status: str):
        """更新进度显示（用于手动更新）"""
        self.current_progress_bar.setValue(progress)
        self.status_label.setText(status)

    def reset_ui(self):
        """重置界面"""
        self.clear_file_list()
        self.output_path_edit.clear()
        self.overall_progress_bar.setValue(0)
        self.overall_progress_label.setText("(0/0)")
        self.current_progress_bar.setValue(0)
        self.current_file_label.setText("无")
        self.status_label.setText("就绪")
        self.log_text.clear()
        self.update_button_states()
        self.status_bar.showMessage("就绪")

    def update_progress(self, progress: int, status: str):
        """更新进度显示（用于手动更新）"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(status)

    def log_message(self, message: str, level: str = 'info'):
        """添加日志消息，带时间戳和emoji

        Args:
            message: 日志消息
            level: 日志级别 (info/success/warning/error)
        """
        timestamp = datetime.now().strftime('%H:%M:%S')

        # 根据日志级别设置emoji和颜色
        level_emoji = {
            'info': '📋',
            'success': '✅',
            'warning': '⚠️',
            'error': '❌'
        }
        emoji = level_emoji.get(level, '📋')

        # 设置颜色
        level_colors = {
            'info': QColor(148, 163, 184),    # 灰色
            'success': QColor(34, 197, 94),   # 绿色
            'warning': QColor(251, 191, 36),  # 黄色
            'error': QColor(239, 68, 68),    # 红色
        }
        text_color = level_colors.get(level, QColor(148, 163, 184))

        # 添加日志消息（使用HTML格式支持颜色）
        time_color = QColor(96, 165, 250)  # 蓝色
        color_hex = text_color.name()
        time_hex = time_color.name()

        # 使用HTML格式实现颜色
        log_html = f'<span style="color: {time_hex};">[{timestamp}]</span> {emoji} <span style="color: {color_hex};">{message}</span>'
        self.log_text.appendHtml(log_html)

        # 自动滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_processing_state(self, processing: bool):
        """设置处理状态（保留用于兼容性）"""
        self.update_button_states()

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
        self.debug_mode_checkbox.setChecked(self.config.getboolean('Output', 'debug_mode', True))

        # 加载UI设置（移除对file_path_edit的引用）
        width = self.config.getint('UI', 'window_width', 900)  # 稍微加宽以容纳新表格
        height = self.config.getint('UI', 'window_height', 700)
        self.resize(width, height)

    def closeEvent(self, event):
        """窗口关闭事件"""
        # 保存窗口大小
        self.config.update_window_size(self.width(), self.height())

        # 停止批量处理
        if self.batch_processor.state != 'IDLE':
            reply = QMessageBox.question(
                self, '确认退出',
                '批量处理正在进行中，确定要退出吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.batch_processor.stop()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

        # 清理线程
        if hasattr(self.batch_processor, '_current_thread') and self.batch_processor._current_thread:
            if self.batch_processor._current_thread.isRunning():
                self.batch_processor._current_thread.quit()
                self.batch_processor._current_thread.wait()
                self.batch_processor._current_thread.deleteLater()


def main():
    app = QApplication(sys.argv)
    window = SubtitleGeneratorGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()