from pathlib import Path
lines = Path('core/logger.py').read_text(encoding='utf-8').splitlines()
for ln in [4,243,246,247,352,357,363,364,365,392,419,443,444,445,448,453,454,460,476,486,495,498,507,509,510,728,931,934,935,936,937,939,972,974,975,976,999,1002,1003,1004,1005,1006,1007,1021,1025,1094,1097,1099,369,370,371]:
    line = lines[ln-1]
    print(f'LINE {ln}: {line}')
    cur = line
    for i in range(1,4):
        try:
            cur = cur.encode('gbk').decode('utf-8')
        except Exception as e:
            print(f'  stop {i}: {e}')
            break
        print(f'  round{i}: {cur}')
