"""Shared inference pipeline for P1-VL-30B-A3B (Q4_K_M GGUF) on text-only questions.

Used by prompt_regular.ipynb, prompt_detailed.ipynb and colab_run.ipynb. A fixed prompt
from prompts/ is combined with a question, the model answers, and the answer is
saved as outputs/<question id>_<prompt>.md (e.g. outputs/Q1_regular.md).
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

# 4-bit quantized copy of PRIME-RL/P1-VL-30B-A3B, run with llama.cpp.
MODEL_REPO = "mradermacher/P1-VL-30B-A3B-GGUF"
MODEL_FILE = "P1-VL-30B-A3B.Q4_K_M.gguf"
MODEL_ID = f"{MODEL_REPO}/{MODEL_FILE}"

# Context window = prompt + reasoning + answer. Must exceed max_tokens plus the prompt.
N_CTX = 32768
FLASH_ATTN = True

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "prompts"
QUESTIONS_FILE = ROOT / "questions.json"
# Set PHYSICS_OUTPUTS_DIR (e.g. to a Google Drive folder) to save answers elsewhere.
OUTPUTS_DIR = Path(os.environ.get("PHYSICS_OUTPUTS_DIR", ROOT / "outputs"))

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
        try:
            _llm = llama_cpp.Llama.from_pretrained(
                repo_id=MODEL_REPO,
                filename=MODEL_FILE,
                n_gpu_layers=-1,  # put every layer on the GPU
                n_ctx=N_CTX,
                # Cuts scratch memory from ~2.2 GiB to ~0.3 GiB, so it fits a 24 GB GPU.
                flash_attn=FLASH_ATTN,
                verbose=False,
            )
        except ValueError as e:
            if "llama_context" not in str(e):
                raise
            raise RuntimeError(
                "Not enough free GPU memory (needs ~21 GiB). Restart the kernel/runtime "
                "to free memory from this failed attempt, then use a bigger GPU or lower "
                "N_CTX in pipeline.py. See the 'cudaMalloc failed' lines above."
            ) from e
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
        n_tokens += bool(choice["text"])  # the final "stop" chunk carries no text
        finish = choice.get("finish_reason") or finish
        if on_token is not None:
            on_token(text)
    return text, n_tokens, finish == "length"


def _slug(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:n].strip("-") or "question"


def prompt_label(prompt_name):
    """prompt_regular -> regular, used in output file names."""
    return prompt_name.removeprefix("prompt_")


def output_stem(prompt_name, question_id, question=""):
    """File name without extension, e.g. Q1_regular."""
    qid = re.sub(r"[^A-Za-z0-9._-]+", "-", question_id.strip()).strip("-")
    return f"{qid or _slug(question)}_{prompt_label(prompt_name)}"


def result_exists(prompt_name, question_id):
    return (OUTPUTS_DIR / f"{output_stem(prompt_name, question_id)}.md").exists()


def save_markdown(prompt_name, prompt, question, reasoning, answer, meta):
    out_dir = OUTPUTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(prompt_name, meta.get("question_id", ""), question)

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
    # A repeat of the same question + prompt becomes Q1_regular_2.md, _3, ...
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
        "n_ctx": N_CTX,
        "flash_attn": FLASH_ATTN,
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
                            f"Saved to <code>{r['path']}</code>")
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


def load_questions():
    return json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))


def run_batch(prompt_names, question_ids=None, seed=None, gen_kwargs=None, skip_existing=True):
    """Answer questions from questions.json with each prompt.

    Goes question by question (Q1 regular, Q1 detailed, Q2 regular, ...) so an
    interrupted run leaves complete pairs. With skip_existing, any question +
    prompt that already has a saved file (e.g. Q1_regular.md) is skipped, so
    re-running after a disconnect continues where it stopped.
    """
    import time

    from IPython.display import Markdown, display

    questions = load_questions()
    ids = list(questions) if question_ids is None else list(question_ids)
    missing = [i for i in ids if i not in questions]
    if missing:
        raise ValueError(f"Not in questions.json: {missing}")
    for name in prompt_names:
        load_prompt(name)  # fail early on a bad prompt name
    load_model()

    jobs = [(qid, name) for qid in ids for name in prompt_names]
    print(f"Saving to {OUTPUTS_DIR}")
    for i, (qid, name) in enumerate(jobs, 1):
        tag = f"[{i}/{len(jobs)}] {output_stem(name, qid)}"
        if skip_existing and result_exists(name, qid):
            print(f"{tag}: already done, skipping")
            continue
        progress = display(Markdown(f"**{tag}**: starting..."), display_id=True)
        state = {"n": 0, "t0": time.time(), "last": 0.0}

        def on_token(_text, tag=tag, progress=progress, state=state):
            state["n"] += 1
            now = time.time()
            if now - state["last"] > 2:
                state["last"] = now
                progress.update(Markdown(
                    f"**{tag}**: generating... {state['n']} tokens, {now - state['t0']:.0f}s"))

        r = ask(name, questions[qid], qid, gen_kwargs, seed, on_token)
        m = r["meta"]
        flag = " ⚠️ TRUNCATED" if m["truncated"] else ""
        progress.update(Markdown(
            f"**{tag}**: done in {m['seconds']}s, {m['output_tokens']} tokens{flag} → `{r['path'].name}`\n\n"
            + (r["answer"] or "_No final answer (hit max_tokens)._")))
    print("All done.")
