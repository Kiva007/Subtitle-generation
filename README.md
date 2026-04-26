# Subtitle Generator - 日语视听资源字幕生成工具

一个完整的日语视频/音频转录翻译工具，支持从日语视听资源自动生成中文字幕。

## 功能特性

- **音频提取**：自动从视频文件提取音频（支持MP4、MKV、AVI等格式）
- **精准转录**：使用Kotoba-Whisper v2.1进行日语语音识别
- **智能翻译**：集成LM Studio本地大模型进行日语→中文翻译
- **多种输出**：支持原文字幕、中文字幕、双语字幕三种格式
- **实时反馈**：带时间戳的日志显示和进度追踪
- **智能过滤**：自动过滤无意义的语气词（ああ、うん等）
- **批量优化**：批量翻译提供更好的上下文理解
- **配置持久化**：自动保存用户设置和偏好

## 快速开始

### 前置要求

1. **Python 3.8+**
2. **FFmpeg**：用于音频提取
3. **LM Studio**：本地运行翻译模型
4. **CUDA环境**：用于GPU加速（推荐）

### 安装

```bash
# 安装依赖
pip install PyQt6 transformers accelerate torchaudio
pip install stable-ts==2.16.0
pip install punctuators==0.0.5
pip install openai requests

# 启动GUI
python gui_main.py
```

### 使用步骤

1. **启动LM Studio** 并加载翻译模型（推荐：`sakura-galtransl-7b-v3.7`）
2. **启动GUI** `python gui_main.py`
3. **选择文件** 点击"选择视频/音频文件"
4. **配置参数**：
   - Whisper模型：`kotoba-tech/kotoba-whisper-v2.1`（默认）
   - 翻译模型：选择已加载的模型（默认：`sakura-galtransl-7b-v3.7`）
   - 批量翻译大小：70（推荐）
5. **选择输出格式**：原文字幕 / 中文字幕 / 双语字幕
6. **开始处理** 点击"开始处理"按钮

## 输出文件

处理完成后生成以下文件：

```
[filename]_original.srt      # 日语原文字幕
[filename]_translated.srt    # 中文翻译字幕  
[filename]_bilingual.srt     # 日中双语字幕
[filename]_transcription.txt # 完整转录文本
```

## 推荐配置

### 翻译模型选择

经过测试，以下模型效果最佳：

| 模型 | 参数量 | 翻译质量 | 速度 | 推荐场景 |
|------|--------|---------|------|----------|
| **sakura-galtransl-7b-v3.7** | 7B | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | **推荐默认使用** |
| hy-mt1.5-1.8b | 1.8B | ⭐⭐ | ⭐⭐⭐⭐⭐ | 快速翻译（质量较低） |
| qwen2.5-7b-instruct | 7B | ⭐⭐⭐⭐ | ⭐⭐⭐ | 通用翻译 |

**重要提示**：小参数模型（如1.5B-1.8B）可能出现翻译失败（直接返回日语原文），建议使用7B以上参数模型。

### 性能优化建议

- **批量翻译大小**：
  - 推荐值：50-80条
  - 更大的值提供更好的上下文，但可能影响稳定性
  - 建议根据模型能力调整

- **语气词过滤**：默认开启，可保持字幕简洁

- **GPU加速**：确保CUDA正确安装以获得最佳性能

## 技术架构

```
┌─────────────┐
│   GUI层     │ PyQt6 + QProcess（子进程隔离）
└──────┬──────┘
       │
┌──────▼──────────────────────────────┐
│  音频提取（FFmpeg）                  │
│  - 视频转WAV                         │
│  - 16kHz采样率，单声道                │
└──────┬──────────────────────────────┘
       │
┌──────▼──────────────────────────────┐
│  转录引擎（Kotoba-Whisper v2.1）     │
│  - 日语语音识别                       │
│  - 时间戳对齐                        │
└──────┬──────────────────────────────┘
       │
┌──────▼──────────────────────────────┐
│  翻译引擎（LM Studio）               │
│  - 批量翻译优化                      │
│  - 上下文保持                        │
└──────┬──────────────────────────────┘
       │
┌──────▼──────────────────────────────┐
│  字幕生成（SRT格式）                 │
│  - 原文字幕                          │
│  - 翻译字幕                          │
│  - 双语字幕                          │
└─────────────────────────────────────┘
```

## 配置文件

GUI配置保存在 `gui_config.ini`：

```ini
[Model]
whisper_model = kotoba-tech/kotoba-whisper-v2.1
translation_model = sakura-galtransl-7b-v3.7
lm_studio_url = http://127.0.0.1:1234/v1

[Output]
output_dir = 
original_subtitle = True
translated_subtitle = True
bilingual_subtitle = True
filter_mood_words = True

[UI]
window_width = 800
window_height = 700
last_input_file = 
```

## 故障排除

### 模型加载失败

**症状**：转录阶段报错或卡住

**解决方案**：
- 检查网络连接（首次需要下载模型）
- 确认CUDA正确安装：`python -c "import torch; print(torch.cuda.is_available())"`
- 查看错误日志定位具体问题

### 翻译返回日语原文

**症状**：中文字幕中出现日语内容

**原因**：翻译模型参数过小或能力不足

**解决方案**：
- 更换更大的模型（推荐 `sakura-galtransl-7b-v3.7`）
- 调整批量翻译大小（降低到30-50）
- 检查LM Studio是否正确加载模型

### LM Studio连接失败

**症状**：无法获取模型列表

**解决方案**：
- 确认LM Studio正在运行
- 检查服务地址（默认：`http://127.0.0.1:1234/v1`）
- 确认LM Studio已加载至少一个模型

### 音频提取失败

**症状**：FFmpeg错误

**解决方案**：
- 确认FFmpeg已安装：`ffmpeg -version`
- 添加FFmpeg到系统PATH环境变量
- 检查输入文件是否损坏

## 命令行版本

如需批量处理或脚本集成：

```bash
python translate_subtitle.py "input.mp4" --output-dir "output"
```

参数说明：
- `--model`: Whisper模型（默认：kotoba-tech/kotoba-whisper-v2.1）
- `--llm-model`: 翻译模型（默认：sakura-galtransl-7b-v3.7）
- `--no-translate`: 只转录不翻译
- `-o, --output-dir`: 输出目录

## 开发说明

### 项目结构

```
Subtitle generation/
├── gui_main.py              # GUI主程序
├── gui_config.py            # 配置管理
├── subprocess_processor.py  # 子进程处理逻辑
├── transcribe.py            # Whisper转录封装
├── llm_client.py            # LLM客户端封装
└── translate_subtitle.py    # 命令行版本
```

### 工作流程

1. **音频提取**：视频 → WAV（16kHz单声道）
2. **转录**：WAV → 日语文本 + 时间戳
3. **翻译**：日语字幕 → 中文字幕（批量处理）
4. **输出**：生成SRT和TXT文件

### 注意事项

- 子进程架构避免PyTorch DLL冲突
- UTF-8编码处理确保中文正确显示
- 异步处理提供流畅的UI响应

## 更新日志

### v1.0
- 基础转录和翻译功能
- GUI界面
- 配置保存
- 日志时间戳
- 智能模型选择

## 贡献

欢迎提交Issue和Pull Request！

## 许可证

本项目仅供学习和个人使用。
