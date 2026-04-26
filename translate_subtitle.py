import os
import sys
import subprocess
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from transcribe import create_pipeline, transcribe_audio
from llm_client import create_llm_client, translate_text


def extract_audio(video_path: str, output_dir: str) -> str:
    """用 ffmpeg 从视频中提取音频为 WAV"""
    video_name = Path(video_path).stem
    audio_path = os.path.join(output_dir, f"{video_name}_audio.wav")

    print(f"[Audio] Extracting audio from {video_path}")
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", audio_path,
        "-y",  # overwrite without asking
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[Audio] ffmpeg error:\n{result.stderr}")
        sys.exit(1)

    print(f"[Audio] Audio saved to {audio_path}")
    return audio_path


def format_srt_timestamp(seconds: float) -> str:
    """秒数转 SRT 时间戳 HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(chunks: list, translated_texts: list, output_path: str):
    """生成 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, (chunk, translation) in enumerate(zip(chunks, translated_texts), 1):
            start = chunk["timestamp"][0]
            end = chunk["timestamp"][1]
            # Skip empty chunks
            if not chunk["text"].strip():
                continue

            f.write(f"{i}\n")
            f.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")
            f.write(f"{chunk['text']}\n")
            f.write(f"{translation}\n\n")

    print(f"[SRT] Subtitle file saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Kotoba Whisper 转录 + LLM翻译 → SRT字幕")
    parser.add_argument("input", help="输入视频文件路径（mp4/mkv等）或音频文件路径（wav/mp3等）")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录，默认为输入文件所在目录")
    parser.add_argument("--model", default="kotoba-tech/kotoba-whisper-v2.1", help="Whisper 模型 ID")
    parser.add_argument("--llm-model", default="hy-mt1.5-1.8b", help="本地 LLM 模型名称")
    parser.add_argument("--no-translate", action="store_true", help="跳过翻译，只生成日文字幕")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"[Error] File not found: {input_path}")
        sys.exit(1)

    # Determine output directory
    output_dir = args.output_dir or str(Path(input_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Extract audio if input is video
    ext = Path(input_path).suffix.lower()
    if ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"):
        audio_file = extract_audio(input_path, output_dir)
    else:
        audio_file = input_path

    # Step 2: Transcribe with Kotoba-Whisper
    print("\n[Step 1/3] Transcribing...")
    pipe = create_pipeline(args.model)
    result = transcribe_audio(pipe, audio_file)

    raw_text = result["text"]
    chunks = result.get("chunks", [])

    # Step 3: Translate with local LLM (optional)
    if args.no_translate:
        print("\n[Step 2/3] Skipping translation (--no-translate enabled)")
        translated_texts = ["" for _ in chunks]
    else:
        print("\n[Step 2/3] Translating...")
        try:
            llm_client = create_llm_client()
            translated_texts = []
            for i, chunk in enumerate(chunks):
                if not chunk["text"].strip():
                    translated_texts.append("")
                    continue
                try:
                    translation = translate_text(llm_client, chunk["text"], args.llm_model)
                    translated_texts.append(translation)
                    print(f"  [{i+1}/{len(chunks)}] {chunk['text'][:30]} → {translation[:30]}")
                except Exception as e:
                    print(f"  [{i+1}/{len(chunks)}] Translation failed: {e}")
                    translated_texts.append("")
        except Exception as e:
            print(f"[Error] LLM translation failed: {e}")
            print("[Fallback] Proceeding without translation...")
            translated_texts = ["" for _ in chunks]

    # Step 4: Generate SRT
    print("\n[Step 3/3] Generating SRT...")
    video_name = Path(input_path).stem
    srt_path = os.path.join(output_dir, f"{video_name}_subtitle.srt")
    generate_srt(chunks, translated_texts, srt_path)

    # Also save raw transcription as txt
    txt_path = os.path.join(output_dir, f"{video_name}_transcription.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"Original (Japanese):\n{raw_text}\n\n")
        f.write("Translation (Chinese):\n")
        for chunk, trans in zip(chunks, translated_texts):
            if chunk["text"].strip():
                start = format_srt_timestamp(chunk["timestamp"][0])
                end = format_srt_timestamp(chunk["timestamp"][1])
                f.write(f"[{start} --> {end}] {chunk['text']}\n  → {trans}\n\n")

    print(f"\n[DONE] All files saved to: {output_dir}")


if __name__ == "__main__":
    main()
