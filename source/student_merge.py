"""Independent conservative filename grouping; merge never anonymizes input."""
from collections import defaultdict
from pathlib import Path
import re
import json
from datetime import datetime
from core import fitz, digest, validate_ids


def plan_merge(folder, codes=None):
    folder=Path(folder).resolve()
    if not folder.is_dir():raise ValueError('PDF 폴더를 선택하십시오.')
    files=sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower()=='.pdf'),key=lambda p:p.name.casefold())
    if not files:raise ValueError('선택 폴더 바로 아래 PDF가 없습니다.')
    roots=list(codes) if codes is not None else [p.stem for p in files]
    if codes is not None:
        validate_ids(roots,len(roots))
        if len({c.casefold() for c in roots})!=len(roots):raise ValueError('코드표 중복 코드')
    groups=defaultdict(list);issues=defaultdict(list);unmatched=[]
    folded=defaultdict(list)
    for p in files:folded[p.stem.casefold()].append(p)
    for p in files:
        candidates=[]
        for code in roots:
            if p.stem.casefold()==code.casefold():candidates.append((code,1))
            elif p.stem.casefold().startswith(code.casefold()):
                tail=p.stem[len(code):]
                if re.fullmatch(r'\d{3}',tail) and int(tail)>=2:candidates.append((code,int(tail)))
        # Without table, an explicit base + NNN continuation is the canonical rule.
        # Multiple matching bases still remain ambiguous; never pick the longest.
        if codes is None:
            continuation=[c for c in candidates if c[1]>1]
            if continuation:candidates=continuation
        unique=list(dict.fromkeys(candidates))
        if len(unique)!=1:
            reason='여러 코드로 해석 가능' if unique else '코드표 불일치 또는 이름 규칙 불일치'
            unmatched.append({'file':p.name,'reason':reason})
            for code,_ in unique:issues[code].append(f'{p.name}: {reason}')
            continue
        code,number=unique[0]
        groups[code].append((number,p))
        if len(folded[p.stem.casefold()])>1:issues[code].append(f'{p.name}: 대소문자 중복 파일명')
    # A stem used as continuation must not also serve as another group's base.
    if codes is None:
        consumed={p.stem.casefold():code for code,items in groups.items() for n,p in items if n>1}
        for code in list(groups):
            if code.casefold() in consumed and consumed[code.casefold()]!=code:
                other=consumed[code.casefold()]
                issues[code].append('후속면 파일이 다른 그룹의 기준 코드로도 사용됨')
                issues[other].append('다른 그룹 기준 코드와 후속면 해석 충돌')
    if codes is not None:
        for code in roots:
            if code not in groups:issues[code].append('코드의 입력 PDF 없음')
    plans=[]
    for code in sorted(set(groups)|set(issues),key=str.casefold):
        items=sorted(groups[code],key=lambda t:t[0]);numbers=[n for n,p in items]
        if numbers and numbers!=list(range(1,max(numbers)+1)):issues[code].append('누락 또는 중복 순번 (첫 면부터 연속 필요)')
        hashes={};signatures=[]
        for number,p in items:
            try:
                hashes[str(p)]=digest(p)
                with fitz.open(p) as d:
                    if d.needs_pass or len(d)!=1:raise ValueError('비암호화 단일 페이지 PDF 필요')
                    signatures.append(page_signature(d[0]))
            except Exception as exc:issues[code].append(f'{p.name}: PDF 손상·암호화·페이지 수 오류 ({type(exc).__name__})')
        output=folder/'all'/f'{code}(all).pdf'
        if output.parent.exists():
            if not output.parent.is_dir() or output.parent.is_symlink():issues[code].append('all 경로가 안전한 일반 폴더가 아님')
            elif any(p.name.casefold()==output.name.casefold() for p in output.parent.iterdir()):issues[code].append('기존 출력 파일 충돌')
        plans.append({'code':code,'files':[str(p) for n,p in items],'numbers':numbers,'output':str(output),
                      'issues':issues[code],'hashes':hashes,'signatures':signatures})
    return {'folder':str(folder),'groups':plans,'unmatched':unmatched}


def page_signature(page):
    import hashlib
    pix=page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False,annots=True)
    return (pix.width,pix.height,hashlib.sha256(pix.samples).hexdigest())


def execute_merge(plan):
    folder=Path(plan['folder']);out=folder/'all'
    if out.is_symlink():raise ValueError('all 심볼릭 링크는 사용할 수 없습니다.')
    out.mkdir(exist_ok=True)
    results=[]
    for group in plan['groups']:
        item={'code':group['code'],'status':'보류','reason':'; '.join(group['issues']),'output':Path(group['output']).name}
        if not group['issues']:
            try:
                target=Path(group['output'])
                if any(p.name.casefold()==target.name.casefold() for p in out.iterdir()):raise ValueError('기존 출력 충돌')
                if not all(digest(p)==h for p,h in group['hashes'].items()):raise ValueError('검사 후 입력 변경')
                with fitz.open() as merged:
                    for path in group['files']:
                        with fitz.open(path) as d:merged.insert_pdf(d)
                    merged.set_metadata({});data=merged.tobytes(garbage=4,deflate=True)
                with fitz.open(stream=data,filetype='pdf') as d:
                    if len(d)!=len(group['files']) or [page_signature(p) for p in d]!=group['signatures']:
                        raise ValueError('병합 페이지 수·순서 검증 실패')
                if not all(digest(p)==h for p,h in group['hashes'].items()):raise ValueError('입력 변경')
                with target.open('xb') as f:f.write(data)
                with fitz.open(target) as d:
                    if [page_signature(p) for p in d]!=group['signatures']:raise ValueError('저장 후 재개방 검증 실패')
                item.update(status='성공',reason=f'{len(group["files"])}페이지 · 순서 재검증 완료')
            except Exception as exc:item.update(status='실패',reason=str(exc))
        results.append(item)
    log=out/('merge_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    with log.open('x',encoding='utf-8') as f:json.dump({'results':results,'unmatched':plan['unmatched']},f,ensure_ascii=False,indent=2)
    return results,log
