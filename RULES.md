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