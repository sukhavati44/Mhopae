"""Local-only, versioned JSON work sessions. Never execute serialized code."""
import copy
import json
import os
import sys
import math
import hashlib
from pathlib import Path
from dataclasses import asdict
from core import digest,fitz,check_cut
from page_rules import PageRules,rectangle
from page_identity import validate
from roster_codes import read_students

SCHEMA='mhopae-local-work-v1'

def work_folder():
    base=Path(sys.executable).parents[2] if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    folder=base/'local_work';folder.mkdir(exist_ok=True)
    return folder.resolve()

def local_path(path):
    path=Path(path).resolve();root=work_folder()
    if not path.is_relative_to(root) or not path.name.endswith('.mhopae.json'):
        raise ValueError(f'작업 파일은 이 PC의 전용 폴더에만 저장·열 수 있습니다: {root}')
    return path

def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def signature(value):
    value=copy.deepcopy(value);ui=value['ui'];value['ui']={key:ui.get(key) for key in ('output_mode','output_folder','scope','editor_code','editor_class','editor_order')}
    return fingerprint(value)

def pack_rules(rule):return asdict(rule)

def unpack_rules(value,count):
    check_cut(value['cut'],300)
    def masks(items):return [rectangle(tuple(x)) for x in items]
    rule=PageRules(value['cut'],masks(value['masks']))
    for key,entry in value.get('cuts',{}).items():
        p=int(key)
        if not 0<=p<count:raise ValueError('작업 파일의 개별 절단 쪽번호 오류')
        check_cut(entry,300);rule.cuts[p]=entry
    for key,entry in value.get('page_masks',{}).items():
        p=int(key)
        if not 0<=p<count:raise ValueError('작업 파일의 개별 마스크 쪽번호 오류')
        rule.page_masks[p]=masks(entry)
    return rule

def save(path,payload):
    path=local_path(path)
    # Exclusive temp and atomic replace: failure cannot claim success or truncate old work.
    temp=path.with_name(path.name+'.writing')
    created=False
    try:
        with temp.open('x',encoding='utf-8') as stream:
            created=True
            json.dump(payload,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        with temp.open(encoding='utf-8') as stream:
            if json.load(stream)!=json.loads(json.dumps(payload)):raise ValueError('작업 파일 재개방 검증 실패')
        os.replace(temp,path)
    finally:
        if created and temp.exists():temp.unlink()
    return path

def load(path):
    path=local_path(path)
    if path.stat().st_size>16*1024*1024:raise ValueError('작업 파일이 너무 큽니다.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=SCHEMA:raise ValueError('지원하지 않는 작업 파일 형식입니다.')
    if type(data.get('dpi')) is not int or data['dpi'] not in (150,200,300,400):raise ValueError('작업 파일의 DPI 오류')
    files=data['files'];counts={};issues=[];unsafe=set()
    for item in files:
        source=item['path'];count=item['pages']
        if not isinstance(source,str) or type(count) is not int or not 0<count<=100000:raise ValueError('입력 참조 오류')
        if source in counts:raise ValueError('중복 입력 참조')
        counts[source]=count
        try:
            if digest(source)!=item['sha256']:raise ValueError('내용 변경')
            with fitz.open(source) as doc:
                if doc.needs_pass or len(doc)!=count:raise ValueError('쪽수·암호 변경')
        except Exception:
            unsafe.add(source);issues.append(f'{Path(source).name}: PDF 없음·변경·쪽수 오류')
    roster=data['roster'];students=[];roster_safe=True
    try:
        if digest(roster['path'])!=roster['sha256']:raise ValueError('코드표 변경')
        students=read_students(roster['path'],*roster['options'],allow_partial=True)
        pairs=[[s['code'],s['class']] for s in students]
        if pairs!=data['student_codes']:raise ValueError('코드 대응 변경')
    except Exception:
        roster_safe=False;students=[];issues.append('코드표 없음·변경·학생코드 대응 불일치: 전체 연결 보류')
    allowed={tuple(x) for x in data['student_codes']};rows=copy.deepcopy(data['rows'])
    for row in rows:
        if row['source'] not in counts or type(row['page']) is not int or not 0<=row['page']<counts[row['source']]:raise ValueError('작업 파일의 원본 쪽 참조 오류')
        if type(row.get('excluded')) is not bool or not isinstance(row.get('note'),str):raise ValueError('작업 파일의 페이지 상태 오류')
        info=row['identity']
        if info is not None:
            info=validate(info)
            if (info['student_code'],info['class']) not in allowed:raise ValueError('작업 파일의 학생 연결이 코드표 범위를 벗어납니다.')
        if row['source'] in unsafe or not roster_safe:
            row['held_identity']=info;row['identity']=None;row['note']='입력 검증 보류: 원본·코드표를 확인하고 다시 연결하십시오.';row['excluded']=False
    rules={source:unpack_rules(value,counts[source]) for source,value in data['rules'].items() if source in counts}
    if set(rules)!=set(counts):raise ValueError('작업 파일의 규칙 참조가 입력과 다릅니다.')
    if data['mode'] not in ('single','continuous','faces') or type(data['per']) is not int or not 1<=data['per']<=9999:raise ValueError('스캔 방식 오류')
    from scan_mapping import ScanBlock
    blocks=[]
    for value in data['blocks']:
        block=ScanBlock(**value)
        if block.source not in counts or not 1<=block.start<=block.end<=counts[block.source] or block.order<1 or block.student_start<0:raise ValueError('작업 파일의 묶음 범위 오류')
        blocks.append(block)
    from ux_helpers import MODES
    ui=data['ui']
    if ui['output_mode'] not in MODES or ui['filter'] not in ('전체','미정','중복','제외') or ui['view'] not in ('원본','출력 미리보기'):raise ValueError('작업 파일의 화면 설정 오류')
    return data,rows,students,rules,blocks,issues
