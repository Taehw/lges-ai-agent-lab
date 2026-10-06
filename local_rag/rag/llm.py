from __future__ import annotations

import os
from typing import Any

from .bootstrap import bootstrap_llama_cpp
from .config import (
    BASE_DIR,
    LLM_MAX_TOKENS,
    LLM_MODEL_PATH,
    LLM_N_CTX,
    LLM_TEMPERATURE,
    SYSTEM_PROMPT,
)


class LocalExaoneLLM:
    """EXAONE GGUF wrapper via llama-cpp-python."""

    def __init__(self, model_path: str | None = None) -> None:
        bootstrap_llama_cpp(BASE_DIR)
        from llama_cpp import Llama

        path = str(model_path or LLM_MODEL_PATH)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"LLM GGUF 파일을 찾을 수 없습니다: {path}")

        n_threads = max(2, (os.cpu_count() or 4) - 1)
        self.client = Llama(
            model_path=path,
            n_ctx=LLM_N_CTX,
            n_threads=n_threads,
            n_batch=256,
            n_gpu_layers=0,
            verbose=False,
        )

    def generate(
        self,
        user_content: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        prompt = self._format_prompt(
            user_content=user_content,
            history=history or [],
            system_prompt=system_prompt or SYSTEM_PROMPT,
        )
        result: dict[str, Any] = self.client(
            prompt,
            max_tokens=LLM_MAX_TOKENS,
            temperature=LLM_TEMPERATURE,
            top_p=0.9,
            repeat_penalty=1.1,
            stop=["[|endofturn|]", "[|user|]", "[|system|]"],
        )
        text = result["choices"][0]["text"].strip()
        return text

    @staticmethod
    def _format_prompt(
        user_content: str,
        history: list[dict[str, str]],
        system_prompt: str,
    ) -> str:
        parts = [f"[|system|]{system_prompt}[|endofturn|]"]
        for message in history:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                parts.append(f"[|user|]{content}[|endofturn|]")
            elif role == "assistant":
                parts.append(f"[|assistant|]{content}[|endofturn|]")
        parts.append(f"[|user|]{user_content}[|endofturn|]")
        parts.append("[|assistant|]")
        return "".join(parts)
