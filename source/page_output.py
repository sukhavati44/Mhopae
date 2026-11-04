"""Batch raster export and metadata-based regrouping, exclusively to new files."""
from collections import defaultdict
from pathlib import Path
from datetime import datetime
import copy
import json
from core import fitz, digest, make_pdf, plan_names
from page_identity import read, write
from scan_mapping import inspect_mapping
from student_merge import page_signature


def relabel_pdf(page,info,dpi=300):
    """For our raster pages, preserve body pixels and replace only both boxes."""
    from page_identity import box_header,draw_header
    try:original=read(page)
    except (ValueError,TypeError,KeyError):original=None
    images=page.get_images(full=True)
    if original and len(images)==1:
        bbox=page.get_image_rects(images[0][0])
        if len(bbox)==1:
            rect=bbox[0];data=page.parent.extract_image(images[0][0])['image']
            with fitz.open() as doc:
                layout=box_header(doc,rect.width,info);h=layout['height']
                target=doc.new_page(width=rect.width,height=rect.height+h);draw_header(target,layout)
                target.insert_image(fitz.Rect(0,h,rect.width,h+rect.height),stream=data);write(target,info)
                doc.subset_fonts();return doc.tobytes(garbage=4,deflate=True)
    # Unknown format: retain the whole already-anonymized page. Never guess a crop.
    return make_pdf(page,info['student_code'],0,dpi,(),info)


def import_pages(paths):
    rows=[]
    for path in paths:
        try:
            with fitz.open(path) as doc:
                if doc.needs_pass or not len(doc):raise ValueError('암호화·빈 PDF')
                for i,page in enumerate(doc):
                    row={'source':str(Path(path).resolve()),'page':i,'identity':None,'excluded':False,'note':''}
                    try:row['identity']=read(page)
                    except (ValueError,TypeError,KeyError,json.JSONDecodeError):row['note']='나중에 지정: 페이지 정보 없음/형식 오류'
                    rows.append(row)
        except Exception as exc:
            rows.append({'source':str(Path(path).resolve()),'page':0,'identity':None,'excluded':False,'note':'나중에 지정: PDF 열기 실패 '+type(exc).__name__})
    return rows


def group_rows(rows,mode):
    groups=defaultdict(list)
    for row in rows:
        info=row['identity']
        if mode=='student':key=('student',info['student_code'])
        elif mode=='class':key=('class',info['class'])
        elif mode=='order':key=('order',info['page_order'])
        elif mode=='page':key=('page',info['student_code'],info['page_order'])
        else:raise ValueError('출력 묶음 방식을 확인하십시오.')
        groups[key].append(row)
    for key,values in groups.items():
        values.sort(key=lambda r:(r['identity']['class'],r['identity']['student_code'],r['identity']['page_order']))
    return groups


def export_pages(rows,folder,mode='class',dpi=300,rules_by_source=None,raster=True,roster=None,progress=None):
    rows=copy.deepcopy(rows);folder=Path(folder).resolve()
    if not folder.is_dir():raise ValueError('기존 로컬 출력 폴더를 선택하십시오.')
    statuses=inspect_mapping(rows)
    ready=[r for i,r in enumerate(rows) if statuses[i]=='출력 가능']
    if not ready:raise ValueError('출력 가능 페이지가 없습니다. 다른 페이지는 그대로 두고 연결을 지정할 수 있습니다.')
    sources={str(Path(r['source']).resolve()) for r in ready}
    if folder in {Path(p).parent for p in sources} or (roster and folder==Path(roster).resolve().parent):
        raise ValueError('입력과 분리된 로컬 출력 폴더를 선택하십시오.')
    hashes={p:digest(p) for p in sources}
    if roster:hashes[str(Path(roster).resolve())]=digest(roster)
    groups=group_rows(ready,mode);names=[]
    for index,key in enumerate(groups,1):
        if key[0]=='student':names.append(key[1]+'(all)')
        elif key[0]=='class':
            from core import validate_ids
            try:validate_ids(['학급_'+key[1]],1);names.append('학급_'+key[1])
            except ValueError:names.append(f'학급_{index:03}')
        elif key[0]=='order':names.append(f'페이지순번_{key[1]:03}')
        else:names.append(f'{key[1]}_p{key[2]:03}')
    paths,collisions=plan_names(names,folder);results=[];done=0
    for (key,values),target in zip(groups.items(),paths):
        try:
            expected=[];signatures=[]
            with fitz.open() as out:
                for row in values:
                    path=str(Path(row['source']).resolve());info=row['identity']
                    if digest(path)!=hashes[path]:raise ValueError('검사 후 입력 파일 변경')
                    with fitz.open(path) as src:
                        page=src[row['page']]
                        if raster:
                            rules=(rules_by_source or {}).get(path)
                            cut,masks=rules.for_page(row['page']) if rules else (.18,())
                            data=make_pdf(page,info['student_code'],cut,dpi,masks,info)
                            with fitz.open(stream=data,filetype='pdf') as one:
                                out.insert_pdf(one);signatures.append(page_signature(one[0]))
                        else:
                            # This menu accepts previously anonymized inputs only.
                            try:same=read(page)==info
                            except (ValueError,TypeError,KeyError):same=False
                            if same:
                                out.insert_pdf(src,from_page=row['page'],to_page=row['page']);signatures.append(page_signature(page))
                            else:
                                with fitz.open(stream=relabel_pdf(page,info,dpi),filetype='pdf') as changed:
                                    out.insert_pdf(changed);signatures.append(page_signature(changed[0]))
                        write(out[-1],info);expected.append(info)
                    done+=1
                    if progress:progress(done,len(ready))
                out.set_metadata({});out.subset_fonts();data=out.tobytes(garbage=4,deflate=True)
            with fitz.open(stream=data,filetype='pdf') as check:
                if [read(p) for p in check]!=expected or [page_signature(p) for p in check]!=signatures:raise ValueError('페이지 정보·출력 순서 검증 실패')
            if not all(digest(p)==h for p,h in hashes.items()):raise ValueError('입력 변경: 저장 중단')
            with target.open('xb') as stream:stream.write(data)
            with fitz.open(target) as check:
                if [read(p) for p in check]!=expected or [page_signature(p) for p in check]!=signatures:raise ValueError('저장 후 재개방 검증 실패')
            results.append({'group':list(key),'file':target.name,'pages':len(values),'status':'성공'})
        except Exception as exc:results.append({'group':list(key),'file':target.name,'pages':len(values),'status':'실패','reason':str(exc)})
    unchanged=all(digest(p)==h for p,h in hashes.items())
    report={'results':results,'ready_pages':len(ready),'pending_pages':sum(s not in ('출력 가능','제외') for s in statuses.values()),
            'excluded_pages':sum(s=='제외' for s in statuses.values()),'inputs_unchanged':unchanged,'source_sha256':hashes,
            'mapping':[{'file':Path(r['source']).name,'source_page':r['page']+1,'identity':r['identity'],'state':statuses[i]} for i,r in enumerate(rows)]}
    log=folder/('page_output_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    with log.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    if not unchanged:raise RuntimeError('원본 해시 변경: 위험 작업 중단')
    return report,log
