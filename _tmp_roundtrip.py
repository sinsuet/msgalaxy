from pathlib import Path
text = Path('core/logger.py').read_text(encoding='utf-8').splitlines()[1]
cur = text
for i in range(5):
    print(i, cur)
    try:
        cur = cur.encode('gbk').decode('utf-8')
    except Exception as e:
        print('ERR', e)
        break
