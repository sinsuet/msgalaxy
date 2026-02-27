# File Writing Protocol & Error Prevention (写文件协议与错误预防)

Whenever you need to create, modify, or write to a file, you MUST strictly follow these rules to prevent "Write failed" errors:

1. **Path Verification (路径验证)**: 
   - Never write to a file without explicitly defining the absolute or relative path.
   - If writing to a new file in a subdirectory, you MUST use bash commands (e.g., `mkdir -p <dir_name>`) to ensure the parent directories exist BEFORE calling the Write tool.

2. **Cross-Platform Pathing (跨平台路径规范)**:
   - This project runs on Windows but often uses a Git Bash/Conda environment. 
   - Always use standard forward slashes (`/`) for relative paths (e.g., `scripts/create_model.py`).
   - Avoid mixing `E:\` and `/e/` in the same tool call. Stick to relative paths from the workspace root whenever possible.

3. **File Lock & Permission Checks (文件占用检查)**:
   - If a file is likely being used by another process (like COMSOL or a running Python script), warn the user to close it before attempting to write.

4. **Fallback Mechanism (失败降级方案)**:
   - If the built-in `Write` tool fails, DO NOT just give up.
   - Fallback 1: Use a bash command to write the file, e.g., `cat > filepath << 'EOF' ... EOF`.
   - Fallback 2: Output the exact code block in the chat and ask the user to manually copy-paste it into the specific file.

5. **Python Environment Execution (Python环境执行强制要求)**:
   - You MUST ALWAYS use the `msgalaxy` conda environment when executing ANY Python scripts, running tests, or using tools.
   - Do NOT use the default system `python` or `pytest` commands directly, as your background shell might not have the environment activated.
   - You MUST use `conda run -n msgalaxy <command>` for all executions to guarantee the correct environment.
   - Correct Example: `conda run -n msgalaxy python run_tests.py`
   - Correct Example: `conda run -n msgalaxy pytest tests/`
   - Incorrect Example: `python run_tests.py`

6. **Strict Editing Protocol (严格的文件编辑协议以防止 Edit Failed)**:
   The native `Edit` tool strictly requires an EXACT character-for-character match for the search block. To prevent "Edit failed" errors, you MUST follow this sequence when modifying existing files:
   - **Step 1: ALWAYS READ FIRST**. You must use the read tool to fetch the exact target lines and surrounding context BEFORE attempting any edit. Do not rely on your memory of the file.
   - **Step 2: EXACT WHITESPACE**. Copy the exact whitespace and indentation from the read output into your search block.
   - **Step 3: SUFFICIENT CONTEXT**. Include at least 3 unique lines of unchanged code BEFORE and AFTER the target modification in your search block to ensure a unique match.
   - **Step 4: IMMEDIATE FALLBACK**. If the native Edit tool fails despite these precautions, do not waste time retrying it. Immediately fallback to a bash script (e.g., Python script to read/replace/write, or `cat << 'EOF'`) to make the change.

7. **Strict UTF-8 Encoding Protocol (强制 UTF-8 编码防乱码)**:
   - This project runs on Windows and heavily uses Chinese characters and emojis in logs/outputs.
   - When executing ANY Python command in Bash, you MUST prepend `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` to force Python to output UTF-8.
   - Correct Execution Example: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python scripts/tests/test_phase3_multiphysics.py`
   - When creating or modifying ANY entry-point Python script (especially in `scripts/` or `tests/`), you MUST ensure the following Windows stdout reconfiguration block is at the very beginning of the executable code (as specified in `CLAUDE.md`):
     ```python
     import sys, io
     if sys.platform == 'win32':
         try:
             sys.stdout.reconfigure(encoding='utf-8')
             sys.stderr.reconfigure(encoding='utf-8')
         except AttributeError:
             sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
             sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
     ```

8. **LLM Configuration & API Key Protocol (大模型配置与密钥规范)**:
   - The default and ONLY permitted LLM for this project is `qwen-plus`. Do not attempt to revert to or use OpenAI models (like `gpt-4`) in the configuration unless explicitly requested.
   - The API key and Base URL are ALREADY securely set in the `.env` file. 
   - NEVER ask the user for the API key, and NEVER hardcode API keys into scripts. Always use `dotenv` to load environment variables or read them from `config/system.yaml`.

9. **Strict Scientific Rigor & Anti-Shortcut Policy (科研严谨性与反捷径协议)**:
   - This is a rigorous scientific research project aiming for academic publication.
   - When encountering complex bugs, mathematical divergences (e.g., COMSOL non-linear convergence failure), or integration challenges, you MUST NOT suggest "simplified versions," "temporary bypasses," "mocking data," or "skipping" the core problem.
   - You are required to dig deep, debug thoroughly, and provide mathematically and architecturally sound solutions. Degraded physical fidelity or "hacking" a fix just to make a test pass is STRICTLY PROHIBITED. Thoroughly solve the root cause.