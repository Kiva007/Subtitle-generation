# Subtitle Generator GUI - 使用说明

## 功能特性

- 支持从视频/音频文件提取音频
- 使用Kotoba-Whisper进行日语转录
- 集成LM Studio进行日语到中文翻译
- 支持多种字幕格式输出（原文字幕、中文字幕、双语字幕）
- 实时进度显示和日志输出
- 配置保存和恢复

## 使用方法

### 1. 启动应用

```bash
cd "Subtitle generation"
python gui_main.py
```

### 2. 操作步骤

#### 选择文件
- 点击"选择视频/音频文件"按钮选择输入文件
- 支持格式：MP4, MKV, AVI, MOV, FLV, WMV, WAV, MP3, M4A, FLAC
- 可选择输出目录，默认为输入文件所在目录

#### 配置模型
- **Whisper模型**：默认为 `kotoba-tech/kotoba-whisper-v2.1`
- **翻译模型**：从LM Studio获取可用模型列表
- **LM Studio**：默认地址 `http://127.0.0.1:1234/v1`
- 点击"刷新模型列表"可更新可用翻译模型

#### 选择输出格式
- ☑️ 原文字幕 (日语)：生成包含日语文本的SRT文件
- ☑️ 中文字幕：生成包含中文翻译的SRT文件
- ☑️ 双语字幕：生成包含日语和中文的SRT文件

#### 开始处理
- 点击"开始处理"按钮启动任务
- 可通过"停止"按钮中断处理
- "重置"按钮清空当前设置

### 3. 输出文件

处理完成后，会在输出目录生成以下文件：

- `[filename]_original.srt` - 原文字幕（日语）
- `[filename]_translated.srt` - 中文字幕
- `[filename]_bilingual.srt` - 双语字幕
- `[filename]_transcription.txt` - 原始转录文本

## 依赖要求

- Python 3.8+
- PyQt6
- PyTorch (CUDA支持)
- Transformers
- Kotoba-Whisper
- LM Studio (本地运行)

### 安装依赖

```bash
pip install PyQt6 transformers accelerate torchaudio
pip install stable-ts==2.16.0
pip install punctuators==0.0.5
pip install openai
```

## 配置文件

GUI配置保存在 `gui_config.ini` 文件中，包含：
- 模型设置
- 输出格式偏好
- 窗口大小和布局
- 最后使用的文件路径

## 故障排除

### 模型加载失败
- 检查网络连接（首次加载需要下载模型）
- 确认CUDA是否正确安装
- 检查LM Studio是否正在运行

### 翻译失败
- 确认LM Studio服务地址正确
- 点击"刷新模型列表"检查可用模型
- 查看日志了解具体错误信息

### 音频提取失败
- 确认FFmpeg已安装并添加到PATH
- 检查输入文件格式是否支持

## 技术架构

- **GUI框架**: PyQt6
- **异步处理**: QThread + Signal/Slot
- **转录引擎**: Kotoba-Whisper v2.1
- **翻译引擎**: LM Studio本地模型
- **音频处理**: FFmpeg

## 命令行版本

如需使用命令行版本，可运行：

```bash
python translate_subtitle.py "input_file.mp4" --output-dir "output_folder"
```

参数说明：
- `--model`: Whisper模型ID（默认：kotoba-tech/kotoba-whisper-v2.1）
- `--llm-model`: 翻译模型名称（默认：qwen3.5-2b）
- `--no-translate`: 跳过翻译，只生成日文字幕
- `-o, --output-dir`: 输出目录

## 注意事项

1. 首次运行需要下载Whisper模型，可能需要较长时间
2. 确保LM Studio已启动并加载了合适的翻译模型
3. 大型视频文件处理时间较长，请耐心等待
4. 翻译质量取决于本地模型的能力和配置

## 性能优化

- 使用CUDA加速转录和推理
- 批量翻译提高效率
- 合理设置模型参数平衡速度和质量