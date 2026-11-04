"""Small, explicit page identity; never carries names or original scan metadata."""
import json
import unicodedata
import pymupdf as fitz

KEY='MHopaeIdentity'


def identity(code, klass, order):
    from core import validate_ids
    validate_ids([code],1)
    if not isinstance(klass,str) or not klass.strip() or any(unicodedata.category(c).startswith('C') for c in klass):
        raise ValueError('학급을 지정하십시오. 제어 문자는 사용할 수 없습니다.')
    if type(order) is not int or not 1<=order<=9999:raise ValueError('페이지순번은 1~9999의 정수입니다.')
    return {'schema':1,'student_code':code,'class':klass,'page_order':order}


def validate(value):
    if not isinstance(value,dict) or set(value)!={'schema','student_code','class','page_order'} or type(value['schema']) is not int or value['schema']!=1:
        raise ValueError('지원하지 않는 페이지 정보 형식')
    return identity(value['student_code'],value['class'],value['page_order'])


def write(page,value):
    value=validate(value)
    page.parent.xref_set_key(page.xref,KEY,fitz.get_pdf_str(json.dumps(value,ensure_ascii=False,separators=(',',':'))))


def read(page):
    kind,text=page.parent.xref_get_key(page.xref,KEY)
    if kind!='string':raise ValueError('학생코드·학급·페이지순번 정보 없음')
    if len(text)>100000:raise ValueError('페이지 정보가 지나치게 큽니다.')
    return validate(json.loads(text))


def box_header(doc,width,value):
    """Measured wrapping, distinct boxes; grow above body instead of covering it."""
    value=validate(value);font=fitz.Font('cjk')
    if width<70:raise ValueError('두 정보 박스를 표시하기에 페이지 폭이 너무 작습니다.')
    margin=min(12,width*.04);gap=6;usable=width-2*margin-gap;left=usable*.62;right=usable-left
    def wrap(text,size,space):
        lines=['']
        for char in text:
            if not font.has_glyph(ord(char)):raise ValueError('내장 글꼴로 표시할 수 없는 페이지 정보 문자')
            if font.text_length(lines[-1]+char,fontsize=size)>space:
                if not lines[-1]:raise ValueError('정보 박스 폭이 한 글자보다 작습니다.')
                lines.append('')
            lines[-1]+=char
        return lines
    code_size=min(16,max(8,left/5));info_size=min(10,max(7,right/5))
    code_lines=wrap(value['student_code'],code_size,left-12)
    class_lines=wrap('학급 '+value['class'],info_size,right-12)
    order_lines=wrap('페이지순번 '+str(value['page_order']),info_size,right-12)
    left_height=10+(code_size+4)*len(code_lines)
    right_height=10+(info_size+3)*(len(class_lines)+len(order_lines))
    header=max(48,left_height,right_height)+12
    return {'font':font,'height':header,'margin':margin,'gap':gap,'left':left,'right':right,
            'code_size':code_size,'info_size':info_size,'code_lines':code_lines,'info_lines':class_lines+order_lines}


def draw_header(page,layout):
    m=layout['margin'];left=layout['left'];gap=layout['gap'];h=layout['height']
    page.insert_font(fontname='idfont',fontbuffer=layout['font'].buffer)
    boxes=[fitz.Rect(m,5,m+left,h-5),fitz.Rect(m+left+gap,5,page.rect.width-m,h-5)]
    for box in boxes:page.draw_rect(box,color=(.25,.3,.35),width=.6)
    for lines,size,box in [(layout['code_lines'],layout['code_size'],boxes[0]),(layout['info_lines'],layout['info_size'],boxes[1])]:
        y=box.y0+size+6
        for line in lines:
            page.insert_text((box.x0+6,y),line,fontname='idfont',fontsize=size,color=(0,0,0));y+=size+4 if box==boxes[0] else size+3
