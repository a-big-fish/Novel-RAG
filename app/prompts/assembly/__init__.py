from app.prompts.assembly import v1

PROMPT_VERSION = v1.PROMPT_VERSION
render_prompt = v1.render_prompt
render_reference = v1.render_reference
wrapper_chars = v1.wrapper_chars

__all__ = ["PROMPT_VERSION", "render_prompt", "render_reference", "wrapper_chars"]
