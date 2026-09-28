# Physics Prompting — P1-VL-30B-A3B (Q4_K_M)

Runs a 4-bit copy of [PRIME-RL/P1-VL-30B-A3B](https://huggingface.co/PRIME-RL/P1-VL-30B-A3B)
([mradermacher/P1-VL-30B-A3B-GGUF](https://huggingface.co/mradermacher/P1-VL-30B-A3B-GGUF), file `Q4_K_M`, 18.6 GB)
with llama.cpp, on text questions with two fixed prompts.

## Files
- `prompts/prompt_regular.md`, `prompts/prompt_detailed.md`: the two fixed prompts. Put `{question}` where the question goes. Without it, the question is appended at the end.
- `prompt_regular.ipynb`, `prompt_detailed.ipynb`: textbox UI, one notebook per prompt.
- `questions.json`: the questions (ID → text), shared by both notebooks and run by their batch cell.
- `pipeline.py`: model download/loading, generation, and saving. Settings are at the top.
- `outputs/prompt_regular/`, `outputs/prompt_detailed/`: one `.md` per answer (settings, prompt, question, answer, collapsible reasoning), plus `log.jsonl` with every run.

## Server requirements
- NVIDIA GPU with at least 24 GB free: 18.6 GB for the model plus ~3 GB for a 32k-token context.
- ~20 GB of disk for the download.
- Python 3.11 or 3.12 if you use the prebuilt wheel in step 3.

## Setup on the GPU server
1. `git clone <repo-url>` and `cd` into it.
2. `pip install -r requirements.txt`
3. Install llama-cpp-python **with CUDA**. Pick one:
   - Prebuilt wheel. Needs a CUDA 12.4+ driver; check the version in the top right of `nvidia-smi`.
     ```bash
     pip install llama-cpp-python==0.3.19 --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
     ```
   - Build from source (latest version). Needs `nvcc` and takes ~10–20 min.
     ```bash
     CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --no-cache-dir --force-reinstall
     ```
   Install into the same Python your Jupyter kernel uses. If unsure, run the command in a notebook cell with `%pip install ...`; for the source build, first run `%env CMAKE_ARGS=-DGGML_CUDA=on`.
4. Optional: `hf auth login` with a Read token, for faster downloads.
5. Open a notebook and run all cells. The first cell must print `GPU: True`.

## Notes
- **Model:** Q4_K_M is a quantized copy. Answers can differ slightly from the full-precision model. Every output records the exact model file.
- **Two notebooks:** each notebook loads its own copy (~21 GB). Both can run at once if the GPU has ~45 GB free; otherwise shut one kernel down before starting the other.
- **Truncation:** the model reasons before answering after `</think>`. If `max_tokens` (default 16384) runs out first, the output is marked `truncated: True`. Raise it via `GEN_KWARGS` (up to ~30000 with the default `N_CTX = 32768` in `pipeline.py`).
- **Sampling:** it is random (temperature 0.6). Set `SEED` for repeatability. All settings are saved with each answer.
