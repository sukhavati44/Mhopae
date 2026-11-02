"""Offline raster-only PDF export. No source PDF objects are copied."""
import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
import pymupdf as fitz
from openpyxl import load_workbook

RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{s}{i}' for s in ('COM', 'LPT') for i in range(1, 10)}

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def validate_ids(ids, count):
    if len(ids) != count:
        raise ValueError(f'페이지 {count}개 / 선택 ID {len(ids)}개: 개수가 다릅니다.')
    for ident in ids:
        if (not isinstance(ident, str) or not ident.strip()
                or any(c in '<>:"/\\|?*' or unicodedata.category(c).startswith('C') for c in ident)
                or ident.endswith((' ', '.')) or ident in ('.', '..')):
            raise ValueError('ID는 한글·영문·숫자·공백·하이픈 등 파일명으로 사용 가능한 문자여야 합니다. Windows 금지 문자 < > : " / \\ | ? * 및 끝의 공백·점은 사용할 수 없습니다. 원본 값은 자동 수정하지 않습니다.')
        if ident.split('.')[0].rstrip(' ').upper() in RESERVED:
            raise ValueError('Windows 예약 이름은 ID로 사용할 수 없습니다.')

def read_roster_details(path, sheet, id_column):
    """Read a code table or assessment export; keep class and original row order."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = book[sheet] if sheet else book[book.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        # The manager's score export uses title/key/header rows 1/2/3.
        matches = [(i, [str(v or '') for v in row]) for i, row in enumerate(rows[:3])
                   if id_column in [str(v or '') for v in row]]
        if not matches:
            raise ValueError('첫 3행에서 ID 열 이름을 찾지 못했습니다.')
        header_row, headers = matches[0]
        if headers.count(id_column) != 1:
            raise ValueError('중복 ID 열 이름은 허용하지 않습니다.')
        col = headers.index(id_column)
        class_cols = [i for i, h in enumerate(headers) if h in ('반', '학급')]
        if len(class_cols) > 1:
            raise ValueError('반·학급 열이 중복되어 분류할 수 없습니다.')
        records = []
        for row in rows[header_row + 1:]:
            if not any(v is not None for v in row):
                continue
            ident = row[col] if col < len(row) else None
            validate_ids([ident], 1)
            klass = str(row[class_cols[0]] or '') if class_cols else ''
            records.append((ident, ' | '.join('' if v is None else str(v) for v in row), klass))
        return records
    finally:
        book.close()

def read_roster(path, sheet, id_column):
    """Return rows to GUI only; never persist student columns."""
    return [(ident, display) for ident, display, _ in read_roster_details(path, sheet, id_column)]

def check_cut(cut, dpi):
    if not isinstance(cut, (int, float)) or not math.isfinite(cut) or not 0 <= cut < 1:
        raise ValueError('절단선은 0% 이상 100% 미만이어야 합니다.')
    if dpi not in (150, 200, 300):
        raise ValueError('DPI는 150, 200, 300 중 선택하십시오.')

def render_bottom(page, cut, dpi):
    check_cut(cut, dpi)
    rect = page.rect
    # Round towards the retained region, never include a pixel above the cut.
    top = math.ceil((rect.y0 + rect.height * cut) * dpi / 72 - 1e-7) * 72 / dpi
    clip = fitz.Rect(rect.x0, top, rect.x1, rect.y1)
    if clip.height * dpi / 72 < 1:
        raise ValueError('절단 후 남는 영역이 1픽셀보다 작습니다.')
    # annots=False also excludes rendered annotations, including below the cut.
    return page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csRGB, alpha=False, annots=False)

def make_pdf(page, ident, cut, dpi):
    pix = render_bottom(page, cut, dpi)
    doc = fitz.open()
    try:
        width, height = pix.width * 72 / dpi, pix.height * 72 / dpi
        # Built-in embedded Unicode font: portable, no Windows font dependency.
        font = fitz.Font('cjk')
        if any(not font.has_glyph(ord(char)) for char in ident):
            raise ValueError('내장 글꼴로 표시할 수 없는 ID 문자가 있습니다. 원본 ID는 변경하지 않습니다.')
        text_width = font.text_length(ident, fontsize=18)
        font_size = min(18, 18 * max(0, width - 24) / max(1, text_width))
        lines = [ident]
        if font_size < 10:
            # Preserve every character; wrap display instead of limiting ID length.
            font_size = 18
            lines = ['']
            for char in ident:
                if font.text_length(lines[-1] + char, fontsize=font_size) > width - 24:
                    if not lines[-1]:
                        raise ValueError('출력 페이지 너비가 ID 한 글자보다 작습니다.')
                    lines.append('')
                lines[-1] += char
        header = 36 + (len(lines) - 1) * 24
        out = doc.new_page(width=width, height=height + header)
        out.insert_font(fontname='idfont', fontbuffer=font.buffer)
        for i, line in enumerate(lines):
            out.insert_text((12, 24 + i * 24), line, fontsize=font_size, fontname='idfont', color=(0, 0, 0))
        out.insert_image(fitz.Rect(0, header, width, height + header), stream=pix.tobytes('png'))
        doc.set_metadata({})
        doc.subset_fonts()
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()

def plan_names(ids, folder):
    folder = Path(folder)
    used = {p.name.casefold() for p in folder.iterdir()}
    planned, collisions = [], []
    for ident in ids:
        n, name = 1, f'{ident}.pdf'
        if name.casefold() in used:
            collisions.append(ident)
        while name.casefold() in used:
            n += 1
            name = f'{ident}_{n:02d}.pdf'
        used.add(name.casefold())
        planned.append(folder / name)
    return planned, collisions

def source_page(position, count, reverse=False):
    if not 0 <= position < count:
        raise ValueError('페이지 범위를 벗어났습니다.')
    return count - 1 - position if reverse else position

def roster_sheets(path):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()

def export_batch(source, ids, folder, cut, dpi=300, allow_collisions=False, roster=None, progress=None, reverse=False):
    source, folder = Path(source).resolve(), Path(folder).resolve()
    check_cut(cut, dpi)
    if not folder.is_dir():
        raise ValueError('기존 로컬 출력 폴더를 선택하십시오.')
    if source.parent == folder or (roster and Path(roster).resolve().parent == folder):
        raise ValueError('출력 폴더는 입력 PDF 및 명렬표 폴더와 분리하십시오.')
    hashes = {str(source): digest(source)}
    if roster:
        hashes[str(Path(roster).resolve())] = digest(roster)
    with fitz.open(source) as src:
        if src.needs_pass or not src.is_pdf or not src.page_count:
            raise ValueError('암호화·빈 문서 또는 PDF가 아닌 파일입니다.')
        validate_ids(ids, src.page_count)
        paths, collisions = plan_names(ids, folder)
        if collisions and not allow_collisions:
            raise ValueError('기존 파일 충돌: 사용자 확인이 필요합니다.')
        # Create the audit log exclusively before any output. Do not overwrite logs.
        run = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        log = folder / f'run_{run}.csv'
        results = []
        with log.open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['run', 'source_pdf', 'page', 'anonymous_id', 'output', 'cut', 'dpi', 'status'])
            for i, (ident, path) in enumerate(zip(ids, paths)):
                status = 'ok'
                try:
                    data = make_pdf(src[source_page(i, src.page_count, reverse)], ident, cut, dpi)
                    # Exclusive creation protects against races after name planning.
                    with path.open('xb') as f:
                        f.write(data)
                    with fitz.open(path) as check:
                        if check.page_count != 1 or ''.join(check[0].get_text().splitlines()) != ident:
                            raise RuntimeError('출력 재열람 검증 실패')
                except Exception as exc:
                    status = f'failed:{type(exc).__name__}'
                writer.writerow([run, source.name, source_page(i, src.page_count, reverse) + 1, ident, path.name, cut, dpi, status])
                stream.flush()
                results.append((source_page(i, src.page_count, reverse) + 1, ident, path.name, status))
                if progress:
                    progress(i + 1, src.page_count)
    unchanged = all(digest(path) == before for path, before in hashes.items())
    if not unchanged:
        raise RuntimeError('입력 파일 해시가 변경되었습니다. 처리를 중단하고 원인을 확인하십시오.')
    with (folder / f'run_{run}_hashes.json').open('x', encoding='utf-8') as f:
        json.dump({'source_pdf_sha256': hashes[str(source)],
                   'roster_sha256': hashes.get(str(Path(roster).resolve())) if roster else None,
                   'inputs_unchanged': unchanged}, f, indent=2)
    return results, log, hashes


def export_bundle(source, ids, folder, cut, dpi=300, allow_collisions=False, roster=None, progress=None, reverse=False):
    """Combine only newly rasterized, anonymized pages, never original objects."""
    source, folder = Path(source).resolve(), Path(folder).resolve()
    check_cut(cut, dpi)
    if not folder.is_dir():
        raise ValueError('기존 로컬 출력 폴더를 선택하십시오.')
    if source.parent == folder or (roster and Path(roster).resolve().parent == folder):
        raise ValueError('출력 폴더는 입력 PDF 및 명렬표 폴더와 분리하십시오.')
    hashes = {str(source): digest(source)}
    if roster:
        hashes[str(Path(roster).resolve())] = digest(roster)
    with fitz.open(source) as src:
        if src.needs_pass or not src.is_pdf or not src.page_count:
            raise ValueError('암호화·빈 문서 또는 PDF가 아닌 파일입니다.')
        validate_ids(ids, src.page_count)
        paths, collisions = plan_names(['비식별_묶음'], folder)
        if collisions and not allow_collisions:
            raise ValueError('기존 파일 충돌: 사용자 확인이 필요합니다.')
        path = paths[0]
        run = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        log = folder / f'run_{run}.csv'
        results = []
        # Open exclusively before work, so even a failed run has a preserved log.
        with log.open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['run', 'source_pdf', 'page', 'anonymous_id', 'output',
                             'cut', 'dpi', 'status', 'output_page'])
            status = 'ok'
            try:
                with fitz.open() as combined:
                    for i, ident in enumerate(ids):
                        data = make_pdf(src[source_page(i, src.page_count, reverse)], ident, cut, dpi)
                        with fitz.open(stream=data, filetype='pdf') as single:
                            combined.insert_pdf(single)
                        if progress:
                            progress(i + 1, src.page_count)
                    combined.set_metadata({})
                    data = combined.tobytes(garbage=4, deflate=True)
                # Validate all pages before creating the single output file.
                with fitz.open(stream=data, filetype='pdf') as check:
                    if check.page_count != len(ids) or any(
                            ''.join(page.get_text().splitlines()) != ident
                            for page, ident in zip(check, ids)):
                        raise RuntimeError('묶음 출력 검증 실패')
                if not all(digest(p) == h for p, h in hashes.items()):
                    raise RuntimeError('입력 파일이 변경되었습니다.')
                with path.open('xb') as target:
                    target.write(data)
                with fitz.open(path) as check:
                    if check.page_count != len(ids) or any(
                            ''.join(page.get_text().splitlines()) != ident
                            for page, ident in zip(check, ids)):
                        raise RuntimeError('묶음 출력 재열람 검증 실패')
            except Exception as exc:
                # A bundle is one result: never report a partial document as success.
                status = f'failed:{type(exc).__name__}'
            for i, ident in enumerate(ids):
                page_number = source_page(i, src.page_count, reverse) + 1
                writer.writerow([run, source.name, page_number, ident, path.name,
                                 cut, dpi, status, i + 1])
                results.append((page_number, ident, path.name, status))
            stream.flush()
    unchanged = all(digest(p) == h for p, h in hashes.items())
    if not unchanged:
        raise RuntimeError('입력 파일 해시가 변경되었습니다. 처리를 중단하고 원인을 확인하십시오.')
    with (folder / f'run_{run}_hashes.json').open('x', encoding='utf-8') as f:
        json.dump({'source_pdf_sha256': hashes[str(source)],
                   'roster_sha256': hashes.get(str(Path(roster).resolve())) if roster else None,
                   'inputs_unchanged': unchanged, 'output_mode': 'bundle'}, f, indent=2)
    return results, log, hashes
