"""Three-step scan mapping and page-identity regrouping. No network access."""
import io
import threading
import copy
import queue
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
from core import fitz, make_pdf, digest
from roster_codes import read_students
from page_identity import identity
from page_rules import PageRules
from scan_mapping import ScanBlock, build_mapping, inspect_mapping
from page_output import import_pages, export_pages, relabel_pdf
from ux_helpers import student_label,categories,output_estimate,MODES
from dataclasses import asdict
import work_session
from visual_guide import ButtonGuide, batch_button, review_button


class EndpointPreview:
    """Read-only source thumbnails. Worker returns pixels; only UI thread touches Tk."""
    def __init__(self,parent):
        self.frame=ttk.LabelFrame(parent,text='선택 묶음 실제 원본 · 클릭하여 크게 보기 · 연결 학생은 추정',padding=4)
        self.frame.pack(fill='both',expand=True,pady=4)
        self.frame.columnconfigure(0,weight=1);self.frame.columnconfigure(1,weight=1);self.frame.rowconfigure(1,weight=1)
        self.labels=[];self.canvases=[];self.photos=[None,None];self.images=[None,None];self.keys=[None,None]
        self.cache=OrderedDict();self.errors={};self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix="source-preview");self.pending=set();self.results=queue.Queue();self.closed=False
        for i,name in enumerate(('첫 원본','마지막 원본')):
            text=tk.StringVar(value=name+' · 묶음을 선택하십시오.');self.labels.append(text)
            label=ttk.Label(self.frame,textvariable=text,wraplength=450);label.grid(row=0,column=i,sticky='ew',padx=4)
            canvas=tk.Canvas(self.frame,height=160,width=1,bg='#E9EDF2',highlightthickness=0);canvas.grid(row=1,column=i,sticky='nsew',padx=4)
            canvas.bind('<Configure>',lambda e,j=i:self.draw(j));canvas.bind('<Button-1>',lambda e,j=i:self.enlarge(j));canvas.configure(cursor='hand2');self.canvases.append(canvas)
            label.bind('<Configure>',lambda e,w=label:w.configure(wraplength=max(100,e.width-8)))
        self.poll_job=self.frame.after(60,self.poll)
        self.frame.bind('<Destroy>',lambda e:self.stop() if e.widget==self.frame else None)

    def stop(self):
        self.closed=True;self.executor.shutdown(wait=False,cancel_futures=True)
        try:self.frame.after_cancel(self.poll_job)
        except tk.TclError:pass

    def set_pages(self,source,start,end,expected):
        for i,page in enumerate((start-1,end-1)):
            self.labels[i].set(('첫 원본' if i==0 else '마지막 원본')+f': {Path(source).name} · 실제 {page+1}쪽\n예상 연결(추정): '+expected[i])
            key=(str(source),page);self.keys[i]=key;self.images[i]=None
            if key in self.cache:self.images[i]=self.cache[key];self.cache.move_to_end(key)
            elif key not in self.pending:
                self.pending.add(key)
                def work(k=key):
                    try:
                        with fitz.open(k[0]) as doc:
                            page=doc[k[1]];scale=min(1.0,600/max(page.rect.width,page.rect.height));pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),annots=False)
                            im=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
                        self.results.put((k,im,None))
                    except Exception as exc:self.results.put((k,None,str(exc)))
                self.executor.submit(work)
            self.draw(i)

    def enlarge(self,index):
        image=self.images[index]
        if image is None:return
        image=image.copy();window=tk.Toplevel(self.frame);window.title('실제 원본 확대 · 자동 연결 추정과 구분');window.transient(self.frame.winfo_toplevel());window.geometry('900x720');window.minsize(480,400)
        ttk.Label(window,text=self.labels[index].get(),wraplength=850,padding=8).pack(fill='x')
        canvas=tk.Canvas(window,bg='#E9EDF2',highlightthickness=0);canvas.pack(fill='both',expand=True)
        def draw(event=None):
            im=image.copy();im.thumbnail((max(30,canvas.winfo_width()-12),max(30,canvas.winfo_height()-12)))
            canvas.photo=ImageTk.PhotoImage(im);canvas.delete('all');canvas.create_image(canvas.winfo_width()/2,canvas.winfo_height()/2,image=canvas.photo)
        canvas.bind('<Configure>',draw)

    def clear(self):
        self.keys=[None,None];self.images=[None,None]
        for i in range(2):self.labels[i].set('묶음을 선택하면 실제 첫·끝 원본을 표시합니다.');self.draw(i)

    def poll(self):
        if self.closed:return
        while True:
            try:key,im,error=self.results.get_nowait()
            except queue.Empty:break
            self.pending.discard(key)
            if error:self.errors[key]=error
            if im is not None:
                self.cache[key]=im
                while len(self.cache)>12:self.cache.popitem(last=False)
            for i in range(2):
                if self.keys[i]==key:
                    self.images[i]=im;self.draw(i,error)
        self.poll_job=self.frame.after(60,self.poll)

    def draw(self,i,error=None):
        c=self.canvases[i];c.delete('all');im=self.images[i];error=error or self.errors.get(self.keys[i])
        if im is None:
            c.create_text(8,12,anchor='nw',width=max(80,c.winfo_width()-16),text='원본 미리보기 오류 · 상세 연결 작업은 계속할 수 있습니다: '+error if error else '원본 확인 중…' if self.keys[i] else '선택된 묶음 없음');return
        im=im.copy();im.thumbnail((max(30,c.winfo_width()-12),max(30,c.winfo_height()-12)))
        self.photos[i]=ImageTk.PhotoImage(im);c.create_image(c.winfo_width()/2,c.winfo_height()/2,image=self.photos[i])


class Review:
    def __init__(self,parent,app,raster=True):
        self.app=app;self.rows=[];self.raster=raster;self.students=[];self.rules={};self.hashes={};self.busy=False
        self.history=[];self.filter='전체';self.return_selection=();self.revision=0;self.saved_revision=None;self.output_folder='미선택'
        if not hasattr(app,'page_reviews'):app.page_reviews=[]
        app.page_reviews.append(self)
        self.frame=ttk.Frame(parent);self.frame.pack(fill='both',expand=True)
        self.summary=tk.StringVar(value='입력 후 페이지 대응을 확인하십시오.')
        ttk.Label(self.frame,textvariable=self.summary,style='Status.TLabel',wraplength=980).pack(fill='x',padx=8,pady=5)
        statusbar=ttk.Frame(self.frame);statusbar.pack(fill='x',padx=8)
        self.status_buttons={}
        for name in ('전체','미정','중복','제외'):
            button=ttk.Button(statusbar,text=name,command=lambda n=name:self.filter_rows(n));button.pack(side='left',padx=2);self.status_buttons[name]=button
        ttk.Button(statusbar,text='이전 페이지로 복귀',command=self.return_page).pack(side='left',padx=6)
        ttk.Button(statusbar,text='연결 변경 되돌리기',command=self.undo).pack(side='left')
        paned=self.paned=ttk.Panedwindow(self.frame,orient='horizontal',cursor='sb_h_double_arrow');paned.pack(fill='both',expand=True)
        self.pane_fraction=.53;self.pane_width=0;self.sash_initialized=False
        paned.bind('<Map>',lambda e:paned.after_idle(self.clamp_sash))
        paned.bind('<Configure>',lambda e:paned.after_idle(self.clamp_sash))
        paned.bind('<ButtonRelease-1>',lambda e:paned.after_idle(self.clamp_sash))
        paned.bind('<B1-Motion>',lambda e:paned.after_idle(self.clamp_sash))
        left=ttk.Frame(paned,padding=(0,0,10,0));right=ttk.Frame(paned,padding=(10,0,0,0));paned.add(left,weight=1);paned.add(right,weight=1)
        self.sash_handle=ttk.Label(paned,text='⋮',anchor='center',cursor='sb_h_double_arrow',relief='raised')
        self.sash_handle.bind('<ButtonPress-1>',self.start_sash_drag);self.sash_handle.bind('<B1-Motion>',self.drag_sash);self.sash_handle.bind('<ButtonRelease-1>',lambda e:self.clamp_sash())
        columns=('source','seat','name','code','order','state')
        self.tree=ttk.Treeview(left,columns=columns,show='headings',selectmode='extended',height=8)
        for key,title,width,minimum in zip(columns,('원본 페이지','학급·번호','이름','익명코드','답안 순번','상태'),(140,90,100,145,75,125),(110,70,75,115,70,90)):
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=minimum,stretch=True)
        sy=ttk.Scrollbar(left,command=self.tree.yview);sx=ttk.Scrollbar(left,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
        left.columnconfigure(0,weight=1);left.rowconfigure(0,weight=1)
        self.tree.tag_configure('pending',background='#FFF0CE');self.tree.tag_configure('excluded',foreground='#777777')
        self.tree.bind('<<TreeviewSelect>>',self.selection_changed)
        self.view=tk.StringVar(value='출력 미리보기' if raster else '페이지 보기')
        nav=ttk.Frame(right);nav.pack(fill='x')
        if raster:
            for name in ('원본','출력 미리보기'):ttk.Radiobutton(nav,text='원본 보기' if name=='원본' else '비식별 출력 보기',value=name,variable=self.view,command=self.show).pack(side='left')
        self.fit=tk.StringVar(value='페이지 맞춤');self.zoom=1.0;self.preview_cache=OrderedDict();self.last_preview_key=None
        zoomrow=ttk.Frame(right);zoomrow.pack(fill='x')
        fitbox=ttk.Combobox(zoomrow,textvariable=self.fit,values=['페이지 맞춤','너비 맞춤'],width=12,state='readonly');fitbox.pack(side='left');fitbox.bind('<<ComboboxSelected>>',lambda e:self.reset_zoom())
        ttk.Button(zoomrow,text='−',width=3,command=lambda:self.change_zoom(.8)).pack(side='left',padx=2)
        ttk.Button(zoomrow,text='+',width=3,command=lambda:self.change_zoom(1.25)).pack(side='left')
        self.zoom_text=tk.StringVar(value='100%');ttk.Label(zoomrow,textvariable=self.zoom_text).pack(side='left',padx=4)
        self.student_info=tk.StringVar(value='학생정보는 이 컴퓨터 화면에만 표시합니다.')
        ttk.Label(right,textvariable=self.student_info,wraplength=410).pack(fill='x')
        if not raster:ttk.Label(nav,text='기존 비식별 PDF 보기 · 추가 개인정보 제거 없음').pack(side='left')
        self.student_label=next(w for w in right.winfo_children() if isinstance(w,ttk.Label))
        imagepanel=ttk.Frame(right);imagepanel.pack(fill='both',expand=True,padx=4)
        self.canvas=tk.Canvas(imagepanel,bg='#E9EDF2',highlightthickness=0,width=1,height=140)
        sy=ttk.Scrollbar(imagepanel,command=self.canvas.yview);sx=ttk.Scrollbar(imagepanel,orient='horizontal',command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
        imagepanel.rowconfigure(0,weight=1);imagepanel.columnconfigure(0,weight=1)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        right.bind('<Configure>',lambda e:self.student_label.configure(wraplength=max(160,e.width-12)))
        self.canvas.bind('<Configure>',lambda e:self.show())
        self.edit_bar=ttk.Frame(self.frame);self.edit_bar.pack(fill='x',padx=8,pady=2)
        self.edit_toggle=ttk.Button(self.edit_bar,text='선택 페이지 수정 ▸',command=self.toggle_edit);self.edit_toggle.pack(side='left')
        ttk.Label(self.edit_bar,text='미정·중복은 수정하거나 나중에 지정하고 계속할 수 있습니다.').pack(side='left',padx=8)
        self.edit_open=False
        edit=self.edit_panel=ttk.LabelFrame(self.frame,text='대응 조정 · 선택한 페이지를 수정하고 계속 진행',padding=6);edit.pack(fill='x',padx=8,pady=5)
        self.code=tk.StringVar();self.klass=tk.StringVar();self.order=tk.StringVar(value='1')
        ttk.Label(edit,text='학생 찾기').grid(row=0,column=0)
        self.codebox=ttk.Combobox(edit,textvariable=self.code,width=23);self.codebox.grid(row=0,column=1,columnspan=5,padx=4,sticky='ew')
        self.codebox.bind('<<ComboboxSelected>>',self.code_changed)
        self.codebox.bind('<KeyRelease>',self.search_students)
        ttk.Label(edit,text='학급').grid(row=1,column=0);self.classbox=ttk.Combobox(edit,textvariable=self.klass,width=12);self.classbox.grid(row=1,column=1,padx=4,sticky='w')
        ttk.Label(edit,text='페이지순번').grid(row=1,column=2);ttk.Spinbox(edit,from_=1,to=9999,textvariable=self.order,width=6).grid(row=1,column=3,padx=4)
        ttk.Button(edit,text='선택에 적용',command=self.apply).grid(row=0,column=6,padx=4)
        row=ttk.Frame(edit);row.grid(row=2,column=0,columnspan=7,sticky='w',pady=(5,0))
        for name,command in [('나중에 지정',self.pending),('이번 출력에서 제외',lambda:self.exclude(True)),('다시 포함',lambda:self.exclude(False))]:ttk.Button(row,text=name,command=command).pack(side='left',padx=(0,5))
        edit.columnconfigure(1,weight=1)
        edit.pack_forget()
        self.output_group=ttk.LabelFrame(self.frame,text='출력·저장',padding=(8,5));self.output_group.pack(fill='x',padx=8,pady=(2,4))
        self.output_summary=tk.StringVar(value='출력 방식과 예상 파일 수를 확인하십시오.')
        output_head=ttk.Frame(self.output_group);output_head.pack(fill='x')
        self.output_toggle=ttk.Button(output_head,text='저장 준비 ▸',command=self.toggle_output);self.output_toggle.pack(side='right')
        self.output_summary_label=ttk.Label(output_head,textvariable=self.output_summary,wraplength=850);self.output_summary_label.pack(side='left',fill='x',expand=True)
        self.output_open=False;self.output_details=ttk.Frame(self.output_group)
        footer=ttk.Frame(self.output_details);footer.pack(fill='x')
        self.output_mode=tk.StringVar(value='학급별 묶음')
        ttk.Label(footer,text='만들 파일').pack(side='left')
        self.modebox=ttk.Combobox(footer,textvariable=self.output_mode,values=['학급별 묶음','학생별 묶음','페이지순번별 묶음','페이지별 개별'],width=20,state='readonly');self.modebox.pack(side='left',padx=5)
        self.guide_folder=ttk.Button(footer,text='출력 폴더 선택',command=self.choose_output_folder);self.guide_folder.pack(side='right')
        self.estimate=tk.StringVar()
        if raster:
            ttk.Button(footer,text='메인 규칙 갱신',command=self.refresh_rules_dialog).pack(side='left',padx=4)
            ttk.Button(footer,text='규칙 되돌림',command=self.undo_rules).pack(side='left')
        scopebar=ttk.Frame(self.output_details);scopebar.pack(fill='x',pady=(2,0))
        self.scope=tk.StringVar(value='전체')
        for name in ('전체','선택 페이지만'):
            ttk.Radiobutton(scopebar,text='출력 가능한 전체' if name=='전체' else name,value=name,variable=self.scope,command=self.mode_changed).pack(side='left',padx=4)
        self.scope_info=tk.StringVar();ttk.Label(self.output_details,textvariable=self.scope_info,wraplength=1000).pack(fill='x')
        self.preflight=tk.StringVar();ttk.Label(self.output_details,textvariable=self.preflight,wraplength=1040).pack(fill='x',pady=(2,4))
        actions=ttk.Frame(self.output_details);actions.pack(fill='x')
        self.agreed=tk.BooleanVar(value=False)
        ttk.Checkbutton(actions,text='연결 순서·개인정보 제거 미리보기 확인',variable=self.agreed).pack(side='left')
        self.export_button=ttk.Button(actions,text='출력 가능한 페이지만 저장',command=self.export,style='Accent.TButton');self.export_button.pack(side='right')
        self.result_group=ttk.Frame(self.frame,padding=(8,0));self.result_group.pack(fill='x')
        self.dirty_text=tk.StringVar(value='현재 설정: 미저장')
        ttk.Label(self.result_group,textvariable=self.dirty_text,foreground='#995500',wraplength=1040).pack(fill='x')
        self.last_saved=tk.StringVar(value='직전 저장 결과: 없음');ttk.Label(self.result_group,textvariable=self.last_saved,wraplength=1040,foreground='#526078').pack(fill='x',pady=(2,0))
        self.output_mode.trace_add('write',lambda *_:self.mode_changed())
        self.message=tk.StringVar(value='행을 골라 원본과 출력을 비교하십시오. 저장하려면 저장 준비를 여십시오.')
        self.message_label=ttk.Label(self.frame,textvariable=self.message,wraplength=1040);self.message_label.pack(fill='x',padx=8,pady=(0,3))
        self.frame.bind('<Configure>',self.resize_labels)
        self.main_seen=self.main_token();self.rule_history=None;self.rule_stale=False
        self.rule_warning=tk.StringVar(value='메인 규칙 확인됨')
        ttk.Label(self.result_group,textvariable=self.rule_warning,foreground='#9C3C14',wraplength=1000).pack(fill='x')
        self.frame.winfo_toplevel().bind('<FocusIn>',lambda e:self.update_rule_status(),add='+')

    def resize_labels(self,event):
        width=max(160,event.width-36)
        self.message_label.configure(wraplength=width)
        self.output_summary_label.configure(wraplength=max(160,width-self.output_toggle.winfo_reqwidth()-30))
        for parent in (self.output_details,self.result_group):
            for child in parent.winfo_children():
                if isinstance(child,ttk.Label):child.configure(wraplength=width)

    def toggle_edit(self,opened=None):
        if self.busy:return
        self.edit_open=not self.edit_open if opened is None else opened
        if self.edit_open:
            if getattr(self,'output_open',False):self.output_details.pack_forget();self.output_open=False;self.output_toggle.configure(text='저장 준비 ▸')
            self.edit_panel.pack(fill='x',padx=8,pady=3,before=self.output_group)
        else:self.edit_panel.pack_forget()
        self.edit_toggle.configure(text='선택 페이지 수정 ▾' if self.edit_open else '선택 페이지 수정 ▸')

    def toggle_output(self,opened=None):
        if self.busy:return
        self.output_open=not self.output_open if opened is None else opened
        if self.output_open:
            if self.edit_open:self.edit_panel.pack_forget();self.edit_open=False;self.edit_toggle.configure(text='선택 페이지 수정 ▸')
            self.output_details.pack(fill='x',pady=(4,0))
        else:self.output_details.pack_forget()
        self.output_toggle.configure(text='저장 설정 접기 ▾' if self.output_open else '저장 준비 ▸')

    def start_sash_drag(self,event):
        self.drag_start=(event.x_root,self.paned.sashpos(0))

    def drag_sash(self,event):
        if self.busy:return
        self.paned.sashpos(0,self.drag_start[1]+event.x_root-self.drag_start[0]);self.clamp_sash()

    def set_rows(self,rows,students=None,rules=None):
        self.rows=rows;self.students=students or [];self.rules=rules or {};self.agreed.set(False)
        self.history=[];self.filter='전체';self.revision+=1;self.toggle_edit(False)
        self.main_seen=self.main_token();self.rule_history=None;self.update_rule_status()
        self.codebox.configure(values=[student_label(s) for s in self.students]);self.classbox.configure(values=list(dict.fromkeys(s['class'] for s in self.students)))
        paths={str(Path(r['source']).resolve()) for r in rows if Path(r['source']).is_file()}
        self.hashes={p:digest(p) for p in paths}
        self.refresh()

    def minimum_table(self):return min(600,max(360,int(self.paned.winfo_width()*.42)))

    def clamp_sash(self):
        if not self.paned.winfo_exists() or not self.paned.winfo_ismapped():return
        width=self.paned.winfo_width()
        if width<4:return
        low=self.minimum_table();high=max(low,width-240)
        current=self.paned.sashpos(0)
        if not self.sash_initialized or self.pane_width!=width:current=int(width*self.pane_fraction)
        value=max(low,min(high,current));self.paned.sashpos(0,value)
        self.pane_fraction=value/width;self.pane_width=width;self.sash_initialized=True
        if hasattr(self,'sash_handle'):self.sash_handle.place(x=value-9,rely=.5,anchor='w',width=18,height=58);self.sash_handle.lift()

    def mode_changed(self):
        if self.busy:return
        self.revision+=1;self.agreed.set(False);self.update_estimate()

    def choose_output_folder(self):
        if self.busy:return
        folder=filedialog.askdirectory(parent=self.frame.winfo_toplevel(),title='입력과 분리된 로컬 출력 폴더')
        if folder:self.output_folder=folder;self.mode_changed()

    def update_estimate(self):
        states=inspect_mapping(self.rows)
        selected=self.selected();chosen=[i for i in selected if states.get(i)=='출력 가능']
        if self.scope.get()=='선택 페이지만':
            rows=[self.rows[i] for i in chosen];self.estimate.set(output_estimate(rows,inspect_mapping(rows),MODES[self.output_mode.get()]))
            self.scope_info.set(f'선택 {len(selected)}쪽 · 출력 가능 {len(chosen)}쪽 · 미정·중복·제외 {len(selected)-len(chosen)}쪽은 출력하지 않습니다.')
            self.export_button.configure(text=f'선택 {len(chosen)}쪽 출력')
        else:
            self.estimate.set(output_estimate(self.rows,states,MODES[self.output_mode.get()]));self.scope_info.set('전체 작업 중 출력 가능한 페이지만 저장합니다.');self.export_button.configure(text='출력 가능한 페이지만 저장')
        rules=[]
        for r in self.rules.values():
            rules.append(f'상단 {r.cut:.1%} 절단·마스크 {len(r.masks)}개·예외 {len(r.exceptions())}쪽')
        rule_text=' / '.join(dict.fromkeys(rules)) if self.raster else '재구성 전용: 추가 개인정보 제거 없음'
        self.preflight.set(f'규칙: {rule_text or "메인 공통 규칙"} · 출력 폴더: {self.output_folder}')
        self.output_summary.set(f'{self.output_mode.get()} · {self.estimate.get()}')
        self.dirty_text.set('현재 설정: 저장 완료' if self.saved_revision==self.revision else '현재 설정: 미저장 변경사항 있음 · 저장 전 미리보기를 확인하십시오.')

    def filter_rows(self,name):
        if self.filter=='전체':self.return_selection=self.tree.selection()
        self.filter=name;self.refresh()

    def return_page(self):
        self.filter='전체';self.refresh()
        keep=[i for i in self.return_selection if self.tree.exists(i)]
        if keep:self.tree.selection_set(keep);self.tree.see(keep[0]);self.show(sync=True)

    def remember(self):
        self.history.append(copy.deepcopy(self.rows));self.history=self.history[-20:]

    def changed(self):
        self.revision+=1;self.agreed.set(False)
        self.message.set('현재 연결을 변경했습니다. 정상 페이지는 계속 처리할 수 있습니다.');self.refresh()

    def undo(self):
        if self.busy or not self.history:return
        self.rows=self.history.pop();self.changed()

    def refresh(self):
        selection=self.tree.selection();scroll=(self.tree.xview(),self.tree.yview());old_count=len(self.tree.get_children());old_filter=getattr(self,'shown_filter',None)
        self.states=inspect_mapping(self.rows);self.tree.delete(*self.tree.get_children())
        buckets=categories(self.states)
        for key,button in self.status_buttons.items():button.configure(text=f'{key} {len(buckets[key])}')
        for i,row in enumerate(self.rows):
            if i not in buckets[self.filter]:continue
            info=row['identity'] or {};state=self.states[i]
            student=next((s for s in self.students if s['code']==info.get('student_code')),None)
            seat=f"{student.get('class') or '학급 미확인'} · {student.get('number')+'번' if student.get('number') else '번호 미확인'}" if student else info.get('class','미확인')
            name=student.get('name') or '미확인' if student else '미확인'
            self.tree.insert('','end',iid=str(i),values=(f'입력{list(dict.fromkeys(r["source"] for r in self.rows)).index(row["source"])+1:02d} · {row["page"]+1}쪽',seat,name,info.get('student_code','미정'),info.get('page_order',''),state),tags=('excluded' if state=='제외' else 'ready' if state=='출력 가능' else 'pending',))
        ready=sum(s=='출력 가능' for s in self.states.values());excluded=sum(s=='제외' for s in self.states.values())
        self.summary.set(f"원본 {len(self.rows)}쪽 · 출력 가능 {ready} · 미정 {len(buckets['미정'])} · 중복 {len(buckets['중복'])} · 제외 {excluded}")
        if selection:
            keep=[i for i in selection if self.tree.exists(i)]
            if keep:self.tree.selection_set(keep)
        if not self.tree.selection() and self.tree.get_children():self.tree.selection_set(self.tree.get_children()[0])
        if old_filter==self.filter and old_count==len(self.tree.get_children()):
            if scroll[0]:self.tree.xview_moveto(scroll[0][0])
            if scroll[1]:self.tree.yview_moveto(scroll[1][0])
        self.shown_filter=self.filter
        self.show(sync=True);self.update_estimate()

    def code_changed(self,event=None):
        match=next((s for s in self.students if student_label(s)==self.code.get() or s['code']==self.code.get()),None)
        if match:self.klass.set(match['class'])

    def search_students(self,event=None):
        query=self.code.get().casefold()
        self.codebox.configure(values=[student_label(s) for s in self.students if query in student_label(s).casefold()])

    def selection_changed(self,event=None):
        self.show(sync=True)
        if hasattr(self,'scope'):
            if self.scope.get()=='선택 페이지만':self.agreed.set(False)
            self.update_estimate()

    def selected(self):return [int(i) for i in self.tree.selection()]

    def show(self,sync=False):
        selected=self.selected()
        if not selected:self.canvas.delete('all');self.student_info.set('표시할 페이지가 없습니다. 전체 목록으로 돌아갈 수 있습니다.');return
        row=self.rows[selected[0]];info=row['identity']
        student=next((s for s in self.students if info and s['code']==info['student_code']),None)
        self.student_info.set(f'원본: {row["source"]} · {row["page"]+1}쪽\n'+(student_label(student) if student else '학생정보 미확인 · 로컬 코드표로 연결할 수 있습니다.')+f'\n상태: {getattr(self,"states",{}).get(selected[0],"")}' +(' · 미정 페이지: 원본 표시' if not info else ''))
        if sync:
            if self.filter in ('미정','중복') or (getattr(self,'states',{}).get(selected[0]) not in ('출력 가능','제외')):self.toggle_edit(True)
            if info:self.code.set(student_label(student) if student else info['student_code']);self.klass.set(info['class']);self.order.set(str(info['page_order']))
            else:self.code.set('');self.klass.set('');self.order.set('1')
            self.codebox.configure(values=[student_label(s) for s in self.students])
        self.canvas.delete('all')
        rules=self.rules.get(str(Path(row['source']).resolve()))
        cut,masks=rules.for_page(row['page']) if rules else (.18,())
        key=(row['source'],row['page'],str(info),self.view.get(),int(self.app.dpi.get()),cut,repr(masks),self.raster)
        try:
            if key in self.preview_cache:im=self.preview_cache[key];self.preview_cache.move_to_end(key)
            else:
                with fitz.open(row['source']) as src:
                    page=src[row['page']]
                    if self.raster and info and self.view.get()=='출력 미리보기':
                        data=make_pdf(page,info['student_code'],cut,int(self.app.dpi.get()),masks,info)
                        with fitz.open(stream=data,filetype='pdf') as out:pix=out[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5))
                    elif not self.raster and info:
                        from page_identity import read
                        try:same=read(page)==info
                        except (ValueError,TypeError,KeyError):same=False
                        if same:pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),annots=False)
                        else:
                            with fitz.open(stream=relabel_pdf(page,info,int(self.app.dpi.get())),filetype='pdf') as changed:pix=changed[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5))
                    else:pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),annots=False)
                    im=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
                self.preview_cache[key]=im
                while len(self.preview_cache)>6:self.preview_cache.popitem(last=False)
            if self.last_preview_key!=key:self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
            self.last_preview_key=key;self.preview_source=(row['source'],row['page']);self.draw_image(im)
        except Exception as exc:self.canvas.create_text(12,20,text='이 페이지의 미리보기를 표시하지 못했습니다. 원본을 확인하거나 나중에 지정하십시오: '+str(exc),anchor='nw',width=max(100,self.canvas.winfo_width()-24))

    def reset_zoom(self):
        self.zoom=1.0;self.zoom_text.set('100%');self.canvas.xview_moveto(0);self.canvas.yview_moveto(0);self.show()

    def change_zoom(self,factor):
        self.zoom=max(.25,min(4,self.zoom*factor));self.zoom_text.set(f'{self.zoom:.0%}');self.show()

    def draw_image(self,image):
        width=max(40,self.canvas.winfo_width()-12);height=max(40,self.canvas.winfo_height()-12)
        scale=width/image.width
        if self.fit.get()=='페이지 맞춤':scale=min(scale,height/image.height)
        size=(max(1,round(image.width*scale*self.zoom)),max(1,round(image.height*scale*self.zoom)))
        im=image.resize(size,Image.Resampling.LANCZOS)
        self.photo=ImageTk.PhotoImage(im)
        area=(max(size[0]+12,width+12),max(size[1]+12,height+12))
        self.canvas.create_image(area[0]/2,max(6,(height-size[1])/2),anchor='n',image=self.photo)
        self.canvas.configure(scrollregion=(0,0,*area))

    def apply(self):
        if self.busy:return
        try:
            match=next((s for s in self.students if student_label(s)==self.code.get()),None)
            if self.students and not match and not any(s['code']==self.code.get() and s['code'] for s in self.students):raise ValueError('이름·번호로 검색한 뒤 코드표의 학생을 선택하십시오. 검색어를 코드로 저장하지 않습니다.')
            code=match['code'] if match else self.code.get()
            value=identity(code,self.klass.get(),int(self.order.get()))
            chosen=self.selected()
            if not chosen:return
            self.remember()
            for i in chosen:
                if len(chosen)>1:
                    previous=self.rows[i]['identity']
                    if previous:self.rows[i]['identity']=identity(previous['student_code'],previous['class'],value['page_order'])
                else:self.rows[i]['identity']=dict(value)
                self.rows[i]['note']='';self.rows[i]['excluded']=False
            self.changed()
            if len(chosen)==1 and not self.tree.exists(str(chosen[0])) and self.states.get(chosen[0])=='출력 가능':
                self.filter='전체';self.refresh();self.tree.selection_set(str(chosen[0]));self.tree.see(str(chosen[0]));self.show(sync=True);self.update_estimate()
            if len(chosen)>1:self.message.set('여러 페이지는 학생코드·학급을 유지하고 페이지순번만 바꿨습니다. 미정 학생코드는 한 페이지씩 지정하십시오.')
            if not self.raster:self.message.set('정보를 고친 페이지는 두 박스도 갱신합니다. 정보가 없던 외부 형식은 기존 전체 페이지를 보존하므로 기존 표시가 남는지 미리보기를 확인하십시오.')
        except Exception as exc:self.message.set(str(exc))

    def pending(self):
        if self.busy:return
        self.remember()
        for i in self.selected():self.rows[i]['identity']=None;self.rows[i]['note']='나중에 지정';self.rows[i]['excluded']=False
        self.changed()

    def exclude(self,value):
        if self.busy:return
        self.remember()
        for i in self.selected():self.rows[i]['excluded']=value
        self.changed()

    def main_token(self):
        return work_session.fingerprint({'source':str(Path(self.app.pdf).resolve()) if self.app.pdf else None,'rules':work_session.pack_rules(self.app.rules)})

    def update_rule_status(self):
        if not hasattr(self,'rule_warning') or not self.frame.winfo_exists():return
        self.rule_stale=self.raster and self.main_seen!=self.main_token()
        self.rule_warning.set('메인 규칙 변경됨 · 메인 규칙 갱신에서 비교·선택 후 출력하십시오.' if self.rule_stale else '현재 대량 규칙 사용 · 메인 변경 확인됨' if self.raster else '재구성 전용 · 추가 절단·마스킹 없음')
        if self.rule_stale:self.agreed.set(False)

    def rule_proposal(self,policy):
        if policy not in ('preserve','replace','keep'):raise ValueError('규칙 적용 방식을 선택하십시오.')
        if policy=='keep':return {p:rule.snapshot() for p,rule in self.rules.items()}
        proposed={}
        for source,current in self.rules.items():
            if self.app.pdf and Path(source).resolve()==Path(self.app.pdf).resolve():rule=self.app.rules.snapshot()
            else:rule=PageRules(self.app.rules.cut,copy.deepcopy(self.app.rules.masks))
            if policy=='preserve':rule.cuts.update(copy.deepcopy(current.cuts));rule.page_masks.update(copy.deepcopy(current.page_masks))
            proposed[source]=rule
        return proposed

    def rule_difference(self,proposed):
        return sum(self.rules[row['source']].for_page(row['page'])!=proposed[row['source']].for_page(row['page']) for row in {(r['source'],r['page']):r for r in self.rows}.values())

    def apply_main_rules(self,policy):
        if self.busy:return
        proposed=self.rule_proposal(policy);self.rule_history=({p:r.snapshot() for p,r in self.rules.items()},self.main_seen)
        changed=self.rule_difference(proposed);self.rules=proposed;self.main_seen=self.main_token();self.preview_cache.clear();self.revision+=1;self.agreed.set(False);self.update_estimate();self.update_rule_status();self.show()
        self.message.set(f'규칙 확인 완료 · 변경 {changed}쪽 · 학생 연결은 유지했습니다. 출력 미리보기를 다시 확인하십시오.')

    def undo_rules(self):
        if self.busy or not self.rule_history:return
        self.rules,self.main_seen=self.rule_history;self.rule_history=None;self.preview_cache.clear();self.revision+=1;self.agreed.set(False);self.update_estimate();self.update_rule_status();self.show()
        self.message.set('직전 규칙으로 되돌렸습니다. 메인과 다르면 갱신에서 다시 선택하십시오.')

    def refresh_rules_dialog(self):
        if self.busy or not self.raster:return
        preserve=self.rule_proposal('preserve');replace=self.rule_proposal('replace');first=self.rule_difference(preserve);second=self.rule_difference(replace)
        exceptions=sum(len(rule.exceptions()) for rule in self.rules.values())
        dialog=tk.Toplevel(self.frame);dialog.title('메인 규칙 비교·적용');dialog.transient(self.frame.winfo_toplevel());dialog.grab_set()
        ttk.Label(dialog,text=f'학생 연결·원본은 유지합니다. 현재 개별 예외 {exceptions}쪽.\n메인: 상단 {self.app.rules.cut:.1%}, 공통 마스크 {len(self.app.rules.masks)}개.\n현재 예외 보존 시 변경 {first}쪽 / 메인 규칙·예외로 교체 시 변경 {second}쪽.\n메인 개별 예외는 같은 원본 PDF에만 적용합니다.',wraplength=540,padding=12).pack(fill='x')
        choice=tk.StringVar(value='preserve')
        for value,label in [('preserve','현재 대량 개별 예외 보존 · 공통 규칙 갱신'),('replace','메인 규칙·예외로 교체 · 현재 예외가 교체될 수 있음'),('keep','현재 대량 규칙 유지 · 메인 변경을 확인했음')]:
            ttk.Radiobutton(dialog,text=label,value=value,variable=choice).pack(anchor='w',padx=12,pady=3)
        bar=ttk.Frame(dialog,padding=10);bar.pack(fill='x')
        ttk.Button(bar,text='취소',command=dialog.destroy).pack(side='right')
        ttk.Button(bar,text='선택 적용',command=lambda:(self.apply_main_rules(choice.get()),dialog.destroy())).pack(side='right',padx=8)
        self.app.place_dialog(dialog)

    def export_rows(self):
        if self.scope.get()!='선택 페이지만':return copy.deepcopy(self.rows)
        # Inspect the WHOLE mapping first: a selected duplicate must not become valid by slicing.
        states=inspect_mapping(self.rows);selected=self.selected()
        return [copy.deepcopy(self.rows[i]) for i in selected if states.get(i)=='출력 가능']

    def export(self):
        if self.busy:return
        self.update_rule_status()
        if self.rule_stale:self.toggle_output(True);self.message.set('메인 규칙이 바뀌었습니다. 메인 규칙 갱신에서 적용 또는 현재 규칙 유지를 선택한 뒤 미리보기를 확인하십시오.');return
        rows=self.export_rows()
        if not any(s=='출력 가능' for s in inspect_mapping(rows).values()):self.message.set('출력할 페이지가 없습니다. 출력 가능한 행을 선택하거나 미정·중복 연결을 먼저 확인하십시오.');return
        if not self.agreed.get():self.toggle_output(True);self.message.set('출력 미리보기를 확인한 뒤 저장 설정의 확인 체크를 눌러야 저장할 수 있습니다.');return
        if any(not Path(p).is_file() or digest(p)!=h for p,h in self.hashes.items()):self.message.set('입력 파일이 바뀌었습니다. 다시 불러와 확인하십시오.');return
        if self.output_folder=='미선택':
            folder=filedialog.askdirectory(parent=self.frame.winfo_toplevel(),title='입력과 분리된 로컬 출력 폴더')
            if not folder:return
            self.output_folder=folder;self.agreed.set(False);self.update_estimate();self.message.set('출력 폴더와 요약을 확인한 뒤 확인 체크 → 저장을 누르십시오.');return
        folder=self.output_folder
        if not folder:return
        mode={'학급별 묶음':'class','학생별 묶음':'student','페이지순번별 묶음':'order','페이지별 개별':'page'}[self.output_mode.get()]
        saved_revision=self.revision
        self.busy=True;self.export_button.configure(state='disabled');self.message.set('저장·페이지 정보·순서를 검증하고 있습니다…')
        selected_only=self.scope.get()=='선택 페이지만'
        rules={p:r.snapshot() for p,r in self.rules.items()};dpi=int(self.app.dpi.get())
        locked=[]
        def lock(w):
            if isinstance(w,(ttk.Button,ttk.Checkbutton,ttk.Radiobutton,ttk.Entry,ttk.Combobox,ttk.Spinbox)):
                locked.append((w,w.state()));w.state(['disabled'])
            for child in w.winfo_children():lock(child)
        lock(self.frame.winfo_toplevel())
        def work():
            succeeded=False
            try:
                report,log=export_pages(rows,folder,mode,dpi,rules,raster=self.raster)
                success=[r for r in report['results'] if r['status']=='성공']
                text=('선택 페이지 출력 · ' if selected_only else '전체 출력 · ')+f'저장 성공 {len(success)}파일 · {sum(r["pages"] for r in success)}페이지 / 실패 {len(report["results"])-len(success)}파일 / 나중에 지정 {report["pending_pages"]}페이지\n기록: {log}'
                succeeded=bool(success) and len(success)==len(report['results'])
            except Exception as exc:text='저장 결과: '+str(exc)
            def finish():
                self.busy=False
                if self.frame.winfo_exists():
                    for w,state in locked:
                        if w.winfo_exists():w.state(['!disabled']);w.state(state)
                    self.last_saved.set('직전 저장 결과: '+text);self.saved_revision=saved_revision if succeeded and not selected_only else self.saved_revision if selected_only else None;self.update_estimate();self.message.set('선택 출력은 전체 작업 저장 완료가 아닙니다. 보충 파일은 기존 부분본과 재구성하십시오. 부분본+완전본을 함께 넣으면 중복됩니다.' if selected_only else '이번 실행 결과를 확인하십시오. 미정·제외 페이지는 출력되지 않았습니다.');self.export_button.configure(state='normal')
            self.app.root.after(0,finish)
        threading.Thread(target=work,daemon=False).start()


class BatchDialog:
    def __init__(self,app):
        self.roster_path=getattr(app,'roster',None);self.roster_options=getattr(app,'roster_options',(None,None));self.roster_fingerprint=digest(self.roster_path) if self.roster_path and Path(self.roster_path).is_file() else None
        self.session_path=None;self.session_saved=None
        if not hasattr(app,'batch_dialogs'):app.batch_dialogs=[]
        app.batch_dialogs.append(self)
        self.app=app;self.files=[];self.students=list(app.students);self.blocks=[];self.setup_history=[];self.rebuilding=False;self.last_config=None;self.auto_job=None
        top=self.top=tk.Toplevel(app.root);top.title('대량 스캔 · 학생코드와 페이지순번 부여');top.geometry('1180x820');top.minsize(1000,700);top.transient(app.root)
        self.step_text=tk.StringVar(value='1 / 3 · 자료 불러오기')
        ttk.Label(top,textvariable=self.step_text,font=('맑은 고딕',11,'bold')).pack(fill='x',padx=14,pady=(8,2))
        sessionbar=ttk.Frame(top);sessionbar.pack(fill='x',padx=10,pady=2)
        ttk.Button(sessionbar,text='작업 저장',command=self.save_session).pack(side='left')
        ttk.Button(sessionbar,text='작업 이어하기',command=self.load_session).pack(side='left',padx=5)
        self.session_note=tk.StringVar(value='작업 파일은 이 PC 전용 폴더에만 저장합니다. 검색어·되돌리기 기록은 제외합니다.')
        ttk.Label(sessionbar,textvariable=self.session_note,wraplength=650).pack(side='left',padx=6)
        self.book=ttk.Notebook(top);self.book.pack(fill='both',expand=True,padx=8,pady=(2,8))
        self.book.bind('<<NotebookTabChanged>>',self.update_step)
        inputs=ttk.Frame(self.book,padding=12);pattern=ttk.Frame(self.book,padding=12);review_tab=ttk.Frame(self.book)
        self.book.add(inputs,text='① 자료 불러오기');self.book.add(pattern,text='② 스캔 순서·연결 확인');self.book.add(review_tab,text='③ 출력 확인·저장')
        pattern_tab=pattern
        pattern_nav=ttk.Frame(pattern_tab);pattern_nav.pack(side='bottom',fill='x',pady=(4,0))
        self.pattern_canvas=tk.Canvas(pattern_tab,highlightthickness=0,width=1)
        pattern_scroll=ttk.Scrollbar(pattern_tab,command=self.pattern_canvas.yview);pattern_scroll.pack(side='right',fill='y')
        self.pattern_canvas.pack(fill='both',expand=True);self.pattern_canvas.configure(yscrollcommand=pattern_scroll.set)
        pattern=self.pattern_content=ttk.Frame(self.pattern_canvas);self.pattern_layout=None
        self.pattern_window=self.pattern_canvas.create_window((0,0),window=pattern,anchor='nw')
        pattern.bind('<Configure>',self.layout_pattern)
        self.pattern_canvas.bind('<Configure>',self.layout_pattern)
        self.input_text=tk.StringVar(value='코드표와 스캔 PDF를 선택하십시오.')
        ttk.Label(inputs,text='코드표와 PDF는 원본 그대로 보존합니다.',font=('맑은 고딕',12,'bold')).pack(anchor='w')
        ttk.Label(inputs,textvariable=self.input_text,wraplength=1050).pack(fill='x',pady=8)
        bar=ttk.Frame(inputs);bar.pack(fill='x')
        self.guide_roster=ttk.Button(bar,text='코드표 XLSX 선택',command=self.choose_roster);self.guide_roster.pack(side='left')
        self.klass=tk.StringVar(value='전체 학급');self.classbox=ttk.Combobox(bar,textvariable=self.klass,values=['전체 학급'],width=18,state='readonly');self.classbox.pack(side='left',padx=8)
        self.classbox.bind('<<ComboboxSelected>>',lambda e:self.update_inputs())
        filesbar=ttk.Frame(inputs);filesbar.pack(fill='x',pady=8)
        self.guide_files=ttk.Button(filesbar,text='스캔 PDF 여러 개 추가',command=self.choose_files);self.guide_files.pack(side='left')
        ttk.Button(filesbar,text='선택 파일 빼기',command=self.remove_files).pack(side='left',padx=8)
        nav=ttk.Frame(inputs);nav.pack(side='bottom',fill='x',pady=(8,0))
        self.guide_next=ttk.Button(nav,text='다음: 스캔 순서 확인 →',command=lambda:self.next_pattern(),style='Accent.TButton');self.guide_next.pack(side='right')
        filepanel=ttk.LabelFrame(inputs,text='선택한 스캔 PDF · 입력 순서 유지',padding=5);filepanel.pack(fill='both',expand=True,pady=(0,8))
        self.filelist=tk.Listbox(filepanel,selectmode='extended',exportselection=False,height=6);self.filelist.pack(side='left',fill='both',expand=True)
        fs=ttk.Scrollbar(filepanel,command=self.filelist.yview);fs.pack(side='right',fill='y');self.filelist.configure(yscrollcommand=fs.set)
        guide=ttk.LabelFrame(inputs,text='다음 단계 전에 확인',padding=8);guide.pack(fill='x')
        ttk.Label(guide,text='명단의 학급·학생 순서와 스캔 파일의 실제 순서를 확인하십시오. 다음 단계에서 스캔 방식을 고릅니다.\n절단·마스크는 메인의 전체 공통 규칙, 같은 PDF의 개별 예외를 사용합니다. 다른 파일의 예외는 복사하지 않습니다.',wraplength=1040).pack(fill='x')
        self.mode=tk.StringVar(value='single');self.per=tk.StringVar(value='3')
        modes_row=ttk.Frame(pattern);modes_row.pack(fill='x')
        for text,value in [('학생당 1장','single'),('학생별 연속 (1·2·3장)','continuous'),('페이지순번별 묶음 (전원1장→전원2장)','faces')]:
            ttk.Radiobutton(modes_row,text=text,value=value,variable=self.mode).pack(side='left',padx=(0,10),pady=3)
        row=ttk.Frame(pattern);row.pack(fill='x',pady=8)
        ttk.Label(row,text='학생별 연속 스캔: 학생당 페이지 수').pack(side='left');ttk.Spinbox(row,textvariable=self.per,from_=1,to=9999,width=6).pack(side='left',padx=5)
        ttk.Button(row,text='기본 묶음으로 다시 제안',command=self.reset_proposal).pack(side='left',padx=8)
        ttk.Button(row,text='이전 설정 복원',command=self.undo_setup).pack(side='left')
        ttk.Label(pattern,text='페이지순번별 묶음은 학급 인원 수로 구간을 제안합니다. 빠진 페이지가 있으면 구간을 직접 고치십시오.\n구간별 정순·역순을 따로 지정할 수 있습니다. 자동 지정은 초안이며 출력 전 대응을 확인합니다.',wraplength=1040).pack(fill='x')
        self.cards=ttk.Frame(pattern);self.cards.pack(fill='x',pady=6);self.direction_controls={}
        self.blocktree=ttk.Treeview(pattern,columns=('file','start','end','order','direction','student'),show='headings',height=4,selectmode='browse',displaycolumns=('file','start','order','direction','student'))
        for key,text,width in zip(('file','start','end','order','direction','student'),('스캔 파일 / 묶음','원본 쪽','끝 페이지','답안 순번','학생 순서','첫 학생 → 마지막 학생'),(230,100,60,90,80,430)):
            self.blocktree.heading(key,text=text);self.blocktree.column(key,width=width,minwidth=65)
        self.blocktree.pack(fill='both',expand=True,pady=6);self.blocktree.bind('<<TreeviewSelect>>',self.select_block)
        basic=self.basic=ttk.Frame(pattern);basic.pack(fill='x')
        self.quick_label=ttk.Label(basic,text='선택한 묶음의 학생 순서');self.quick_label.pack(side='left')
        self.quick_direction=tk.StringVar(value='정순')
        q=self.quick_combo=ttk.Combobox(basic,textvariable=self.quick_direction,values=['정순','역순'],width=8,state='readonly');q.pack(side='left',padx=6);q.bind('<<ComboboxSelected>>',self.quick_reverse)
        self.detail_button=ttk.Button(basic,text='상세 조정 열기 / 닫기',command=self.toggle_details);self.detail_button.pack(side='left',padx=8)
        self.selected_endpoints=tk.StringVar(value='묶음을 선택하면 첫 학생과 마지막 학생을 표시합니다.')
        ttk.Label(pattern,textvariable=self.selected_endpoints,wraplength=1020).pack(fill='x',pady=4)
        self.detail=ttk.Frame(pattern)
        edit=self.detail
        self.start=tk.StringVar(value='1');self.end=tk.StringVar(value='1');self.face=tk.StringVar(value='1');self.direction=tk.StringVar(value='정순');self.start_student=tk.StringVar(value='1')
        for label,var,width in [('시작',self.start,5),('끝',self.end,5),('페이지순번',self.face,5),('시작 학생 위치',self.start_student,5)]:
            ttk.Label(edit,text=label).pack(side='left');ttk.Entry(edit,textvariable=var,width=width).pack(side='left',padx=4)
        ttk.Combobox(edit,textvariable=self.direction,values=['정순','역순'],width=7,state='readonly').pack(side='left',padx=4)
        ttk.Button(edit,text='선택 구간에 적용',command=self.apply_block).pack(side='left',padx=5)
        ttk.Button(edit,text='구간 복제',command=self.duplicate_block).pack(side='left')
        self.pattern_text=tk.StringVar(value='①에서 코드표와 PDF를 선택한 뒤 스캔 방식을 고르십시오.')
        self.pattern_label=ttk.Label(pattern,textvariable=self.pattern_text,wraplength=1020);self.pattern_label.pack(fill='x',pady=3)
        self.endpoints=EndpointPreview(pattern)
        nav=pattern_nav
        self.guide_review=ttk.Button(nav,text='다음: 출력 확인·저장 →',command=lambda:self.build(True),style='Accent.TButton');self.guide_review.pack(side='right')
        ttk.Button(nav,text='← 자료 불러오기',command=lambda:self.book.select(0)).pack(side='left')
        self.review=Review(review_tab,app,True)
        review_nav=ttk.Frame(self.review.frame);review_nav.pack(side='bottom',fill='x',padx=8,pady=2)
        ttk.Button(review_nav,text='← 스캔 순서·연결 확인',command=lambda:self.book.select(1)).pack(side='left')
        self.update_inputs()
        self.mode.trace_add('write',lambda *_:self.schedule_auto())
        self.per.trace_add('write',lambda *_:self.schedule_auto())
        top.protocol('WM_DELETE_WINDOW',self.close)
        app.place_dialog(top)
        self.visual_guide=ButtonGuide(top,lambda:batch_button(self))

    def session_payload(self):
        if self.auto_job:self.auto_rebuild()
        if not self.files or not self.roster_path or not self.roster_fingerprint:raise ValueError('코드표 XLSX와 PDF를 불러온 작업만 저장할 수 있습니다.')
        review=self.review
        editor=next((student['code'] for student in self.students if student_label(student)==review.code.get() or student['code']==review.code.get()),'')
        return {'schema':work_session.SCHEMA,'files':[{'path':p,'pages':n,'sha256':review.hashes.get(str(Path(p).resolve())) or digest(p)} for p,n in self.files],
                'roster':{'path':self.roster_path,'sha256':self.roster_fingerprint,'options':list(self.roster_options)},
                'student_codes':[[s['code'],s['class']] for s in self.students],
                'mode':self.mode.get(),'per':int(self.per.get()),'class_filter':self.klass.get(),'blocks':[asdict(b) for b in self.blocks],
                'rows':copy.deepcopy(review.rows),'rules':{p:work_session.pack_rules(rule) for p,rule in review.rules.items()},
                'ui':{'output_mode':review.output_mode.get(),'output_folder':review.output_folder,'filter':review.filter,'selection':review.selected(),
                      'step':self.book.index(self.book.select()),'view':review.view.get(),'fit':review.fit.get(),'zoom':review.zoom,
                      'pane_fraction':review.pane_fraction,'columns':{c:review.tree.column(c,'width') for c in review.tree['columns']},
                      'edit_open':review.edit_open,'output_open':review.output_open,'editor_code':editor,'editor_class':review.klass.get(),'editor_order':review.order.get(),
                      'last_saved':review.last_saved.get(),'scope':review.scope.get() if hasattr(review,'scope') else '전체'},
                'main_reference':getattr(review,'main_seen',None),'dpi':int(self.app.dpi.get())}

    def session_dirty(self):
        if not self.files:return False
        try:return work_session.signature(self.session_payload())!=self.session_saved
        except Exception:return True

    def save_session(self,path=None):
        if self.review.busy:return False
        try:
            payload=self.session_payload()
            if path is None:
                path=self.session_path or filedialog.asksaveasfilename(parent=self.top,initialdir=str(work_session.work_folder()),initialfile='답안작업.mhopae.json',defaultextension='.mhopae.json',filetypes=[('로컬 작업 파일','*.mhopae.json')])
            if not path:return False
            self.session_path=work_session.save(path,payload);self.session_saved=work_session.signature(payload)
            self.session_note.set('작업 저장 완료: '+self.session_path.name+' · 로컬 전용')
            self.review.message.set(f'작업 파일: {self.session_path} · 외부 업로드 금지. 불필요해지면 이 파일을 삭제하십시오.');return True
        except Exception as exc:self.review.message.set('작업 저장 실패: '+str(exc));self.session_note.set('작업 미저장 · 저장 실패');return False

    def confirm_close(self):
        if not self.session_dirty():return True
        answer=tk.StringVar(value='취소');dialog=tk.Toplevel(self.top);dialog.title('작업을 저장하고 닫기');dialog.transient(self.top);dialog.grab_set()
        ttk.Label(dialog,text='작업 파일에 저장하지 않은 변경이 있습니다. PDF 출력과 작업 저장은 별개입니다.',wraplength=460,padding=12).pack()
        bar=ttk.Frame(dialog,padding=10);bar.pack(fill='x')
        for name in ('취소','작업 저장','저장 없이 종료'):
            ttk.Button(bar,text=name,command=lambda v=name:(answer.set(v),dialog.destroy())).pack(side='left',padx=4)
        self.app.place_dialog(dialog);dialog.wait_window()
        return self.save_session() if answer.get()=='작업 저장' else answer.get()=='저장 없이 종료'

    def load_session(self,path=None):
        if self.review.busy:return False
        if path is None:
            if self.files and not self.confirm_close():return False
            path=filedialog.askopenfilename(parent=self.top,initialdir=str(work_session.work_folder()),filetypes=[('로컬 작업 파일','*.mhopae.json')])
        if not path:return False
        try:
            data,rows,students,rules,blocks,issues=work_session.load(path)
            if self.auto_job:self.top.after_cancel(self.auto_job);self.auto_job=None
            self.rebuilding=True;self.files=[(f['path'],f['pages']) for f in data['files']];self.students=students;self.blocks=blocks
            self.roster_path=data['roster']['path'];self.roster_options=data['roster']['options'];self.roster_fingerprint=data['roster']['sha256']
            self.mode.set(data['mode']);self.per.set(str(data['per']));self.klass.set(data['class_filter']);self.classbox.configure(values=['전체 학급']+list(dict.fromkeys(s['class'] for s in students)))
            self.filelist.delete(0,'end')
            for i,(p,n) in enumerate(self.files,1):self.filelist.insert('end',f'{i:02d}  |  {n}쪽  |  {Path(p).name}')
            self.input_text.set(f'복원: 명단 {len(students)}명 · PDF {len(self.files)}개 · 입력 검증 보류 {len(issues)}건')
            self.render_blocks();review=self.review;review.set_rows(rows,self.selected_students(),rules)
            # Keep original fingerprints even when an input is missing or has changed.
            review.hashes={f['path']:f['sha256'] for f in data['files']}
            ui=data['ui'];self.app.dpi.set(str(data['dpi']));review.output_mode.set(ui['output_mode']);review.output_folder=ui['output_folder'];review.last_saved.set(ui['last_saved'])
            review.view.set(ui['view']);review.fit.set(ui.get('fit','페이지 맞춤'));review.zoom=max(.25,min(4,float(ui.get('zoom',1))));review.zoom_text.set(f'{review.zoom:.0%}')
            review.pane_fraction=max(.42,min(.75,float(ui.get('pane_fraction',.53))));review.sash_initialized=False
            for c,w in ui.get('columns',{}).items():
                if c in review.tree['columns']:review.tree.column(c,width=max(65,min(1500,int(w))))
            review.filter_rows(ui['filter']);selected=[str(i) for i in ui['selection'] if review.tree.exists(str(i))]
            if selected:review.tree.selection_set(selected);review.tree.see(selected[0]);review.show(sync=True)
            review.code.set(ui.get('editor_code',''));review.klass.set(ui.get('editor_class',''));review.order.set(ui.get('editor_order','1'))
            review.toggle_edit(ui['edit_open'] or bool(issues));review.toggle_output(ui['output_open']);review.agreed.set(False)
            if hasattr(review,'scope'):review.scope.set(ui.get('scope','전체'))
            if hasattr(review,'main_seen'):review.main_seen=data.get('main_reference');review.update_rule_status()
            review.preview_cache.clear();review.update_estimate();self.book.select(max(0,min(2,int(ui['step']))));self.capture_setup();self.rebuilding=False
            self.session_path=work_session.local_path(path);self.session_saved=work_session.signature(self.session_payload()) if not issues else None
            self.session_note.set('복원 완료 · 입력 검증 보류 있음' if issues else '복원 완료 · 저장 전 출력 미리보기 재확인')
            review.message.set(' / '.join(issues) if issues else '작업을 복원했습니다. 출력 확인 체크는 다시 해야 합니다.');return True
        except Exception as exc:
            self.rebuilding=False;self.review.message.set('작업 이어하기 실패: '+str(exc));return False

    def layout_pattern(self,event=None):
        width=self.pattern_canvas.winfo_width();height=max(self.pattern_canvas.winfo_height(),self.pattern_content.winfo_reqheight())
        size=(width,height)
        if size!=self.pattern_layout:
            self.pattern_layout=size;self.pattern_canvas.itemconfigure(self.pattern_window,width=width,height=height);self.pattern_canvas.configure(scrollregion=(0,0,width,height))

    def next_pattern(self):
        if not self.students:self.input_text.set('코드표가 아직 없습니다. 코드표 XLSX를 선택하십시오.');return
        if not self.files:self.input_text.set('스캔 PDF를 추가한 뒤 다음 단계로 이동하십시오.');return
        self.book.select(1)

    def update_step(self,event=None):
        index=self.book.index(self.book.select());titles=('자료 불러오기 · 명단과 스캔 파일 선택','스캔 순서·연결 확인 · 묶음 방향 선택','출력 확인·저장 · 미리보기와 저장 대상 확인')
        self.step_text.set(f'{index+1} / 3 · {titles[index]}')

    def close(self):
        if self.review.busy:self.review.message.set('저장이 끝난 뒤 창을 닫을 수 있습니다.');return
        if not self.confirm_close():return
        if self.auto_job:self.top.after_cancel(self.auto_job)
        self.top.destroy()

    def toggle_details(self):
        if self.detail.winfo_manager():self.detail.pack_forget()
        else:self.detail.pack(fill='x',before=self.pattern_label)

    def remember_setup(self):
        if self.last_config:
            self.setup_history.append((self.last_config[0],self.last_config[1],copy.deepcopy(self.blocks),copy.deepcopy(self.review.rows)));self.setup_history=self.setup_history[-20:]

    def capture_setup(self):
        self.last_config=(self.mode.get(),self.per.get(),copy.deepcopy(self.blocks),copy.deepcopy(self.review.rows))

    def undo_setup(self):
        if self.review.busy or not self.setup_history:return
        mode,per,blocks,rows=self.setup_history.pop();self.rebuilding=True
        if self.auto_job:self.top.after_cancel(self.auto_job);self.auto_job=None
        self.mode.set(mode);self.per.set(per);self.blocks=blocks;self.render_blocks();self.build(False)
        self.review.set_rows(rows,self.selected_students(),self.review.rules);self.capture_setup();self.rebuilding=False

    def schedule_auto(self):
        if self.rebuilding or self.review.busy:return
        self.review.agreed.set(False)
        if self.auto_job:self.top.after_cancel(self.auto_job)
        self.auto_job=self.top.after(250,self.auto_rebuild)

    def auto_rebuild(self):
        if self.auto_job:self.top.after_cancel(self.auto_job)
        self.auto_job=None
        if self.review.busy:return
        self.remember_setup();self.rebuilding=True
        self.suggest_blocks();self.build(False);self.capture_setup();self.rebuilding=False

    def reset_proposal(self):self.auto_rebuild()

    def quick_reverse(self,event=None):
        chosen=self.blocktree.selection()
        if not chosen or self.review.busy:return
        self.remember_setup();i=int(chosen[0]);self.blocks[i].reverse=self.quick_direction.get()=='역순'
        # Direction alone cannot confirm an incomplete scan or locate a missing page.
        self.render_blocks();self.blocktree.selection_set(str(i));self.select_block();self.build(False);self.capture_setup()

    def select_card(self,index):
        self.blocktree.selection_set(str(index));self.select_block()

    def set_block_direction(self,index,value):
        if self.review.busy or not 0<=index<len(self.blocks):return
        self.select_card(index);self.quick_direction.set(value);self.quick_reverse()

    def invalidate_mapping(self):
        self.blocks=[]
        if hasattr(self,'basic'):self.render_blocks()
        if hasattr(self,'review') and not self.review.busy:self.review.set_rows([])

    def update_inputs(self):
        if hasattr(self,'review') and self.review.busy:return
        self.invalidate_mapping()
        classes=list(dict.fromkeys(s['class'] for s in self.students));self.classbox.configure(values=['전체 학급']+classes)
        if self.klass.get() not in ['전체 학급']+classes:self.klass.set('전체 학급')
        self.input_text.set(f'명단 {len(self.selected_students())}명 · 스캔 {len(self.files)}파일 / {sum(n for p,n in self.files)}페이지 · 코드가 빈 행은 나중에 지정합니다.')
        self.filelist.delete(0,'end')
        for index,(path,n) in enumerate(self.files,1):self.filelist.insert('end',f'{index:02d}  |  {n}쪽  |  {Path(path).name}')
        self.last_config=None;self.setup_history=[]
        if hasattr(self,'review') and self.files and self.students:self.auto_rebuild()

    def selected_students(self):return [s for s in self.students if self.klass.get()=='전체 학급' or s['class']==self.klass.get()]

    def choose_roster(self):
        path=filedialog.askopenfilename(parent=self.top,filetypes=[('코드표 XLSX','*.xlsx')])
        if not path:return
        def load():
            options=self.app.choose_roster_options(path)
            if options is None:return
            self.students=read_students(path,*options,allow_partial=True);self.roster_path=str(Path(path).resolve());self.roster_options=options;self.roster_fingerprint=digest(path);self.update_inputs()
        self.app.guarded(load)

    def choose_files(self):
        paths=filedialog.askopenfilenames(parent=self.top,filetypes=[('스캔 PDF','*.pdf')])
        for path in paths:
            if any(Path(path).resolve()==Path(p).resolve() for p,n in self.files):continue
            try:
                with fitz.open(path) as doc:
                    if doc.needs_pass or not len(doc):raise ValueError('비암호화·비어 있지 않은 PDF 필요')
                    self.files.append((str(Path(path).resolve()),len(doc)))
            except Exception as exc:self.input_text.set(f'{Path(path).name}: {exc}');return
        self.update_inputs()

    def remove_files(self):
        for i in reversed(self.filelist.curselection()):self.files.pop(i)
        self.update_inputs()

    def suggest_blocks(self):
        if self.auto_job:self.top.after_cancel(self.auto_job);self.auto_job=None
        try:
            n=len(self.selected_students());per=int(self.per.get())
            if n<1 or not self.files or per<1:raise ValueError('코드표 인원·입력 PDF·학생당 페이지 수를 확인하십시오.')
            self.blocks=[];face=1;student_start=0
            for path,count in self.files:
                if self.mode.get()=='faces':
                    for start in range(1,count+1,n):self.blocks.append(ScanBlock(path,start,min(start+n-1,count),face));face+=1
                else:
                    self.blocks.append(ScanBlock(path,1,count,1,False,student_start))
                    student_start+=count//per if self.mode.get()=='continuous' else count
            self.render_blocks();self.pattern_text.set('구간별 시작·끝·페이지순번과 정순/역순을 확인하십시오. 특정 구간만 고쳐도 나머지는 유지됩니다.')
        except Exception as exc:self.pattern_text.set(str(exc))

    def render_blocks(self):
        self.blocktree.delete(*self.blocktree.get_children())
        students=self.selected_students()
        for i,b in enumerate(self.blocks):
            count=b.end-b.start+1
            try:per=max(1,int(self.per.get())) if self.mode.get()=='continuous' else 1
            except ValueError:per=1
            indexes=[b.student_start,b.student_start+(count-1)//per]
            if b.reverse:indexes=[len(students)-1-j for j in indexes]
            labels=[student_label(students[j]) if 0<=j<len(students) else '학생 미확인' for j in indexes]
            self.blocktree.insert('','end',iid=str(i),values=(f'{i+1}번째 묶음 · {Path(b.source).name}',f'{b.start}~{b.end}쪽',b.end,f'{b.order}째 장' if self.mode.get()!='continuous' else f'1~{per}째 장','역순' if b.reverse else '정순',' → '.join(labels)))
        self.render_direction_cards()
        if not self.blocks and hasattr(self,'endpoints'):self.endpoints.clear()

    def render_direction_cards(self):
        for widget in self.cards.winfo_children():widget.destroy()
        self.direction_controls={}
        compact=0<len(self.blocks)<=4
        if compact:
            self.blocktree.pack_forget();self.cards.pack(fill='x',before=self.basic,pady=6)
            self.quick_label.pack_forget();self.quick_combo.pack_forget()
            students=self.selected_students()
            for i,b in enumerate(self.blocks):
                panel=ttk.Frame(self.cards,padding=(6,2),relief='solid',borderwidth=1);panel.pack(fill='x',pady=2);panel.columnconfigure(0,weight=1)
                title=f'{i+1}번째 묶음 · 원본 {b.start}~{b.end}쪽 · 답안 '+(f'1~{self.per.get()}째 장' if self.mode.get()=='continuous' else f'{b.order}째 장')
                ttk.Button(panel,text=title,command=lambda j=i:self.select_card(j)).grid(row=0,column=0,sticky='w')
                value=tk.StringVar(value='역순' if b.reverse else '정순');self.direction_controls[i]=value
                for col,label in ((1,'정순'),(2,'역순')):
                    ttk.Radiobutton(panel,text=label,value=label,variable=value,command=lambda j=i,v=value:self.set_block_direction(j,v.get())).grid(row=0,column=col,padx=6)
                per=max(1,int(self.per.get())) if self.mode.get()=='continuous' else 1
                indexes=[b.student_start,b.student_start+(b.end-b.start)//per]
                if b.reverse:indexes=[len(students)-1-j for j in indexes]
                def brief(j):
                    if not 0<=j<len(students):return '학생 미확인'
                    s=students[j];name=s.get('name') or '이름 미확인';code=s.get('code') or '코드 미확인'
                    return f"{s.get('class') or '?'} {s.get('number') or '?'}번 {name[:7]+('…' if len(name)>7 else '')}"
                text=f'{Path(b.source).name[:38]} · 처음 {brief(indexes[0])} → 마지막 {brief(indexes[1])}'
                ttk.Label(panel,text=text,wraplength=1000).grid(row=1,column=0,columnspan=3,sticky='w')
        else:
            self.cards.pack_forget();self.blocktree.pack(fill='both',expand=True,before=self.basic,pady=6)
            self.quick_label.pack(side='left',before=self.detail_button);self.quick_combo.pack(side='left',before=self.detail_button,padx=6)

    def select_block(self,event=None):
        chosen=self.blocktree.selection()
        if not chosen:return
        b=self.blocks[int(chosen[0])];self.start.set(str(b.start));self.end.set(str(b.end));self.face.set(str(b.order));self.direction.set('역순' if b.reverse else '정순');self.start_student.set(str(b.student_start+1))
        self.quick_direction.set('역순' if b.reverse else '정순')
        values=self.blocktree.item(chosen[0],'values')
        self.selected_endpoints.set('선택 묶음 · 예상 연결(추정): '+values[-1])
        expected=values[-1].split(' → ',1)
        if hasattr(self,'endpoints'):self.endpoints.set_pages(b.source,b.start,b.end,expected if len(expected)==2 else [values[-1]]*2)

    def apply_block(self):
        chosen=self.blocktree.selection()
        if not chosen:return
        try:
            i=int(chosen[0]);old=self.blocks[i];b=ScanBlock(old.source,int(self.start.get()),int(self.end.get()),int(self.face.get()),self.direction.get()=='역순',int(self.start_student.get())-1)
            count=next(n for p,n in self.files if p==b.source)
            if not (1<=b.start<=b.end<=count and b.order>=1 and b.student_start>=0):raise ValueError('구간·페이지순번·시작 학생 위치를 확인하십시오.')
            self.remember_setup();b.confirmed=True;self.blocks[i]=b;self.render_blocks();self.blocktree.selection_set(str(i));self.select_block();self.build(False);self.capture_setup()
        except Exception as exc:self.pattern_text.set(str(exc))

    def duplicate_block(self):
        chosen=self.blocktree.selection()
        if chosen:
            self.remember_setup();b=self.blocks[int(chosen[0])];self.blocks.append(ScanBlock(**vars(b)));self.render_blocks();self.build(False);self.capture_setup()

    def build(self,navigate=True):
        if self.review.busy:return
        if navigate and self.auto_job:self.auto_rebuild()
        if navigate and self.last_config and self.last_config[:3]==(self.mode.get(),self.per.get(),self.blocks):self.book.select(2);return
        try:
            if not self.blocks:self.suggest_blocks()
            rows=build_mapping(self.blocks,self.selected_students(),self.mode.get(),int(self.per.get()))
            # Pages omitted from edited ranges remain explicit, pending rows.
            present={(r['source'],r['page']) for r in rows}
            for path,n in self.files:
                for p in range(n):
                    if (path,p) not in present:rows.append({'source':path,'page':p,'identity':None,'excluded':False,'note':'나중에 지정: 스캔 구간에 미배정'})
            rules={}
            for path,n in self.files:
                if self.app.pdf and Path(path).resolve()==Path(self.app.pdf).resolve():rules[path]=self.app.rules.snapshot()
                else:rules[path]=PageRules(self.app.rules.cut,list(self.app.rules.masks))
            self.review.set_rows(rows,self.selected_students(),rules)
            ready=sum(s=='출력 가능' for s in self.review.states.values())
            self.pattern_text.set(f'자동 제안 {len(rows)}쪽 · 연결된 {ready}쪽 · 확인할 {len(rows)-ready}쪽. 제안은 실제 학생 확인 완료를 뜻하지 않습니다.')
            if self.blocks and not self.blocktree.selection():self.blocktree.selection_set('0');self.select_block()
            if navigate:self.book.select(2)
        except Exception as exc:
            self.review.set_rows([]);self.pattern_text.set('설정을 확인하십시오: '+str(exc))


def regroup_dialog(app):
    top=tk.Toplevel(app.root);top.title('페이지 정보로 묶기·나누기');top.geometry('1180x820');top.minsize(1000,700);top.transient(app.root)
    ttk.Label(top,text='이미 비식별화된 PDF 전용 · 여러 PDF의 학생코드·학급·페이지순번으로 재구성합니다. 추가 개인정보 제거는 하지 않습니다.',wraplength=1080).pack(fill='x',padx=10,pady=8)
    bar=ttk.Frame(top);bar.pack(fill='x',padx=8)
    review=Review(top,app,False)
    def load():
        if review.busy:return
        paths=filedialog.askopenfilenames(parent=top,filetypes=[('정보를 기록한 PDF','*.pdf')])
        if paths:review.set_rows(import_pages(paths),review.students)
    guide_load=ttk.Button(bar,text='PDF 여러 개 불러오기',command=load);guide_load.pack(side='left')
    def roster():
        path=filedialog.askopenfilename(parent=top,filetypes=[('로컬 학생 코드표','*.xlsx')])
        if not path:return
        def read_roster():
            options=app.choose_roster_options(path)
            if options is not None:review.set_rows(review.rows,read_students(path,*options,allow_partial=True),review.rules)
        app.guarded(read_roster)
    ttk.Button(bar,text='학생정보 표시용 코드표',command=roster).pack(side='left',padx=4)
    ttk.Label(bar,text='정보가 없는 페이지만 나중에 지정할 수 있습니다. 중복·학급 충돌 페이지는 자동 배정하지 않습니다.').pack(side='left',padx=8)
    top.protocol('WM_DELETE_WINDOW',lambda:top.destroy() if not review.busy else review.message.set('저장이 끝난 뒤 닫으십시오.'))
    top.review=review
    app.place_dialog(top)
    top.visual_guide=ButtonGuide(top,lambda:review_button(review) if review.rows else guide_load)
    return top
