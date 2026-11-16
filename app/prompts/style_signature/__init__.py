from app.prompts.style_signature import v1

PROMPT_VERSION = v1.PROMPT_VERSION
SYSTEM_PROMPT = v1.SYSTEM_PROMPT
build_user_prompt = v1.build_user_prompt

__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT", "build_user_prompt"]
