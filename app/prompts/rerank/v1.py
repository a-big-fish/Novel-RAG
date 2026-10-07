"""Qwen3-Reranker prompt for Ollama's raw generate endpoint."""

PROMPT_VERSION = "v1"

_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements '
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
INSTRUCTION = 'Given a writing request, retrieve novel scenes useful as writing references'


def build_prompt(query: str, document: str) -> str:
    return (
        f"{_PREFIX}<Instruct>: {INSTRUCTION}\n"
        f"<Query>: {query}\n<Document>: {document}{_SUFFIX}"
    )
