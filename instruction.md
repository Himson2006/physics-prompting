# How to run the pipeline

Model: P1-VL-30B-A3B, Q4_K_M GGUF ([original](https://huggingface.co/PRIME-RL/P1-VL-30B-A3B), [GGUF](https://huggingface.co/mradermacher/P1-VL-30B-A3B-GGUF)). Needs a GPU with ≥ 24 GB free and ~20 GB of disk.

## One-time setup (server)
1. Check the server in a terminal: `nvidia-smi` (free memory, CUDA version) and `python --version`.
2. Clone the repo. Log in with your GitHub username and a personal access token (Contents: read and write).
   ```bash
   git clone https://github.com/Himson2006/physics-prompting.git
   cd physics-prompting
   ```
3. Optional, for faster downloads: `hf auth login` with a Hugging Face Read token.
4. Open `prompt_regular.ipynb`, add a temporary cell at the top, and run one of:
   - If CUDA ≥ 12.4 and Python is 3.11 or 3.12:
     ```
     %pip install -r requirements.txt
     %pip install llama-cpp-python==0.3.19 --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
     ```
   - Otherwise (builds from source, ~15 min):
     ```
     %pip install -r requirements.txt
     %env CMAKE_ARGS=-DGGML_CUDA=on
     %pip install llama-cpp-python --no-cache-dir --force-reinstall
     ```
   Then restart the kernel and delete that cell.

## Each run
Run cells one at a time with Shift+Enter. Don't use "Run All", or the last cell will start every question.

1. **GPU check cell:** must print `GPU: True`.
2. **Settings cell:** set `SEED = 0` and run it. The first time, it downloads the model (~19 GB).
3. **Last cell:** set `RUN_IDS` and run it.
   - `["Q1"]` runs a single question; use this for a first test.
   - `None` runs every question in `questions.json`.
4. Answers print in the notebook and are saved to `outputs/<prompt>/`, one `.md` per run.
5. To switch prompts: shut down this notebook's kernel, open `prompt_detailed.ipynb`, and repeat steps 1–3 with the **same** `SEED` and `RUN_IDS`.
6. If an answer shows `truncated: True`, set `GEN_KWARGS = {"max_tokens": 28000}` and re-run that question in **both** notebooks.

## Save results
On the server:
```bash
git add outputs && git commit -m "Results: <what you ran>" && git push
```
Then run `git pull` on your Mac.

## Adding questions
On your Mac:
1. Add the questions to `questions.json` with **new** IDs.
2. Double every LaTeX backslash (`$\\theta$`).
3. Push to GitHub.

On the server, `git pull`, then set `RUN_IDS` to just the new IDs. Never edit or renumber questions that have already been run.

## Troubleshooting
- **`GPU: False` or very slow:** reinstall llama-cpp-python using the build-from-source option.
- **Kernel dies while loading:** not enough free GPU memory. Shut down other notebooks or jobs.
- **`Not in questions.json`:** typo in `RUN_IDS`, or you haven't pulled the new questions yet.
- **`exceeds N_CTX`:** keep `max_tokens` ≤ 30000.
