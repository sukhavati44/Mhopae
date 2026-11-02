"""Synthetic-only functional, preservation and offline tests."""
from pathlib import Path
import sys
import json
import socket
from unittest.mock import patch
import tkinter as tk
from openpyxl import Workbook, load_workbook
sys.path.insert(0,str(Path(__file__).parent/'source'))
from roster_codes import read_students, issue_missing, save_code_table
from core import digest, export_batch, fitz, make_pdf
from app import App

base=Path(__file__).parent/(sys.argv[1] if len(sys.argv)>1 else 'validation');base.mkdir(exist_ok=True)
inputs=base/'input';inputs.mkdir(exist_ok=True)
tables=base/'tables';tables.mkdir(exist_ok=True)
outputs=base/'pdf';outputs.mkdir(exist_ok=True)
checks=[]
def check(name, condition):
    assert condition,name
    checks.append({'check':name,'result':'PASS'})
def roster(name,rows,headers=('반','번호','이름','학생코드')):
    p=inputs/name; b=Workbook();w=b.active;w.append(headers)
    for r in rows:w.append(r)
    b.save(p);b.close();return p
def reject(name,fn):
    try:fn()
    except ValueError:checks.append({'check':name,'result':'PASS'})
    else:raise AssertionError(name)
plain=roster('no_codes.xlsx',[['1반','01','가상학생가'],['1반','02','가상학생나'],['2반','01','가상학생다']],('학급','번호','이름'))
full=roster('existing_codes.xlsx',[['1반','01','가상학생가','26-2-3-역-0u'],['1반','02','가상학생나','기존+코드_02'],['2반','01','가상학생다','HP-ABCDEFGHJK']])
partial=roster('partial.xlsx',[['1반','01','가상학생가','keep-01'],['1반','02','가상학생나',None],['2반','01','가상학생다',None]])
pdf=inputs/'synthetic.pdf';d=fitz.open()
for i in range(3):
    p=d.new_page(width=300,height=400);p.insert_text((12,40),'FAKE IDENTITY ABOVE CUT');p.insert_text((12,220),f'SYNTHETIC ANSWER {i+1}')
d.save(pdf);d.close()
before={p:digest(p) for p in (plain,full,partial,pdf)}
with patch.object(socket,'socket',side_effect=RuntimeError('network forbidden')),patch.object(socket,'create_connection',side_effect=RuntimeError('network forbidden')):
    s=read_students(plain);check('no code column loads without guessing',all(not r['code'] for r in s))
    issued,n=issue_missing(s);check('all missing issued nonpersonal 10-char random codes',n==3 and all(r['code'].startswith('HP-') and len(r['code'])==13 for r in issued))
    table=tables/'generated.xlsx'
    if table.exists():raise FileExistsError('Use fresh validation directory for this test.')
    save_code_table(issued,table,[outputs,inputs]);again=read_students(table)
    check('generated table reimport preserves exact codes and zero padded number',[r['code'] for r in again]==[r['code'] for r in issued] and again[0]['number']=='01')
    existing=read_students(full);unchanged,n=issue_missing(existing)
    check('valid existing codes are never regenerated',n==0 and unchanged==existing)
    s=read_students(partial);some,n=issue_missing(s,[1]);check('selected missing only',n==1 and some[0]['code']=='keep-01' and some[2]['code']=='')
    complete,n=issue_missing(some);check('remaining missing only',n==1 and complete[1]['code']==some[1]['code'])
    # Force RNG collision with an existing code, then choose a fresh code.
    collision=[dict(r) for r in s];collision[0]['code']='HP-AAAAAAAAAA'
    with patch('roster_codes.secrets.choice',side_effect=['A']*10+['B']*10):
        recovered,n=issue_missing(collision,[1])
    check('random collision retried preserving existing',recovered[0]['code']=='HP-AAAAAAAAAA' and recovered[1]['code']=='HP-BBBBBBBBBB')
    with patch('roster_codes.secrets.choice',return_value='A'):
        try:issue_missing(collision,[1])
        except RuntimeError:check('repeated RNG collision stops safely',collision[1]['code']=='')
        else:raise AssertionError('collision loop')
    invalids=[('duplicate_code',[['1반','1','가상가','SAME'],['1반','2','가상나','same']]),('duplicate_seat',[['1반','01','가상가','A'],['1','1','가상나','B']]),('missing_name',[['1반','1',None,'A']]),('missing_number',[['1반',None,'가상가','A']]),('missing_class',[[None,'1','가상가','A']]),('numeric_code',[['1반','1','가상가',123]]),('formula_code',[['1반','1','가상가','=1+1']])]
    for name,rows in invalids:
        p=roster(name+'.xlsx',rows);h=digest(p);reject(name,lambda p=p:read_students(p));check(name+' input preserved',digest(p)==h)
    h=digest(table)
    try:save_code_table(issued,table)
    except FileExistsError:check('same name no overwrite',digest(table)==h)
    else:raise AssertionError('overwritten')
    reject('identity table cannot enter PDF output folder',lambda:save_code_table(issued,outputs/'private.xlsx',[outputs]))
    ids=[r['code'] for r in existing]
    result,log,_=export_batch(pdf,ids,outputs,.25,150,True,full)
    check('existing code individual PDF output',all(r[3]=='ok' for r in result) and len(result)==3)
    for r in result:
        with fitz.open(outputs/r[2]) as d:
            check('exact code only text '+r[1],''.join(d[0].get_text().splitlines())==r[1] and len(d[0].get_images())==1)
    result2,_,_=export_batch(pdf,ids,outputs,.25,150,True,full,reverse=True)
    check('reverse mapping and collision suffix',result2[0][0]==3 and result2[0][2].endswith('_02.pdf'))
    root=tk.Tk();root.withdraw();a=App(root)
    a.pdf=str(pdf);a.count=3;a.pdf_hash=digest(pdf)
    with patch.object(App,'choose_roster_options',return_value=(None,None)),patch('app.messagebox.askyesno',return_value=True),patch('app.simpledialog.askinteger',return_value=1),patch('app.simpledialog.askstring',return_value=''),patch('app.messagebox.showerror') as error:
        check('GUI no-code roster load',a.load_xlsx(str(plain)) is True)
        a.available.selection_set(0);a.generate_codes()
        check('GUI selected missing issuance',bool(a.students[0]['code']) and not a.students[1]['code'] and a.code_dirty)
        a.available.selection_set(1);a.include();check('GUI prevents missing ID inclusion',not a.selected and error.called);error.reset_mock()
        a.run();check('GUI blocks unsaved-code PDF run',error.called and not a.busy);error.reset_mock()
        a.available.selection_clear(0,'end');a.generate_codes()
        with patch('app.filedialog.asksaveasfilename',return_value=str(tables/'gui-table.xlsx')),patch('app.messagebox.showinfo'):
            a.save_codes()
        check('GUI table saved and dirty state cleared',not a.code_dirty and Path(a.roster).name=='gui-table.xlsx')
        a.available.selection_set(0,'end');a.include();check('GUI generated IDs connected to PDF order',a.selected==[r['code'] for r in a.students])
        captured=[]
        def capture(*args,**kwargs):
            data=make_pdf(*args,**kwargs);captured.append((args[1],data));return data
        with patch('app.make_pdf',side_effect=capture):a.preview()
        check('GUI preview uses generated first student code',captured and captured[0][0]==a.selected[0])
        with fitz.open(stream=captured[0][1],filetype='pdf') as d:check('preview PDF contains exact generated code',d[0].get_text().strip()==a.selected[0])
        check('GUI no unexpected errors',not error.called)
    root.destroy()
check('original synthetic inputs unchanged',all(digest(p)==h for p,h in before.items()))
report={'passed':True,'synthetic_only':True,'network_socket_and_create_connection_blocked':True,'checks':checks,'check_count':len(checks)}
(base/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'checks':len(checks)}))
