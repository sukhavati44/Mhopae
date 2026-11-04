"""Offline student-code preparation. Identity data stays in teacher-selected XLSX."""
import io
import re
import secrets
from pathlib import Path
from copy import deepcopy
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from core import validate_ids

ALIASES = {'class': ('반', '학급'), 'number': ('번호', '출석번호'),
           'name': ('이름', '성명', '학생명'),
           'code': ('학생코드', '비식별코드', '익명ID', '식별번호', '익명코드')}
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def validate_students(students):
    if not students:
        raise ValueError('학생 행이 없는 명단입니다.')
    seats, codes = set(), set()
    for row in students:
        where = f"{row.get('source_row', '?')}행"
        for field in ('class', 'number', 'name'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f'{where}: 학급·번호·이름은 모두 필요합니다. 원본을 확인하십시오.')
        number = row['number'].strip()
        if not re.fullmatch(r'[0-9]+', number) or int(number) <= 0:
            raise ValueError(f'{where}: 번호는 양의 정수여야 합니다. 추정 수정하지 않습니다.')
        klass = row['class'].strip()
        canonical_class = klass[:-1].strip() if klass.endswith('반') else klass
        if canonical_class.isascii() and canonical_class.isdigit():
            canonical_class = str(int(canonical_class))
        seat = (canonical_class, int(number))
        if seat in seats:
            raise ValueError(f'{where}: 같은 학급·번호의 학생 행이 중복됩니다. 원본을 확인하십시오.')
        seats.add(seat)
        code = row.get('code')
        if code:
            validate_ids([code], 1)
            if code.casefold() in codes:
                raise ValueError(f'{where}: 학생 간 코드가 중복됩니다. 자동 변경하지 않습니다.')
            codes.add(code.casefold())
        elif code != '':
            raise ValueError(f'{where}: 비어 있는 코드는 빈 문자열이어야 합니다.')


def read_students(path, sheet=None, code_column=None, allow_partial=False):
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        ws = book[sheet] if sheet else book[book.sheetnames[0]]
        rows = list(ws.iter_rows())
        match = None
        for i, row in enumerate(rows[:3]):
            headers = [str(c.value or '').strip() for c in row]
            if (all(any(alias in headers for alias in ALIASES[k]) for k in ('class','number','name')) or (allow_partial and any(alias in headers for alias in ALIASES['code']))):
                match = (i, headers)
                break
        if match is None:
            raise ValueError('첫 3행에 학급(반)·번호·이름(성명) 열이 필요합니다. 원본을 확인하십시오.')
        header_row, headers = match
        columns = {}
        for field, aliases in ALIASES.items():
            if field == 'code' and code_column:
                aliases = (code_column,)
            found = [j for j, h in enumerate(headers) if h in aliases]
            if len(found) > 1:
                raise ValueError(f'{field}에 해당하는 열이 중복됩니다. 사용할 열을 확인하십시오.')
            if not found and (field != 'code' or code_column) and not (allow_partial and field!='code'):
                raise ValueError('지정한 열을 찾지 못했습니다. 열 이름을 확인하십시오.')
            columns[field] = found[0] if found else None
        students = []
        for i, row in enumerate(rows[header_row + 1:], header_row + 2):
            if not any(c.value is not None for c in row):
                continue
            student = {'source_row': i}
            for field, col in columns.items():
                cell = row[col] if col is not None and col < len(row) else None
                if cell is not None and cell.data_type == 'f':
                    raise ValueError(f'{i}행: 학급·번호·이름·코드의 수식은 허용하지 않습니다. 값을 확인하십시오.')
                value = cell.value if cell is not None else None
                if field == 'code':
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f'{i}행: 기존 코드는 문자열이어야 합니다. 자동 변환하지 않습니다.')
                    student[field] = '' if value is None else value
                else:
                    if isinstance(value, bool) or (value is not None and not isinstance(value, (str,int,float))):
                        raise ValueError(f'{i}행: 필수정보 형식을 확인하십시오.')
                    if isinstance(value, float) and value.is_integer():
                        value = int(value)
                    text = '' if value is None else str(value)
                    if field == 'number' and isinstance(value, int) and cell is not None and re.fullmatch(r'0+', cell.number_format):
                        text = str(value).zfill(len(cell.number_format))
                    student[field] = text
            students.append(student)
        if allow_partial:
            if not students:raise ValueError('학생 행이 없는 명단입니다.')
            codes=set();seats=set()
            for student in students:
                code=student['code']
                if code:
                    validate_ids([code],1)
                    if code.casefold() in codes:raise ValueError('학생 간 코드 중복: 원본 코드표를 확인하십시오.')
                    codes.add(code.casefold())
                if student['number'] and (not student['number'].isdigit() or int(student['number'])<1):raise ValueError('번호 형식을 확인하십시오.')
                if student['class'] and student['number']:
                    seat=(student['class'],int(student['number']))
                    if seat in seats:raise ValueError('같은 학급·번호 중복: 원본 코드표를 확인하십시오.')
                    seats.add(seat)
        else:validate_students(students)
        return students
    finally:
        book.close()


def issue_missing(students, indices=None):
    validate_students(students)
    result = deepcopy(students)
    targets = list(range(len(result))) if indices is None else list(indices)
    if any(not isinstance(i, int) or not 0 <= i < len(result) for i in targets):
        raise ValueError('발급 대상 행 범위가 잘못되었습니다.')
    used = {r['code'].casefold() for r in result if r['code']}
    issued = 0
    for i in dict.fromkeys(targets):
        if result[i]['code']:
            continue
        for _ in range(1000):
            code = 'HP-' + ''.join(secrets.choice(ALPHABET) for _ in range(10))
            if code.casefold() not in used:
                break
        else:
            raise RuntimeError('난수 코드 충돌이 반복되었습니다. 기존 코드를 보존하고 중단합니다.')
        result[i]['code'] = code
        used.add(code.casefold())
        issued += 1
    validate_students(result)
    return result, issued


def save_code_table(students, path, forbidden_folders=()):
    validate_students(students)
    path = Path(path).resolve()
    if path.suffix.lower() != '.xlsx':
        raise ValueError('새 XLSX 파일 이름을 지정하십시오.')
    if any(path.parent == Path(p).resolve() for p in forbidden_folders if p):
        raise ValueError('실명 대조표는 PDF 입력·출력 폴더와 분리해 저장하십시오.')
    book = Workbook()
    ws = book.active; ws.title = '학생코드 대조표'
    # Keep the manager's existing import header; the GUI calls it 비식별코드.
    ws.append(['학급', '번호', '이름', '학생코드'])
    for row in students:
        ws.append([row[k] for k in ('class','number','name','code')])
        for cell in ws[ws.max_row]:
            cell.data_type = 's'  # Never turn a name/code beginning with '=' into a formula.
            cell.number_format = '@'
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='31556F')
    for col,width in zip('ABCD',(16,12,22,34)):
        ws.column_dimensions[col].width=width
    ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
    data=io.BytesIO(); book.save(data); book.close()
    # Validate the complete XLSX before exclusive creation, including blank codes.
    check = load_workbook(io.BytesIO(data.getvalue()),read_only=True,data_only=False)
    try:
        actual=list(check.active.iter_rows(min_row=2,values_only=True))
        expected=[tuple(r[k] or None for k in ('class','number','name','code')) for r in students]
        if actual != expected:
            raise RuntimeError('대조표 검증 실패: 저장하지 않았습니다.')
    finally:
        check.close()
    with path.open('xb') as stream:
        stream.write(data.getvalue())
    return path
