"""
backends/llm.py — Large Language Model Abstraction Layer

Available backends (configured via LLM_BACKEND in .env):
  - ollama     : Mac M-series / local server        (current)
  - llama_cpp  : Linux / ARM / Arduino Uno Q       (migration)
  - openai     : Remote API (fallback / dev/test)

To migrate to the Arduino Uno Q platform :
  1. Change LLM_BACKEND=llama_cpp in config/kira.yaml
  2. Install : pip install llama-cpp-python
  3. Download a quantized GGUF model (e.g., Qwen2.5-3B-Q4_K_M.gguf)
  4. Update LLM_MODEL=path/to/model.gguf
  5. No other changes are needed in server.py

Recommended models by platform:
  Mac M1 Pro 32 GB   → qwen2.5:3b (tools + correct French)  via Ollama
  Mac M1 Pro 32 GB   → gemma3:4b  (fast but without tools)    via Ollama
  Arduino Uno Q 16 GB→ Qwen2.5-3B-Q4_K_M  via llama_cpp

Generation parameters in config/kira.yaml (defaults below) :
  LLM_TEMPERATURE=0.7      # 0.0=strict  0.7=natural  1.0=creative
  LLM_TOP_P=0.9            # token diversity
  LLM_REPEAT_PENALTY=1.1   # avoids repetitions
  LLM_NUM_PREDICT=80       # limits verbosity — key for speed!
  LLM_CTX=4096             # context window
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ──------------------------- Load config from kira.yaml if available ──-------------------------
# Fallback to default values if kira.yaml is absent
def _load_kira_config() -> dict:
    import yaml

    path = "config/kira.yaml"

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing required config file: {path}"
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if "llm" not in cfg:
        raise KeyError("Missing required section: llm")

    llm = cfg["llm"]

    return {
        "backend": llm["backend"],
        "model": llm["model"],
        "temperature": float(llm["temperature"]),
        "top_p": float(llm["top_p"]),
        "repeat_penalty": float(llm["repeat_penalty"]),
        "context_size": int(llm["context_size"]),
        "num_predict": int(llm["num_predict"]),
    }


_cfg = _load_kira_config()

LLM_BACKEND        = _cfg["backend"]
LLM_MODEL          = _cfg["model"]
LLM_TEMPERATURE    = _cfg["temperature"]
LLM_TOP_P          = _cfg["top_p"]
LLM_REPEAT_PENALTY = _cfg["repeat_penalty"]
LLM_CTX            = _cfg["context_size"]
LLM_NUM_PREDICT    = _cfg["num_predict"]

_backend_instance = None

def _load_backend():
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    if LLM_BACKEND == "ollama":
        _backend_instance = _OllamaBackend()
    elif LLM_BACKEND == "llama_cpp":
        _backend_instance = _LlamaCppBackend()
    elif LLM_BACKEND == "openai":
        _backend_instance = _OpenAIBackend()
    else:
        raise ValueError(f"Unknown LLM_BACKEND: '{LLM_BACKEND}'. "
                         f"Valid values: ollama, llama_cpp, openai")

    print(f"✅ LLM backend loaded: {LLM_BACKEND} ({LLM_MODEL})")
    return _backend_instance


# ──------------------------- Public interface (server.py only calls these two functions) ──-------------------------

def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """
    Sends a conversation to the LLM and returns a normalized dict:
    {
      "content"    : str,           # response text (can be empty if tool_calls)
      "tool_calls" : list | None,   # list of tool_calls or None
      "raw"        : ...            # raw response from the backend (for _execute_tools)
    }
    """
    return _load_backend().chat(messages, tools)


def backend_name() -> str:
    return LLM_BACKEND


# ──------------------------- Backend : Ollama (current — Mac M-series) ──-------------------------

class _OllamaBackend:
    def __init__(self):
        import ollama
        self._ollama = ollama

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        kwargs = {
            "model":    LLM_MODEL,
            "messages": messages,
            "options":  {
                "temperature":    LLM_TEMPERATURE,
                "top_p":          LLM_TOP_P,
                "repeat_penalty": LLM_REPEAT_PENALTY,
                "num_ctx":        LLM_CTX,
                "num_predict":    LLM_NUM_PREDICT,   # limite la verbosité
                "stop":           [".\n", "\n\n"], # stop dès fin de phrase naturelle
            }
        }
        if tools:
            kwargs["tools"] = tools

        resp = self._ollama.chat(**kwargs)

        # Normalisation
        tool_calls = None
        if hasattr(resp, "message") and resp.message.tool_calls:
            tool_calls = resp.message.tool_calls

        return {
            "content":    resp["message"]["content"] if not tool_calls else "",
            "tool_calls": tool_calls,
            "raw":        resp,
        }

    def chat_with_tool_result(self, messages: list[dict], tool_name: str, tool_result: str) -> str:
        """Second call after tool execution — natural reformulation."""
        import ollama
        resp = ollama.chat(
            model=LLM_MODEL,
            messages=messages,
            options={
                "temperature":    LLM_TEMPERATURE,
                "top_p":          LLM_TOP_P,
                "repeat_penalty": LLM_REPEAT_PENALTY,
                "num_ctx":        LLM_CTX,
                "num_predict":    LLM_NUM_PREDICT,
                "stop":           [".\n", "\n\n"],
            }
        )
        return resp["message"]["content"].strip()


# ──------------------------- Backend : llama-cpp-python (migration Arduino Uno Q) ──-------------------------
#
# Installation  : pip install llama-cpp-python
#
# Pour activer l'accélération NPU sur Arduino Uno Q (OpenCL / Vulkan) :
#   CMAKE_ARGS="-DLLAMA_CLBLAST=on" pip install llama-cpp-python --force-reinstall
#   ou : CMAKE_ARGS="-DLLAMA_VULKAN=on" pip install llama-cpp-python --force-reinstall
#
# Modèles GGUF recommandés (16 Go RAM) :
#   Qwen2.5-3B-Instruct-Q4_K_M.gguf  (~2 Go)  — excellent rapport qualité/vitesse
#   Phi-3-mini-4k-instruct-q4.gguf   (~2.3 Go) — très bon en français
#   mistral-7b-instruct-v0.3.Q4_K_M  (~4.1 Go) — si tu veux rester sur Mistral

class _LlamaCppBackend:
    def __init__(self):
        from llama_cpp import Llama
        n_ctx      = int(os.getenv("LLM_CTX",     "4096"))  # context size
        n_gpu_layers = int(os.getenv("LLM_GPU_LAYERS", "0")) # layers on NPU/GPU (0=CPU only)
        self._llm  = Llama(
            model_path=LLM_MODEL,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        # llama-cpp-python supports ChatML / tool_calls format since v0.2.57
        kwargs = {"messages": messages, "max_tokens": 512, "temperature": 0.3}
        if tools:
            kwargs["tools"] = tools

        resp = self._llm.create_chat_completion(**kwargs)
        choice = resp["choices"][0]["message"]

        tool_calls = choice.get("tool_calls") or None
        content    = choice.get("content") or ""

        return {
            "content":    content.strip(),
            "tool_calls": tool_calls,
            "raw":        resp,
        }

    def chat_with_tool_result(self, messages: list[dict], tool_name: str, tool_result: str) -> str:
        resp = self._llm.create_chat_completion(messages=messages, max_tokens=512)
        return resp["choices"][0]["message"].get("content", "").strip()


# ──------------------------- Backend : OpenAI (dev / test / fallback distant) ──-------------------------
#
# Variables nécessaires dans .env :
#   LLM_BACKEND=openai
#   LLM_MODEL=gpt-4o-mini
#   OPENAI_API_KEY=sk-...

class _OpenAIBackend:
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        kwargs = {"model": LLM_MODEL, "messages": messages, "max_tokens": 512}
        if tools:
            # OpenAI expects a slightly different format
            kwargs["tools"] = [{"type": "function", "function": t["function"]} for t in tools]

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message

        tool_calls = choice.tool_calls or None
        content    = choice.content or ""

        return {
            "content":    content.strip(),
            "tool_calls": tool_calls,
            "raw":        resp,
        }

    def chat_with_tool_result(self, messages: list[dict], tool_name: str, tool_result: str) -> str:
        resp = self._client.chat.completions.create(
            model=LLM_MODEL, messages=messages, max_tokens=512
        )
        return resp.choices[0].message.content.strip()
