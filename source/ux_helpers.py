"""Local display labels; these labels must never enter PDF identity or export logs."""
from pathlib import Path
from page_output import group_rows
from core import validate_ids

MODES={'학급별 묶음':'class','학생별 묶음':'student','페이지순번별 묶음':'order','페이지별 개별':'page'}

def student_label(student):
    if not student:return '학생정보 미확인'
    return f"{student.get('class') or '학급 미확인'} · {student.get('number')+'번' if student.get('number') else '번호 미확인'} · {student.get('name') or '이름 미확인'} ({student.get('code') or '코드 미확인'})"

def categories(states):
    result={'전체':list(states),'미정':[],'중복':[],'제외':[]}
    for i,state in states.items():
        if state=='제외':result['제외'].append(i)
        elif '중복' in state or '여러 번' in state:result['중복'].append(i)
        elif state!='출력 가능':result['미정'].append(i)
    return result

def output_estimate(rows,states,mode):
    ready=[r for i,r in enumerate(rows) if states[i]=='출력 가능']
    groups=group_rows(ready,mode)
    if not groups:return '저장 대상 0페이지 · 연결할 학생을 확인하십시오.'
    key=next(iter(groups));sizes=[len(g) for g in groups.values()]
    if mode=='student':name=key[1]+'(all).pdf'
    elif mode=='class':
        try:validate_ids(['학급_'+key[1]],1);name='학급_'+key[1]+'.pdf'
        except ValueError:name='학급_001.pdf'
    elif mode=='order':name=f'페이지순번_{key[1]:03d}.pdf'
    else:name=f'{key[1]}_p{key[2]:03d}.pdf'
    amount=f'각 {sizes[0]}쪽' if len(set(sizes))==1 else f'파일당 {min(sizes)}~{max(sizes)}쪽'
    return f'예상 {len(groups)}파일 · 총 {len(ready)}쪽 ({amount}) · 예: {name} · 동명 파일은 새 번호로 저장'
