"""Conservative explicit DPI discovery, distinct from effective placement DPI."""
import io
from PIL import Image
import pymupdf as fitz


def detect_dpi(path):
    pages=[];effective=[]
    with fitz.open(path) as doc:
        for page in doc:
            values=[];all_explicit=True
            for info in page.get_image_info(xrefs=True):
                w,h=info['width'],info['height'];box=fitz.Rect(info['bbox'])
                if box.width and box.height:effective.append((round(w*72/box.width,2),round(h*72/box.height,2)))
                xref=info.get('xref',0);dpi=None
                # Only original JPEG density markers; extract_image PNG often invents DPI.
                if xref and doc.xref_get_key(xref,'Filter')[1]=='/DCTDecode':
                    try:
                        with Image.open(io.BytesIO(doc.xref_stream_raw(xref))) as im:
                            if im.info.get('jfif_unit') in (1,2):dpi=im.info.get('dpi')
                            exif=im.getexif()
                            if dpi is None and exif.get(296)==2 and 282 in exif and 283 in exif:
                                dpi=(float(exif[282]),float(exif[283]))
                    except (ValueError,OSError):pass
                if dpi and abs(dpi[0]-dpi[1])<.2 and any(abs(dpi[0]-n)<.2 for n in (300,400)):
                    values.append(min((300,400),key=lambda n:abs(n-dpi[0])))
                else:all_explicit=False
            pages.append(values[0] if values and all_explicit and len(set(values))==1 else None)
    explicit=pages[0] if pages and None not in pages and len(set(pages))==1 else None
    return {'selected':explicit or 300,'explicit':explicit,'per_page_explicit':pages,
            'effective_image_dpi':effective,
            'reason':'JPEG에 명시된 해상도가 모든 페이지에서 일치' if explicit else '명시 정보 미확인·비표준·충돌: 기본 300dpi (유효 DPI 계산값과 구분)'}
