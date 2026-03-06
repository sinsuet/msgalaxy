from pathlib import Path
lines = Path('core/logger.py').read_text(encoding='utf-8').splitlines()
out = []
for i, line in enumerate(lines, 1):
    if any('\u4e00' <= ch <= '\u9fff' for ch in line):
        cur = line
        chain = [cur]
        for _ in range(3):
            try:
                nxt = cur.encode('gbk').decode('utf-8')
            except Exception:
                break
            if nxt == cur:
                break
            chain.append(nxt)
            cur = nxt
        if len(chain) > 1:
            out.append(f'{i}:')
            for idx, item in enumerate(chain):
                out.append(f'  {idx}: {item}')
Path('_tmp_recover_chain.txt').write_text('\n'.join(out), encoding='utf-8')
