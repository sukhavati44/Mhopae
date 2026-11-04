"""Release checks use only generated, temporary synthetic data."""
from pathlib import Path
import compileall, sys, tempfile, time
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source'))
assert compileall.compile_dir(ROOT / 'source', quiet=1)
import tkinter as tk
from app import App
from core import fitz, digest
from openpyxl import Workbook
from roster_codes import read_students
from scan_mapping import ScanBlock, build_mapping, inspect_mapping
from page_output import export_pages, import_pages
from page_identity import read
from page_rules import PageRules
from work_session import save, load

r=tk.Tk();a=App(r);r.update()
until=time.monotonic()+.35
while time.monotonic()<until:r.update();time.sleep(.01)
assert a.visual_guide.current is a.controls['load_roster']
assert a.controls['load_roster'].cget('text')=='명단 XLSX 열기'
r.destroy()

with tempfile.TemporaryDirectory(dir=ROOT, prefix='synthetic_release_') as temp:
    p=Path(temp);pdf=p/'synthetic.pdf';xlsx=p/'synthetic.xlsx'
    wb=Workbook();ws=wb.active;ws.append(['반','번호','이름','학생코드'])
    for n in range(1,4):ws.append(['1반',f'{n:02d}',f'FAKE STUDENT {n}',f'TEST-01-{n:03d}'])
    wb.save(xlsx);students=read_students(xlsx,allow_partial=True)
    assert len(students)==3 and students[0]['code']=='TEST-01-001'
    with fitz.open() as doc:
        for n in range(1,4):
            page=doc.new_page(width=300,height=400)
            page.insert_text((20,20),'FAKE PRIVATE HEADER')
            page.insert_text((20,150),f'SYNTHETIC ANSWER {n}')
        doc.save(pdf)
    before=digest(pdf)
    (p/'out').mkdir()
    rows=build_mapping([ScanBlock(str(pdf),1,3)],students,'single',1)
    assert list(inspect_mapping(rows).values())==['출력 가능']*3
    with patch('socket.socket',side_effect=RuntimeError('offline check')):
        report,log=export_pages(rows,p/'out','class',300,{str(pdf):PageRules(.18)})
    assert report['ready_pages']==3 and report['inputs_unchanged']
    output=p/'out'/report['results'][0]['file']
    with fitz.open(output) as doc:
        assert len(doc)==3
        assert [read(page)['student_code'] for page in doc]==[s['code'] for s in students]
        assert all(read(page)['page_order']==1 for page in doc)
        assert all('FAKE PRIVATE' not in page.get_text() for page in doc)
    assert len(import_pages([output]))==3 and digest(pdf)==before
    # Selected output reuses the engine and must not replace the prior output.
    previous=digest(output)
    with patch('socket.socket',side_effect=RuntimeError('offline check')):
        small,_=export_pages([rows[1]],p/'out','class',300,{str(pdf):PageRules(.18)})
    assert small['ready_pages']==1 and digest(output)==previous
    assert small['results'][0]['file']!=output.name
print('Release smoke: source/Tk/synthetic roster/PDF/offline export/identity/collision passed')
