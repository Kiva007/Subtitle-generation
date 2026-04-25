from openai import OpenAI


def create_llm_client(
    base_url: str = "http://127.0.0.1:1234/v1",
    api_key: str = "lm-studio",
) -> OpenAI:
    """创建本地 LLM 客户端（LM Studio）"""
    return OpenAI(base_url=base_url, api_key=api_key)


def translate_text(client: OpenAI, text: str, model: str = "qwen3.5-2b") -> str:
    """使用本地模型将日文翻译为中文"""
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
