from pathlib import Path
lines = Path('core/logger.py').read_text(encoding='utf-8').splitlines()
for start,end in [(684,694),(912,921),(982,989)]:
    print(f'===== {start}-{end} =====')
    for i in range(start,end+1):
        print(f'{i}: {lines[i-1]}')
