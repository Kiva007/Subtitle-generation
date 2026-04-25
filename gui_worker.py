import os
import sys
import subprocess
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# 导入必要的库（PyTorch 已在 gui_main.py 主线程中加载）
import torch
from transformers import pipeline
from openai import OpenAI
import stable_whisper


class SubtitleWorker(QThread):
    # 信号定义
    progress_update = pyqtSignal(int, str)  # 进度百分比, 当前状态描述
    log_message = pyqtSignal(str)  # 日志消息
    task_completed = pyqtSignal(bool, str)  # 完成状态, 结果消息
    error_occurred = pyqtSignal(str)  # 错误消息

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.is_cancelled = False

    def run(self):
        """执行字幕生成任务"""
        try:
            input_path = self.params['input_file']
            output_dir = self.params['output_dir']
            whisper_model = self.params['whisper_model']
            translation_model = self.params['translation_model']
            lm_url = self.params['lm_studio_url']
            output_formats = self.params['output_formats']  # {'original': bool, 'translated': bool, 'bilingual': bool}

            self.log_message.emit(f"开始处理: {input_path}")
            self.progress_update.emit(5, "准备处理...")

            # 检查文件是否存在
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"文件不存在: {input_path}")

            # Step 1: 提取音频（如果需要）
            ext = Path(input_path).suffix.lower()
            if ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"):
                audio_file = self._extract_audio(input_path, output_dir)
            else:
                audio_file = input_path
                self.log_message.emit(f"使用输入音频文件: {audio_file}")

            # Step 2: 转录
            self.progress_update.emit(20, "加载Whisper模型...")
            pipe = self._create_pipeline(whisper_model)

            self.progress_update.emit(30, "开始转录...")
            result = self._transcribe_audio(pipe, audio_file)

            raw_text = result["text"]
            chunks = result.get("chunks", [])
            self.log_message.emit(f"转录完成: {len(chunks)} 个片段")

            # Step 3: 翻译（如果需要）
            translated_texts = []
            if output_formats['translated'] or output_formats['bilingual']:
                if self.is_cancelled:
                    raise Exception("任务已取消")

                self.progress_update.emit(50, "连接翻译模型...")
                llm_client = self._create_llm_client(lm_url)

                self.progress_update.emit(60, "开始翻译...")
                translated_texts = self._translate_chunks(llm_client, chunks, translation_model)
            else:
                translated_texts = [""] * len(chunks)

            if self.is_cancelled:
                raise Exception("任务已取消")

            # Step 4: 生成字幕文件
            self.progress_update.emit(80, "生成字幕文件...")
            video_name = Path(input_path).stem
            self._generate_subtitle_files(chunks, translated_texts, output_dir, video_name, output_formats)

            self.progress_update.emit(100, "完成!")
            self.log_message.emit(f"所有文件已保存到: {output_dir}")
            self.task_completed.emit(True, "字幕生成完成!")

        except Exception as e:
            self.error_occurred.emit(f"处理失败: {str(e)}")
            self.task_completed.emit(False, f"错误: {str(e)}")

    def cancel(self):
        """取消任务"""
        self.is_cancelled = True
        self.log_message.emit("正在取消任务...")

    def _extract_audio(self, video_path: str, output_dir: str) -> str:
        """从视频中提取音频"""
        if self.is_cancelled:
            raise Exception("任务已取消")

        video_name = Path(video_path).stem
        audio_path = os.path.join(output_dir, f"{video_name}_audio.wav")

        self.log_message.emit(f"正在提取音频...")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", audio_path,
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg错误: {result.stderr}")

        self.log_message.emit(f"音频已保存: {audio_path}")
        return audio_path

    def _create_pipeline(self, model_id: str):
        """创建Whisper转录管线"""
        if self.is_cancelled:
            raise Exception("任务已取消")

        # 使用transcribe模块中的函数，避免重复导入torch
        from transcribe import create_pipeline
        return create_pipeline(model_id)

    def _transcribe_audio(self, pipe, audio_file: str) -> dict:
        """转录音频文件"""
        if self.is_cancelled:
            raise Exception("任务已取消")

        # 使用transcribe模块中的函数
        from transcribe import transcribe_audio
        return transcribe_audio(pipe, audio_file)

    def _create_llm_client(self, base_url: str) -> OpenAI:
        """创建LLM客户端"""
        return OpenAI(base_url=base_url, api_key="lm-studio")

    def _translate_chunks(self, client: OpenAI, chunks: list, model: str) -> list:
        """翻译所有文本片段"""
        translated_texts = []
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            if self.is_cancelled:
                raise Exception("任务已取消")

            if not chunk["text"].strip():
                translated_texts.append("")
                continue

            try:
                translation = self._translate_text(client, chunk["text"], model)
                translated_texts.append(translation)

                # 更新进度
                progress = 60 + int((i + 1) / total * 20)
                self.progress_update.emit(progress, f"翻译中... [{i+1}/{total}]")
                self.log_message.emit(f"[{i+1}/{total}] {chunk['text'][:30]} → {translation[:30]}")

            except Exception as e:
                self.log_message.emit(f"[{i+1}/{total}] 翻译失败: {e}")
                translated_texts.append("")

        return translated_texts

    def _translate_text(self, client: OpenAI, text: str, model: str) -> str:
        """翻译单段文本"""
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "这是一个对话场景，你是一个专业的日语→中文翻译助手。只输出翻译结果，不要解释、不要额外内容。保持原文的口语风格和语气。",
                },
                {"role": "user", "content": text},
            ],
        )
        return response.choices[0].message.content.strip()

    def _generate_subtitle_files(self, chunks: list, translated_texts: list, output_dir: str, video_name: str, formats: dict):
        """生成不同格式的字幕文件"""
        if self.is_cancelled:
            raise Exception("任务已取消")

        # 生成原文字幕
        if formats['original']:
            original_srt = os.path.join(output_dir, f"{video_name}_original.srt")
            self._write_srt_file(original_srt, chunks, None, "original")
            self.log_message.emit(f"原文字幕已生成: {original_srt}")

        # 生成中文字幕
        if formats['translated']:
            translated_srt = os.path.join(output_dir, f"{video_name}_translated.srt")
            self._write_srt_file(translated_srt, chunks, translated_texts, "translated")
            self.log_message.emit(f"中文字幕已生成: {translated_srt}")

        # 生成双语字幕
        if formats['bilingual']:
            bilingual_srt = os.path.join(output_dir, f"{video_name}_bilingual.srt")
            self._write_srt_file(bilingual_srt, chunks, translated_texts, "bilingual")
            self.log_message.emit(f"双语字幕已生成: {bilingual_srt}")

        # 生成原始转录文本
        txt_path = os.path.join(output_dir, f"{video_name}_transcription.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Original (Japanese):\n{chunks[0] if chunks else ''}\n")
            for chunk in chunks:
                if chunk["text"].strip():
                    start = self._format_srt_timestamp(chunk["timestamp"][0])
                    end = self._format_srt_timestamp(chunk["timestamp"][1])
                    f.write(f"[{start} --> {end}] {chunk['text']}\n")
        self.log_message.emit(f"转录文本已生成: {txt_path}")

    def _write_srt_file(self, output_path: str, chunks: list, translated_texts: list, format_type: str):
        """写入SRT文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, chunk in enumerate(chunks, 1):
                if not chunk["text"].strip():
                    continue

                start = chunk["timestamp"][0]
                end = chunk["timestamp"][1]

                f.write(f"{i}\n")
                f.write(f"{self._format_srt_timestamp(start)} --> {self._format_srt_timestamp(end)}\n")

                if format_type == "original":
                    f.write(f"{chunk['text']}\n")
                elif format_type == "translated" and translated_texts:
                    f.write(f"{translated_texts[i-1]}\n")
                elif format_type == "bilingual" and translated_texts:
                    f.write(f"{chunk['text']}\n")
                    f.write(f"{translated_texts[i-1]}\n")

                f.write("\n")

    def _format_srt_timestamp(self, seconds: float) -> str:
        """秒数转SRT时间戳格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"