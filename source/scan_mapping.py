"""Editable mapping model: pending pages never shift other assignments."""
from collections import Counter,defaultdict
from dataclasses import dataclass
from page_identity import identity


@dataclass
class ScanBlock:
    source: str
    start: int
    end: int
    order: int=1
    reverse: bool=False
    student_start: int=0
    confirmed: bool=False


def build_mapping(blocks,students,mode='faces',pages_per_student=1):
    if pages_per_student<1:raise ValueError('학생당 페이지 수는 1 이상입니다.')
    rows=[];totals=Counter()
    for b in blocks:totals[b.source]+=b.end-b.start+1
    overall=sum(totals.values())
    for block in blocks:
        if block.start<1 or block.end<block.start:raise ValueError('스캔 구간의 시작·끝 페이지를 확인하십시오.')
        for offset,page in enumerate(range(block.start-1,block.end)):
            if mode=='continuous':student_index=block.student_start+offset//pages_per_student;order=offset%pages_per_student+1
            else:student_index=block.student_start+offset;order=block.order
            if block.reverse:student_index=len(students)-1-student_index
            student=students[student_index] if 0<=student_index<len(students) else None
            row={'source':block.source,'page':page,'identity':None,'excluded':False,'note':''}
            if student and student['code'] and student['class']:
                row['identity']=identity(student['code'],student['class'],order)
            else:row['note']='나중에 지정: 대응할 학생코드 없음'
            uncertain=(mode=='faces' and totals[block.source]%max(1,len(students))!=0) or (mode=='continuous' and ((block.end-block.start+1)%pages_per_student!=0 or overall!=len(students)*pages_per_student)) or (mode=='single' and overall!=len(students))
            if uncertain and not block.confirmed:
                row['identity']=None;row['note']='나중에 지정: 인원·페이지 수 불일치, 구간 확인 필요 (자동으로 밀어 붙이지 않음)'
            rows.append(row)
    # Overlapping source ranges should be edited, never silently duplicated.
    counts=Counter((r['source'],r['page']) for r in rows)
    for row in rows:
        if counts[(row['source'],row['page'])]>1:row['note']='나중에 지정: 원본 페이지 구간 중복';row['identity']=None
    return rows


def inspect_mapping(rows):
    valid={};groups=defaultdict(list);class_codes=defaultdict(set);origins=defaultdict(list)
    for i,row in enumerate(rows):
        if row.get('excluded'):valid[i]='제외';continue
        origins[(row['source'],row['page'])].append(i)
        try:
            from page_identity import validate
            info=validate(row['identity']);groups[(info['student_code'],info['page_order'])].append(i);class_codes[info['student_code']].add(info['class'])
            valid[i]='출력 가능'
        except (ValueError,TypeError,KeyError):valid[i]=row.get('note') or '나중에 지정'
    for group,indexes in groups.items():
        if len(indexes)>1:
            for i in indexes:valid[i]='나중에 지정: 학생코드·페이지순번 중복'
    for indexes in origins.values():
        if len(indexes)>1:
            for i in indexes:valid[i]='나중에 지정: 같은 원본 페이지가 여러 번 연결됨'
    for i,row in enumerate(rows):
        info=row.get('identity')
        if info and len(class_codes[info['student_code']])>1:valid[i]='나중에 지정: 같은 코드의 학급 충돌'
    return valid
