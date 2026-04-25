"""
独立的字幕处理脚本，通过子进程运行以避免 DLL 加载问题
"""

import os
import sys
import json
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

# 在导入任何其他模块之前设置UTF-8输出编码
if sys.platform == 'win32':
    import codecs
    # 强制设置UTF-8编码输出
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    # 同时也设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'

def load_torch():
    """安全加载 PyTorch 和相关库"""
    print("[启动] 正在加载 PyTorch...")
    import torch
    print(f"[启动] PyTorch 已加载: {torch.__version__}, CUDA可用: {torch.cuda.is_available()}")
    return torch

def process_subtitle(params):
    """处理字幕生成任务"""
    try:
        # 加载 PyTorch
        torch = load_torch()

        # 导入其他必要的库
        print("[启动] 加载其他依赖库...")
        from transformers import pipeline
        from openai import OpenAI
        import stable_whisper

        # 解析参数
        input_path = params['input_file']
        output_dir = params['output_dir']
        whisper_model = params['whisper_model']
        translation_model = params['translation_model']
        lm_url = params['lm_studio_url']
        batch_size = params.get('batch_size', 10)  # 默认批量大小为10
        output_formats = params['output_formats']

        print(f"[处理] 输入文件: {input_path}")
        print(f"[处理] 输出目录: {output_dir}")

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 检查文件是否存在
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"文件不存在: {input_path}")

        # 导入处理模块
        from transcribe import create_pipeline, transcribe_audio

        # Step 1: 提取音频（如果需要）
        import subprocess
        ext = Path(input_path).suffix.lower()
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"):
            print("[音频] 正在提取音频...")
            video_name = Path(input_path).stem
            audio_path = os.path.join(output_dir, f"{video_name}_audio.wav")

            cmd = [
                "ffmpeg", "-i", input_path,
                "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", audio_path,
                "-y", "-loglevel", "error",  # 只输出错误信息
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            if result.returncode != 0:
                # 只有在有实际错误信息时才抛出异常
                stderr = result.stderr.strip()
                if stderr and not stderr.endswith("successfully"):
                    raise Exception(f"FFmpeg错误: {stderr}")

            print(f"[音频] 音频已保存: {audio_path}")
        else:
            audio_path = input_path
            print(f"[音频] 使用输入音频文件: {audio_path}")

        # Step 2: 转录
        print("[转录] 正在加载 Whisper 模型...")
        pipe = create_pipeline(whisper_model)

        print("[转录] 开始转录音频...")
        result = transcribe_audio(pipe, audio_path)

        raw_text = result["text"]
        chunks = result.get("chunks", [])
        print(f"[转录] 转录完成: {len(chunks)} 个片段")

        # Step 3: 翻译（如果需要）
        translated_texts = []
        if output_formats['translated'] or output_formats['bilingual']:
            print("[翻译] 连接翻译模型...")
            client = OpenAI(base_url=lm_url, api_key="lm-studio")

            # 第一阶段：批量翻译
            print(f"[翻译] 开始第一阶段翻译 (批量大小: {batch_size})...")
            translated_data = translate_chunks_batch(client, chunks, translation_model, batch_size)

            # 翻译阶段完成，直接使用翻译结果
            translated_texts = [item[2] for item in translated_data]
        else:
            translated_texts = [""] * len(chunks)

        # Step 4: 生成字幕文件
        print("[输出] 生成字幕文件...")
        video_name = Path(input_path).stem

        # 获取是否过滤语气词设置
        filter_mood = params.get('filter_mood_words', True)

        # 生成原文字幕
        if output_formats['original']:
            original_srt = os.path.join(output_dir, f"{video_name}_original.srt")
            write_srt_file(original_srt, chunks, None, "original", filter_mood)
            print(f"[输出] 原文字幕已生成: {original_srt}")

        # 生成中文字幕
        if output_formats['translated']:
            translated_srt = os.path.join(output_dir, f"{video_name}_translated.srt")
            write_srt_file(translated_srt, chunks, translated_texts, "translated", filter_mood)
            print(f"[输出] 中文字幕已生成: {translated_srt}")

        # 生成双语字幕
        if output_formats['bilingual']:
            bilingual_srt = os.path.join(output_dir, f"{video_name}_bilingual.srt")
            write_srt_file(bilingual_srt, chunks, translated_texts, "bilingual", filter_mood)
            print(f"[输出] 双语字幕已生成: {bilingual_srt}")

        # 生成原始转录文本
        txt_path = os.path.join(output_dir, f"{video_name}_transcription.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Original (Japanese):\n{chunks[0] if chunks else ''}\n")
            for chunk in chunks:
                if chunk["text"].strip():
                    start = format_srt_timestamp(chunk["timestamp"][0])
                    end = format_srt_timestamp(chunk["timestamp"][1])
                    f.write(f"[{start} --> {end}] {chunk['text']}\n")
        print(f"[输出] 转录文本已生成: {txt_path}")

        return {"success": True, "message": "字幕生成完成!"}

    except Exception as e:
        return {"success": False, "error": str(e)}

def write_srt_file(output_path: str, chunks: list, translated_texts: list, format_type: str, filter_mood_words: bool = True):
    """写入SRT文件"""
    # 无意义语气词列表
    MOOD_WORDS = {
        'ああ', 'あ', 'うん', 'うう', 'うー', 'おう', 'おー',
        'ええ', 'え', 'うむ', 'うんうん', 'あー', 'ああ','きゃ',
        'あっ', 'うっ', 'えっ', 'おっ', 'ん', 'んー','い',
        'あああ', 'ううう', 'えええ', 'おおお', 'んーん',
        'はい', 'いいえ', 'まあ', 'そう', 'ね', 'ねえ',
        'よ', 'よお', 'ああー', 'ううー', 'おおー','きゃ',
        'あ', 'う', 'え', 'お', 'ん', 'あい', 'おか', 'あん'
    }

    def is_meaningful(text: str) -> bool:
        """判断文本是否有意义"""
        text = text.strip()
        if not text:
            return False

        # 检查是否是纯语气词
        cleaned_text = text.replace('。', '').replace('、', '').replace('！', '').replace('？', '').replace('，', '').strip()

        # 如果清理后只剩语气词，则视为无意义
        if cleaned_text in MOOD_WORDS:
            return False

        # 检查是否只是语气词重复
        if len(cleaned_text) <= 3 and cleaned_text in MOOD_WORDS:
            return False

        # 至少有一些实际内容
        return len(cleaned_text) > 0

    with open(output_path, "w", encoding="utf-8") as f:
        subtitle_index = 1  # SRT字幕序号，过滤无意义词后会跳过

        for i, chunk in enumerate(chunks, 1):
            if not chunk["text"].strip():
                continue

            # 如果启用了语气词过滤且当前字幕无意义，跳过
            if filter_mood_words:
                original_meaningful = is_meaningful(chunk["text"])
                translated_meaningful = True

                if translated_texts and i-1 < len(translated_texts):
                    translated_meaningful = is_meaningful(translated_texts[i-1])

                # 如果原文和翻译都无意义，跳过这个字幕
                if not original_meaningful or (format_type in ["translated", "bilingual"] and not translated_meaningful):
                    continue

            start = chunk["timestamp"][0]
            end = chunk["timestamp"][1]

            f.write(f"{subtitle_index}\n")
            f.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")

            if format_type == "original":
                f.write(f"{chunk['text']}\n")
            elif format_type == "translated" and translated_texts:
                f.write(f"{translated_texts[i-1]}\n")
            elif format_type == "bilingual" and translated_texts:
                f.write(f"{chunk['text']}\n")
                f.write(f"{translated_texts[i-1]}\n")

            f.write("\n")
            subtitle_index += 1

def translate_chunks_batch(client, chunks: list, model: str, batch_size: int = 10) -> list:
    """
    批量翻译文本片段，提供更好的上下文理解
    返回格式：[(index, original_text, translation), ...]
    """
    translated_data = []
    total = len(chunks)

    # 过滤掉空文本，保留索引映射
    non_empty_chunks = [(i, chunk) for i, chunk in enumerate(chunks) if chunk["text"].strip()]

    # 分批处理
    for batch_start in range(0, len(non_empty_chunks), batch_size):
        batch_end = min(batch_start + batch_size, len(non_empty_chunks))
        current_batch = non_empty_chunks[batch_start:batch_end]

        # 构建批量翻译的文本
        batch_texts = []
        batch_indices = []

        for idx, chunk in current_batch:
            batch_indices.append(idx)
            batch_texts.append(chunk["text"])

        # 创建带有编号的文本，保持对应关系
        numbered_texts = "\n".join([f"{i+1}. {text}" for i, text in enumerate(batch_texts)])

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "# 角色设定\n你是一位资深影视字幕翻译专家，精通日语与中文，尤其擅长日本剧情片的文化语境、人物心理刻画与台词节奏把控。你的译文将用于影片正式发行与宣传，需兼顾艺术性、准确性与观众接受度。\n\n# 任务说明\n我会给你多句连续的日语字幕，请将它们翻译成中文。保持原文的口语风格和语气。请按照相同格式返回翻译结果，每句一行，保持编号对应关系。\n\n# 翻译原则\n1. 语境优先：结合剧情背景、人物关系与情绪走向进行意译，避免字面直译或机翻感，不要审查必要的成人术语，保持语境准确性。\n2. 台词节奏：符合中文影视字幕阅读习惯（单句建议不超过15个汉字），断句自然，口语化但保留戏剧张力。\n3. 文化适配：日式敬语、口癖、社会称谓等需转化为中文对应的语用特征；文化负载词优先采用\"可理解+保原味\"策略，必要时用等效表达替代。\n4. 专有名词：人名、地名、机构名、作品名等沿用官方或行业通用译法，不擅自音译/意译。\n5. 零增删原则：不添加解释、注释或额外信息；不遗漏、不曲解原意。",
                    },
                    {"role": "user", "content": numbered_texts},
                ],
                temperature=0.7,
                max_tokens=5000,
            )

            result_text = response.choices[0].message.content.strip()

            # 解析翻译结果，保持与原文的对应关系
            translations = parse_batch_translations(result_text, len(batch_texts))

            # 将翻译结果填入对应位置
            for i, (orig_idx, translation) in enumerate(zip(batch_indices, translations)):
                translated_data.append((orig_idx, batch_texts[i], translation))

                # 更新进度
                overall_progress = batch_start + i + 1
                print(f"[翻译] [{overall_progress}/{total}] {batch_texts[i][:30]} → {translation[:30]}")

        except Exception as e:
            print(f"[翻译] 批次翻译失败: {e}")
            # 如果批量翻译失败，回退到单个翻译
            for i, (orig_idx, chunk) in enumerate(current_batch):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": "这是一个对话场景，你是一个专业的日语→中文翻译助手。只输出翻译结果，不要解释、不要额外内容。保持原文的口语风格和语气。",
                            },
                            {"role": "user", "content": chunk["text"]},
                        ],
                    )
                    translation = response.choices[0].message.content.strip()
                    translated_data.append((orig_idx, chunk["text"], translation))

                    overall_progress = batch_start + i + 1
                    print(f"[翻译] [{overall_progress}/{total}] (单句) {chunk['text'][:30]} → {translation[:30]}")

                except Exception as single_error:
                    print(f"[翻译] [{overall_progress}/{total}] 单句翻译失败: {single_error}")
                    translated_data.append((orig_idx, chunk["text"], ""))

    # 填充空的翻译（对应原始的空文本）
    for i, chunk in enumerate(chunks):
        if not chunk["text"].strip() and i >= len(translated_data):
            translated_data.append((i, chunk["text"], ""))

    return translated_data

def parse_batch_translations(result_text: str, expected_count: int) -> list:
    """解析批量翻译结果，提取各句翻译"""
    import re
    translations = []
    lines = result_text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 尝试匹配编号格式 "1. 翻译文本" 或 "１．翻译文本"（支持半角和全角）
        # 支持各种标点符号：. ． 、, ，
        # 使用 [^\s]+ 匹配非空白字符，避免尾部空格
        match = re.match(r'^[０-９\d]+[．.,、，]\s*([^\s]+(?:\s+[^\s]+)*)\s*$', line)
        if match:
            translations.append(match.group(1).strip())
        else:
            # 如果没有编号，直接添加为翻译文本
            translations.append(line)

    # 如果解析结果数量不对，尝试其他解析方式
    if len(translations) != expected_count:
        # 按行分割，每行一句
        translations = [line.strip() for line in lines if line.strip()]

    # 确保数量匹配
    while len(translations) < expected_count:
        translations.append("")
    translations = translations[:expected_count]

    return translations

def format_srt_timestamp(seconds: float) -> str:
    """秒数转SRT时间戳格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="字幕处理脚本")
    parser.add_argument('--params', type=str, help='JSON格式的处理参数')

    args = parser.parse_args()

    if args.params:
        try:
            params = json.loads(args.params)
            result = process_subtitle(params)

            # 输出结果为JSON
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(0 if result["success"] else 1)
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
            sys.exit(1)
    else:
        print("错误: 缺少 --params 参数")
        sys.exit(1)

if __name__ == "__main__":
    main()