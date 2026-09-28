"""Shared inference pipeline for P1-VL-30B-A3B (Q4_K_M GGUF) on text-only questions.

Used by prompt_regular.ipynb and prompt_detailed.ipynb. Each notebook loads a fixed prompt
from prompts/, combines it with a question, runs the model, displays the answer
and saves a Markdown record to outputs/<prompt_name>/.
"""

import json
import re
from datetime import datetime
from pathlib import Path

# 4-bit quantized copy of PRIME-RL/P1-VL-30B-A3B, run with llama.cpp.
MODEL_REPO = "mradermacher/P1-VL-30B-A3B-GGUF"
MODEL_FILE = "P1-VL-30B-A3B.Q4_K_M.gguf"
MODEL_ID = f"{MODEL_REPO}/{MODEL_FILE}"

# Context window = prompt + reasoning + answer. Must exceed max_tokens plus the prompt.
N_CTX = 32768

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "prompts"
OUTPUTS_DIR = ROOT / "outputs"

# Placeholder in a prompt file that gets replaced by the question.
# If a prompt file has no placeholder, the question is appended after the prompt.
QUESTION_PLACEHOLDER = "{question}"

# Sampling defaults recommended for Qwen3-VL "Thinking" models (P1-VL's base).
# repeat_penalty and min_p are set explicitly because llama.cpp's defaults
# (1.1 and 0.05) differ from the reference Hugging Face settings.
DEFAULT_GEN_KWARGS = {
    "max_tokens": 16384,
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "repeat_penalty": 1.0,
}

_llm = None


def load_model():
    """Download (first time only) and load the GGUF model once per kernel."""
    global _llm
    if _llm is None:
        import llama_cpp

        if not llama_cpp.llama_supports_gpu_offload():
            raise RuntimeError(
                "llama-cpp-python was installed without CUDA, so it would run on CPU. "
                "Reinstall it with CUDA (see README, step 3)."
            )
        print(f"Loading {MODEL_ID} (the first run downloads ~19 GB)...")
        _llm = llama_cpp.Llama.from_pretrained(
            repo_id=MODEL_REPO,
            filename=MODEL_FILE,
            n_gpu_layers=-1,  # put every layer on the GPU
            n_ctx=N_CTX,
            verbose=False,
        )
        print("Model loaded.")
    return _llm


def load_prompt(prompt_name):
    path = PROMPTS_DIR / f"{prompt_name}.md"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{path} is empty. Put your fixed prompt in it first.")
    return text


def build_user_message(prompt, question):
    # str.replace rather than str.format so LaTeX braces in prompts are safe.
    if QUESTION_PLACEHOLDER in prompt:
        return prompt.replace(QUESTION_PLACEHOLDER, question)
    return f"{prompt}\n\n{question}"


def build_chat(user_message):
    """Qwen3-VL Thinking chat format for a single text-only user turn.

    Built by hand (instead of the GGUF's Jinja template) so the exact text sent
    to the model is fixed and identical for every run.
    """
    return (
        f"<|im_start|>user\n{user_message}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n"
    )


def split_thinking(text):
    """Split a Thinking-model completion into (reasoning, final_answer)."""
    if "</think>" in text:
        reasoning, answer = text.split("</think>", 1)
        return reasoning.replace("<think>", "").strip(), answer.strip()
    # No closing tag: generation was cut off before the model finished reasoning.
    return text.replace("<think>", "").strip(), ""


def generate(user_message, gen_kwargs=None, on_token=None, seed=None):
    """Run the model on one text message.

    Returns (completion_text, n_output_tokens, truncated).
    on_token: optional callback receiving the accumulated text as it streams.
    """
    llm = load_model()
    kwargs = {**DEFAULT_GEN_KWARGS, **(gen_kwargs or {})}

    # Tokenize ourselves with special=True so <|im_start|> etc. become control tokens.
    prompt_tokens = llm.tokenize(build_chat(user_message).encode("utf-8"),
                                 add_bos=False, special=True)
    if len(prompt_tokens) + kwargs["max_tokens"] > N_CTX:
        raise ValueError(
            f"Prompt ({len(prompt_tokens)} tokens) + max_tokens ({kwargs['max_tokens']}) "
            f"exceeds N_CTX ({N_CTX}). Lower max_tokens or raise N_CTX in pipeline.py."
        )

    llm.reset()  # each question starts from a clean state
    text, n_tokens, finish = "", 0, None
    for chunk in llm.create_completion(prompt_tokens, stream=True, seed=seed,
                                       stop=["<|im_end|>"], **kwargs):
        choice = chunk["choices"][0]
        text += choice["text"]
        n_tokens += 1
        finish = choice.get("finish_reason") or finish
        if on_token is not None:
            on_token(text)
    return text, n_tokens, finish == "length"


def _slug(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:n].strip("-") or "question"


def save_markdown(prompt_name, prompt, question, reasoning, answer, meta):
    out_dir = OUTPUTS_DIR / prompt_name
    out_dir.mkdir(parents=True, exist_ok=True)
    label = meta.get("question_id") or _slug(question)
    stem = f"{meta['timestamp'].replace(':', '')}_{_slug(label)}"

    params = "\n".join(f"- **{k}:** {v}" for k, v in meta.items())
    md = (
        f"# {prompt_name} — {meta.get('question_id') or 'Question'}\n\n"
        f"{params}\n\n"
        f"## Prompt\n\n````\n{prompt}\n````\n\n"
        f"## Question\n\n{question}\n\n"
        f"## Answer\n\n{answer or '_No final answer (generation hit max_tokens before </think>)._'}\n\n"
        f"## Reasoning\n\n<details>\n<summary>Show reasoning trace</summary>\n\n"
        f"{reasoning}\n\n</details>\n"
    )
    # Every run gets its own file; never overwrite an earlier one.
    n = 1
    while True:
        path = out_dir / (f"{stem}.md" if n == 1 else f"{stem}_{n}.md")
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(md)
            break
        except FileExistsError:
            n += 1

    # Also append to a JSONL log so all runs are easy to analyse together.
    record = {**meta, "prompt": prompt, "question": question,
              "answer": answer, "reasoning": reasoning}
    with open(out_dir / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def ask(prompt_name, question, question_id="", gen_kwargs=None, seed=None, on_token=None):
    """Full pipeline: prompt + question -> model -> saved Markdown file."""
    question = question.strip()
    if not question:
        raise ValueError("Question is empty.")
    prompt = load_prompt(prompt_name)
    kwargs = {**DEFAULT_GEN_KWARGS, **(gen_kwargs or {})}

    started = datetime.now()
    raw, n_tokens, hit_limit = generate(build_user_message(prompt, question),
                                        kwargs, on_token, seed)
    reasoning, answer = split_thinking(raw)

    meta = {
        "timestamp": started.strftime("%Y-%m-%dT%H:%M:%S"),
        "question_id": question_id.strip(),
        "prompt_name": prompt_name,
        "model": MODEL_ID,
        "seed": seed,
        "output_tokens": n_tokens,
        "truncated": hit_limit or not answer,
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        **kwargs,
    }
    path = save_markdown(prompt_name, prompt, question, reasoning, answer, meta)
    return {"answer": answer, "reasoning": reasoning, "path": path, "meta": meta}


def launch_ui(prompt_name, seed=None, gen_kwargs=None):
    """Show a question textbox + Run button in a Jupyter notebook."""
    import html
    import time

    import ipywidgets as w
    from IPython.display import Markdown, display

    prompt = load_prompt(prompt_name)
    load_model()

    qid = w.Text(placeholder="optional, e.g. Q1", description="Question ID:",
                 layout=w.Layout(width="400px"))
    question = w.Textarea(placeholder="Paste the question here...",
                          layout=w.Layout(width="100%", height="180px"))
    run = w.Button(description="Run", button_style="primary", icon="play")
    status = w.HTML()
    live = w.HTML(layout=w.Layout(max_height="300px", overflow="auto"))
    result = w.Output()

    def on_run(_):
        run.disabled = True
        result.clear_output()
        status.value = "<b>Generating...</b> (live output below)"
        last = [0.0]

        def on_token(text):
            if time.time() - last[0] > 0.5:  # throttle widget updates
                last[0] = time.time()
                live.value = f"<pre style='white-space:pre-wrap'>{html.escape(text[-4000:])}</pre>"

        try:
            r = ask(prompt_name, question.value, qid.value, gen_kwargs, seed, on_token)
            live.value = ""
            m = r["meta"]
            status.value = (f"Done in {m['seconds']}s, {m['output_tokens']} tokens. "
                            f"Saved to <code>{r['path'].relative_to(ROOT)}</code>")
            with result:
                display(Markdown("### Answer\n\n" + (r["answer"] or
                        "_No final answer — hit max_tokens. See reasoning in the saved file._")))
        except Exception as e:
            status.value = f"<span style='color:red'>Error: {html.escape(str(e))}</span>"
        finally:
            run.disabled = False

    run.on_click(on_run)
    display(w.VBox([
        w.HTML(f"<b>Prompt:</b> <code>prompts/{prompt_name}.md</code>"
               f"<pre style='white-space:pre-wrap;max-height:120px;overflow:auto'>{html.escape(prompt)}</pre>"),
        qid, question, run, status, live, result,
    ]))
