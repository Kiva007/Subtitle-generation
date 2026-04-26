# 延迟导入重型库，避免启动时DLL加载问题
torch = None
pipeline = None

def _ensure_libraries_loaded():
    """确保必要的库已加载"""
    global torch, pipeline
    if torch is None:
        import torch
    if pipeline is None:
        from transformers import pipeline

    # 确保stable_whisper可用
    import stable_whisper


def create_pipeline(model_id: str = "kotoba-tech/kotoba-whisper-v2.1"):
    """创建 Kotoba-Whisper 转录管线"""
    _ensure_libraries_loaded()

    torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model_kwargs = {"attn_implementation": "sdpa"} if torch.cuda.is_available() else {}

    print(f"[转录] Using device: {device}")
    print(f"[转录] Loading model: {model_id}")

    pipe = pipeline(
        model=model_id,
        dtype=torch_dtype,
        device=device,
        model_kwargs=model_kwargs,
        trust_remote_code=True,
        punctuator=True,
    )

    print("[转录] 模型加载成功，正在转录中")
    return pipe


def transcribe_audio(pipe, audio_file: str) -> dict:
    """转录音频文件，返回包含 text 和 chunks 的结果"""
    _ensure_libraries_loaded()
    generate_kwargs = {"language": "ja", "task": "transcribe"}
    result = pipe(audio_file, return_timestamps=True, generate_kwargs=generate_kwargs)
    return result
