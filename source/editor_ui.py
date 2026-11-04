"""Mask editing and independent folder merge UI for the existing Tk application."""
import io
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk, ImageDraw
from core import fitz, source_page
from page_rules import PageRules, rectangle
from dpi_info import detect_dpi
from student_merge import plan_merge, execute_merge
from roster_codes import read_students


class EditorUI:
    def init_editor(self,right):
        self.rules=PageRules(self.cut)
        self.cut_local=tk.BooleanVar(value=False);self.mask_local=tk.BooleanVar(value=False)
        self.edit_mode=tk.StringVar(value='절단선');self.zoom=tk.StringVar(value='맞춤')
        self.mask_selection=None;self.drag_state=None;self.exception_pages=[]
        toolbar=ttk.Frame(right,padding=(8,2));toolbar.pack(fill='x')
        ttk.Label(toolbar,text='편집').pack(side='left')
        ttk.Combobox(toolbar,textvariable=self.edit_mode,values=['절단선','마스크 추가','마스크 이동·크기'],width=17,state='readonly').pack(side='left',padx=4)
        ttk.Checkbutton(toolbar,text='절단 개별',variable=self.cut_local,command=lambda:self.change_scope('cut')).pack(side='left')
        ttk.Checkbutton(toolbar,text='마스크 개별',variable=self.mask_local,command=lambda:self.change_scope('mask')).pack(side='left')
        ttk.Label(toolbar,text='확대').pack(side='left',padx=(8,0))
        z=ttk.Combobox(toolbar,textvariable=self.zoom,values=['맞춤','75%','100%','150%'],width=6,state='readonly');z.pack(side='left')
        z.bind('<<ComboboxSelected>>',lambda e:self.show_page())
        bar=ttk.Frame(right,padding=(8,2));bar.pack(fill='x')
        ttk.Button(bar,text='선택 마스크 삭제',command=self.delete_mask).pack(side='left')
        ttk.Button(bar,text='현재 규칙 마스크 비우기',command=self.clear_masks).pack(side='left',padx=4)
        ttk.Label(bar,text='예외 이동').pack(side='left')
        self.exception_choice=tk.StringVar()
        self.exception_combo=ttk.Combobox(bar,textvariable=self.exception_choice,state='readonly',width=21)
        self.exception_combo.pack(side='left',padx=4);self.exception_combo.bind('<<ComboboxSelected>>',self.jump_exception)
        self.rule_info=tk.StringVar(value='기본: 전체 절단·마스크 / 체크하면 현재 원본 페이지의 예외')
        ttk.Label(right,textvariable=self.rule_info,wraplength=650).pack(fill='x',padx=8)
        self.dpi_info=tk.StringVar(value='명시 DPI 미확인 · 출력 기본 300dpi')
        ttk.Label(right,textvariable=self.dpi_info,style='Muted.TLabel',wraplength=650).pack(fill='x',padx=8)

    def current_original(self):
        return source_page(self.page.get()-1,self.count,self.direction.get()=='전체 페이지 역순')

    def change_scope(self,kind):
        if self.busy or not self.pdf:return
        page=self.current_original();cut,masks=self.rules.for_page(page)
        if kind=='cut':
            if self.cut_local.get():self.rules.cuts[page]=cut
            else:self.rules.cuts.pop(page,None)
        else:
            if self.mask_local.get():self.rules.page_masks[page]=masks
            else:self.rules.page_masks.pop(page,None)
        self.rules_changed()

    def rules_changed(self):
        for review in getattr(self,'page_reviews',[]):
            if review.frame.winfo_exists():review.update_rule_status()
        self.reviewed.clear();self.invalidate_previews();self.mask_selection=None
        self.show_page();self.update_controls()

    def sync_rule_ui(self,page):
        self.cut_local.set(page in self.rules.cuts);self.mask_local.set(page in self.rules.page_masks)
        self.exception_pages=self.rules.exceptions()
        labels=[f'원본 {p+1}: '+('절단 ' if p in self.rules.cuts else '')+('마스크' if p in self.rules.page_masks else '') for p in self.exception_pages]
        self.exception_combo.configure(values=labels)
        self.exception_choice.set(labels[self.exception_pages.index(page)] if page in self.exception_pages else '')
        cut,masks=self.rules.for_page(page)
        self.rule_info.set(f'원본 {page+1} · 절단 {cut:.2%} ({"개별" if page in self.rules.cuts else "전체"}) · 마스크 {len(masks)}개 ({"개별" if page in self.rules.page_masks else "전체"}) · 빨강은 편집 표시, 출력은 흰색')

    def jump_exception(self,event=None):
        if self.busy:return
        index=self.exception_combo.current()
        if 0<=index<len(self.exception_pages):
            original=self.exception_pages[index]
            position=self.count-1-original if self.direction.get()=='전체 페이지 역순' else original
            self.page.set(position+1);self.mask_selection=None;self.show_page()

    def current_masks(self):
        p=self.current_original()
        return self.rules.page_masks[p] if p in self.rules.page_masks else self.rules.masks

    def delete_mask(self):
        if self.busy or not self.pdf:return
        masks=self.current_masks()
        if self.mask_selection is not None and 0<=self.mask_selection<len(masks):
            masks.pop(self.mask_selection);self.rules_changed()

    def clear_masks(self):
        if self.busy or not self.pdf:return
        self.current_masks().clear();self.rules_changed()

    def show_page(self):
        if not self.pdf:return
        try:
            original=self.current_original()
            with fitz.open(self.pdf) as doc:
                pix=doc[original].get_pixmap(matrix=fitz.Matrix(1,1),annots=False)
                im=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
            if self.zoom.get()=='맞춤':im.thumbnail((max(50,self.canvas.winfo_width()-20),max(50,self.canvas.winfo_height()-20)))
            else:
                scale=int(self.zoom.get().rstrip('%'))/100
                im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))))
            w,h=im.size;x=max(10,(self.canvas.winfo_width()-w)/2);y=10
            cut,masks=self.rules.for_page(original)
            overlay=Image.new('RGBA',im.size);draw=ImageDraw.Draw(overlay)
            for rect in masks:
                a,b,c,d=rect;draw.rectangle((a*w,b*h,c*w,d*h),fill=(255,0,0,75))
            im=Image.alpha_composite(im.convert('RGBA'),overlay)
            self.photo=ImageTk.PhotoImage(im);self.canvas.delete('all');self.canvas.create_image(x,y,anchor='nw',image=self.photo)
            self.preview_rect=(x,y,w,h)
            self.canvas.create_line(x,y+h*cut,x+w,y+h*cut,fill='red',width=3)
            for i,(a,b,c,d) in enumerate(masks):
                color='#0044CC' if i==self.mask_selection else '#D00000'
                self.canvas.create_rectangle(x+a*w,y+b*h,x+c*w,y+d*h,outline=color,width=2)
                self.canvas.create_text(x+a*w+3,y+b*h+3,text=str(i+1),anchor='nw',fill=color)
                if i==self.mask_selection:self.canvas.create_rectangle(x+c*w-5,y+d*h-5,x+c*w+5,y+d*h+5,fill=color)
            self.canvas.configure(scrollregion=(0,0,x+w+10,y+h+10))
            self.sync_rule_ui(original);self.update_counts()
            self.page_info.set(f'처리 {self.page.get()}/{self.count} · 원본 {original+1}'+(' · 개별 적용' if original in self.rules.exceptions() else ''))
        except Exception as exc:self.status.set(f'미리보기 오류: {exc}')

    def normalized_event(self,event):
        if not self.preview_rect:return None
        x,y,w,h=self.preview_rect
        return (max(0,min(1,(self.canvas.canvasx(event.x)-x)/w)),max(0,min(1,(self.canvas.canvasy(event.y)-y)/h)))

    def click(self,event):
        if self.busy or not self.pdf:return
        pt=self.normalized_event(event)
        if pt is None:return
        x,y,w,h=self.preview_rect
        cx,cy=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y)
        if not x<=cx<=x+w or not y<=cy<=y+h:return
        a,b=pt
        if self.edit_mode.get()=='절단선':
            value=min(b,1-1e-4);p=self.current_original()
            if p in self.rules.cuts:self.rules.cuts[p]=value
            else:self.rules.cut=value;self.cut=value
            self.rules_changed();return
        if self.edit_mode.get()=='마스크 추가':self.drag_state=('new',pt);return
        masks=self.current_masks();self.mask_selection=None
        for i in reversed(range(len(masks))):
            l,t,r,bottom=masks[i]
            if abs(a-r)*w<=9 and abs(b-bottom)*h<=9:
                self.mask_selection=i;self.drag_state=('size',pt,tuple(masks[i]));break
            if l<=a<=r and t<=b<=bottom:
                self.mask_selection=i;self.drag_state=('move',pt,tuple(masks[i]));break
        self.show_page()

    def drag(self,event):
        if self.busy or not self.drag_state:return
        pt=self.normalized_event(event)
        if pt is None:return
        mode,start,*saved=self.drag_state
        if mode=='new':
            x,y,w,h=self.preview_rect;self.canvas.delete('rubber')
            self.canvas.create_rectangle(x+start[0]*w,y+start[1]*h,x+pt[0]*w,y+pt[1]*h,outline='red',width=2,tags='rubber')
        else:
            l,t,r,b=saved[0]
            if mode=='move':
                dx=max(-l,min(1-r,pt[0]-start[0]));dy=max(-t,min(1-b,pt[1]-start[1]));rect=(l+dx,t+dy,r+dx,b+dy)
            else:rect=(l,t,max(l+.001,pt[0]),max(t+.001,pt[1]))
            self.current_masks()[self.mask_selection]=rectangle(rect);self.show_page()

    def release(self,event):
        if self.busy or not self.drag_state:return
        mode,start,*_=self.drag_state
        if mode=='new':
            end=self.normalized_event(event)
            if end and abs(end[0]-start[0])>.002 and abs(end[1]-start[1])>.002:
                self.current_masks().append(rectangle((min(start[0],end[0]),min(start[1],end[1]),max(start[0],end[0]),max(start[1],end[1]))))
        selected=(len(self.current_masks())-1 if self.current_masks() else None) if mode=='new' else self.mask_selection
        self.drag_state=None;self.rules_changed()
        self.mask_selection=selected;self.show_page()

    def merge_dialog(self):
        from visual_guide import ButtonGuide, merge_button
        if self.busy:return
        top=tk.Toplevel(self.root);top.title('학생별 PDF 병합 — 비식별화된 파일 전용');top.geometry('850x620');top.transient(self.root)
        ttk.Label(top,text='이미 비식별화된 단일 페이지 PDF 전용 · 병합은 개인정보를 제거하지 않습니다.',wraplength=800).pack(fill='x',padx=12,pady=8)
        folder=tk.StringVar();table=tk.StringVar();plan=[None]
        ttk.Entry(top,textvariable=folder,state='readonly').pack(fill='x',padx=12)
        def choose_folder():
            p=filedialog.askdirectory(parent=top,title='비식별 개별 PDF 폴더')
            if p:folder.set(p);plan[0]=None
        guide_folder=ttk.Button(top,text='PDF 폴더 선택 (필수)',command=choose_folder);guide_folder.pack(anchor='w',padx=12)
        ttk.Entry(top,textvariable=table,state='readonly').pack(fill='x',padx=12,pady=(8,0))
        def choose_table():
            p=filedialog.askopenfilename(parent=top,filetypes=[('기존 코드표','*.xlsx')])
            if p:table.set(p);plan[0]=None
        bar=ttk.Frame(top);bar.pack(fill='x',padx=12)
        ttk.Button(bar,text='기존 코드표 선택 (보조)',command=choose_table).pack(side='left')
        ttk.Button(bar,text='코드표 없이',command=lambda:(table.set(''),plan.__setitem__(0,None))).pack(side='left',padx=5)
        result=tk.Text(top,wrap='word',height=18);result.pack(fill='both',expand=True,padx=12,pady=8)
        def display(text):result.delete('1.0','end');result.insert('end',text)
        def inspect():
            try:
                codes=None
                if table.get():
                    options=self.choose_roster_options(table.get())
                    if options is None:return
                    sheet,column=options
                    rows=read_students(table.get(),sheet,column)
                    codes=[r['code'] for r in rows]
                    if any(not c for c in codes):raise ValueError('선택 코드표에 빈 코드가 있습니다.')
                plan[0]=plan_merge(folder.get(),codes)
                display('\n'.join(f'{g["code"]}: '+('; '.join(g['issues']) if g['issues'] else f'준비 {len(g["files"])}면 → {Path(g["output"]).name}') for g in plan[0]['groups'])+'\n'+'\n'.join(f'{g["file"]}: {g["reason"]}' for g in plan[0]['unmatched']))
            except Exception as exc:plan[0]=None;display(str(exc))
        def execute():
            if plan[0] is None:inspect()
            if plan[0] is None:return
            if not self.notice('askyesno','비식별 입력 확인','입력 PDF는 이미 비식별화되었습니까? 정상 그룹만 all/코드(all).pdf로 생성하고 문제 그룹은 보류합니다.',parent=top):return
            try:
                results,log=execute_merge(plan[0]);display('\n'.join(f'{r["code"]}: {r["status"]} · {r["reason"]}' for r in results)+f'\n성공 {sum(r["status"]=="성공" for r in results)} / 보류·실패 {sum(r["status"]!="성공" for r in results)}\n기록 {log}')
                plan[0]=None
            except Exception as exc:display(str(exc))
        bottom=ttk.Frame(top);bottom.pack(fill='x',padx=12,pady=8)
        guide_inspect=ttk.Button(bottom,text='파일 검사·보류 사유 확인',command=inspect);guide_inspect.pack(side='left')
        guide_execute=ttk.Button(bottom,text='정상 그룹 병합',command=execute,style='Accent.TButton');guide_execute.pack(side='right')
        self.place_dialog(top)
        top.visual_guide=ButtonGuide(top,lambda:merge_button(folder.get(),plan[0],guide_folder,guide_inspect,guide_execute))
