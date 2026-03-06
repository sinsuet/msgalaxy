from pathlib import Path
lines = Path('core/logger.py').read_text(encoding='utf-8').splitlines()
for i, line in enumerate(lines, 1):
    if any('\u4e00' <= ch <= '\u9fff' for ch in line):
        try:
            conv = line.encode('gbk').decode('utf-8')
        except Exception:
            continue
        if conv != line:
            print(f'{i}: {line}')
            print(f'   -> {conv}')
