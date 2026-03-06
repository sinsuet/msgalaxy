from pathlib import Path
lines = Path('core/logger.py').read_text(encoding='utf-8').splitlines()
for start, end in [(1,5),(242,247),(350,365),(392,420),(440,510),(724,729),(930,976),(998,1025),(1092,1099)]:
    print(f'===== {start}-{end} =====')
    for i in range(start, end+1):
        print(f'{i}: {lines[i-1]}')
