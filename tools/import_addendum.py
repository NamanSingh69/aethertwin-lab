"""Preserve a supplied addendum verbatim while retaining nested code fences."""
import argparse
from pathlib import Path
import shutil


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    lines=args.source.read_text(encoding='utf-8-sig').splitlines()
    start=next(i for i,line in enumerate(lines) if line.strip()=='```markdown')+1
    end=max(i for i,line in enumerate(lines) if line.strip()=='```')
    if end<=start:raise ValueError('Missing addendum closing Markdown fence')
    (root/'references').mkdir(exist_ok=True)
    shutil.copyfile(args.source,root/'references/AetherTwin_addendum_original.txt')
    (root/'PEAK_HIL_AND_UI_SPEC.md').write_text('\n'.join(lines[start:end])+'\n',encoding='utf-8')
    print('Saved addendum and original reference')
