"""Hangrang local PDF anonymizer; no network or OCR functionality."""
import io
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk
from core import fitz, digest, read_roster_details, validate_ids, make_pdf, plan_names, export_batch, export_bundle, source_page, roster_sheets
from file_drop import FileDrop
from roster_codes import read_students, issue_missing, save_code_table

from editor_ui import EditorUI
from page_rules import PageRules
from dpi_info import detect_dpi
from page_identity import identity as page_identity
from batch_ui import BatchDialog,regroup_dialog
from visual_guide import ButtonGuide, main_button

class App(EditorUI):
    def __init__(self, root):
        self.root = root
        # Use bundled Tk's message box placement on the owning window.
        # Windows native message boxes can center on another monitor.
        root.tk.call('source',str(Path(str(root.tk.call('set','tk_library')))/'msgbox.tcl'))
        messagebox.Message.command='::tk::MessageBox'
        locale=str(root.tk.call('::msgcat::mclocale'))
        for original,translated in (('&Yes','예'),('&No','아니요'),('&OK','확인'),('&Cancel','취소'),('&Retry','다시 시도'),('&Abort','중단'),('&Ignore','무시')):
            root.tk.call('::msgcat::mcset',locale,original,translated)
        root.protocol('WM_DELETE_WINDOW',self.close_app)
        self.configure_style()
        root.title('무명호패 v0.1.0 — PDF 비식별화 도구')
        root.geometry('1380x940')
        root.minsize(1080,760)
        self.pdf = self.roster = None
        self.records = []
        self.visible_records = []
        self.selected = []
        self.cut = 0.18
        self.reviewed = set()
        self.preview_rect = None
        self.photo = None
        self.busy = False
        self.generation = 0
        self.preview_windows = []
        self.drop_loading = False
        self.students = []
        self.visible_student_indices = []
        self.code_dirty = False
        self.output_folders = set()
        self.excluded_ids = set()
        self.candidate_order=[]
        self.candidate_excluded=set()
        self.show_excluded_candidates=tk.BooleanVar(value=False)
        self.controls = {}
        self.buttons = []
        def button(parent, key, label, command, **options):
            widget = ttk.Button(parent, text=label, command=command, **options)
            self.controls[key] = widget
            self.buttons.append(widget)
            return widget
        header = ttk.Frame(root, padding=(12,8));header.pack(fill='x')
        ttk.Label(header,text='무명호패 v0.1.0',font=('맑은 고딕',14,'bold')).pack(side='left')
        ttk.Label(header,text='  PDF 비식별화 도구  |  오프라인',style='Muted.TLabel').pack(side='left')
        button(header,'reset','새 작업',self.reset).pack(side='right')
        button(header,'merge','학생별 PDF 병합',self.merge_dialog).pack(side='right',padx=8)
        files = ttk.Frame(root,padding=(12,0,12,6));files.pack(fill='x')
        button(files,'load_roster','명단 XLSX 열기',self.load_xlsx).pack(side='left',padx=(0,6))
        button(files,'load_pdf','원본 PDF 열기',self.load_pdf).pack(side='left')
        ttk.Label(files,text='  PDF·XLSX 끌어 놓기').pack(side='left')
        button(files,'batch_scan','대량 스캔 대응·출력',lambda:BatchDialog(self)).pack(side='right',padx=5)
        button(files,'regroup','정보로 묶기·나누기',lambda:regroup_dialog(self)).pack(side='right')
        self.file_status=tk.StringVar(value='명단과 PDF를 불러오십시오.')
        self.file_label=ttk.Label(root,textvariable=self.file_status,style='Muted.TLabel',padding=(12,0,12,6),wraplength=1100)
        self.file_label.pack(fill='x')
        self.body=ttk.Panedwindow(root,orient='horizontal');self.body.pack(fill='both',expand=True,padx=12)
        lists=ttk.Frame(self.body,width=410);self.body.add(lists,weight=0)
        roster_tools=ttk.LabelFrame(lists,text='1  학생 명단 · 코드',padding=8);roster_tools.pack(fill='x')
        tools=ttk.Frame(roster_tools);tools.pack(fill='x')
        button(tools,'generate','빈 코드 발급',self.generate_codes).pack(side='left')
        button(tools,'table','대조표 보기',self.show_code_table).pack(side='left',padx=4)
        button(tools,'save_table','대조표 새 파일 저장',self.save_codes).pack(side='left')
        self.code_status=tk.StringVar(value='기존 코드는 유지하고 빈 코드만 발급합니다.')
        self.code_label=ttk.Label(roster_tools,textvariable=self.code_status,wraplength=350)
        self.code_label.pack(fill='x',pady=(4,0))
        roster_tools.bind('<Configure>',lambda e:self.code_label.configure(wraplength=max(180,e.width-20)))
        filters=ttk.Frame(lists);filters.pack(fill='x',pady=(8,2))
        ttk.Label(filters,text='학급').pack(side='left')
        self.class_filter=tk.StringVar(value='전체 학급')
        self.class_combo=ttk.Combobox(filters,textvariable=self.class_filter,values=['전체 학급'],state='readonly',width=14)
        self.class_combo.pack(side='left',padx=5)
        self.class_combo.bind('<<ComboboxSelected>>',lambda e:self.filter_candidates())
        self.candidate_view=ttk.Checkbutton(filters,text='제외 후보만 보기',variable=self.show_excluded_candidates,command=self.filter_candidates)
        self.candidate_view.pack(side='right')
        ttk.Label(lists,text='명단 후보  ·  순서 조정 후 「대상에 추가」').pack(anchor='w')
        candidate_tools=ttk.Frame(lists);candidate_tools.pack(fill='x')
        button(candidate_tools,'candidate_exclude','후보 제외',self.exclude_candidates).pack(side='left')
        button(candidate_tools,'candidate_restore','후보 복구',self.restore_candidates).pack(side='left',padx=3)
        button(candidate_tools,'candidate_up','후보 ↑',lambda:self.move_candidates(-1)).pack(side='left')
        button(candidate_tools,'candidate_down','후보 ↓',lambda:self.move_candidates(1)).pack(side='left',padx=3)
        def student_list(parent,height):
            frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
            frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
            widget=tk.Listbox(frame,selectmode='extended',exportselection=False,width=43,height=height,font=('맑은 고딕',10),background='#FFFFFF',foreground='#202124',selectbackground='#2B579A',selectforeground='#FFFFFF',activestyle='dotbox',highlightcolor='#1A73E8',highlightthickness=1,relief='solid',borderwidth=1)
            widget.grid(row=0,column=0,sticky='nsew')
            vertical=ttk.Scrollbar(frame,orient='vertical',command=widget.yview);vertical.grid(row=0,column=1,sticky='ns')
            horizontal=ttk.Scrollbar(frame,orient='horizontal',command=widget.xview);horizontal.grid(row=1,column=0,sticky='ew')
            widget.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
            return widget
        self.available=student_list(lists,7)
        self.available.bind('<<ListboxSelect>>',lambda e:self.update_counts())
        actions=ttk.Frame(lists);actions.pack(fill='x',pady=5)
        button(actions,'include','대상에 추가 ↓',self.include).pack(side='left')
        button(actions,'exclude','대상에서 제외',self.exclude).pack(side='left',padx=4)
        ttk.Label(lists,text='2  이번 PDF 처리 대상  ·  이 순서대로 페이지와 연결').pack(anchor='w')
        self.order=student_list(lists,7)
        self.order.bind('<<ListboxSelect>>',lambda e:self.update_counts())
        movebar=ttk.Frame(lists);movebar.pack(fill='x',pady=4)
        button(movebar,'up','선택 학생 위로',lambda:self.move(-1)).pack(side='left')
        button(movebar,'down','선택 학생 아래로',lambda:self.move(1)).pack(side='left',padx=4)
        self.selection_status=tk.StringVar()
        self.count_label=ttk.Label(lists,textvariable=self.selection_status,wraplength=390)
        self.count_label.pack(fill='x',pady=(2,3))
        ttk.Label(lists,text='Ctrl: 개별 선택 · Shift: 범위 · Ctrl+A: 전체\n제외는 이번 작업에만 적용됩니다. 복구한 학생은 끝에 추가됩니다.',wraplength=390).pack(fill='x',pady=(0,6))
        # Grid allocates remaining height to the two lists, preserving commands.
        left_widgets=lists.winfo_children()
        for widget in left_widgets:widget.pack_forget()
        for row,widget in enumerate(left_widgets):
            widget.grid(row=row,column=0,sticky='nsew' if widget in (self.available.master,self.order.master) else 'ew',pady=2)
            lists.rowconfigure(row,weight=1 if widget in (self.available.master,self.order.master) else 0)
        lists.columnconfigure(0,weight=1)
        for widget in (self.available,self.order):
            widget.bind('<Control-a>',self.select_all);widget.bind('<Control-A>',self.select_all)
        right=ttk.Frame(self.body);self.body.add(right,weight=1)
        nav=ttk.Frame(right,padding=(8,0));nav.pack(fill='x')
        ttk.Label(nav,text='3  원본·절단 확인   페이지').pack(side='left')
        self.page=tk.IntVar(value=1)
        self.spin=ttk.Spinbox(nav,from_=1,to=1,textvariable=self.page,width=6,command=self.show_page)
        self.spin.pack(side='left',padx=4);self.spin.bind('<Return>',lambda e:self.show_page())
        button(nav,'show_page','이동',self.show_page).pack(side='left')
        self.page_info=tk.StringVar(value='PDF를 불러오면 원본이 표시됩니다.')
        ttk.Label(nav,textvariable=self.page_info).pack(side='left',padx=8)
        settings=ttk.Frame(right,padding=(8,5));settings.pack(fill='x')
        ttk.Label(settings,text='DPI 선택').pack(side='left')
        self.dpi=tk.StringVar(value='300')
        self.dpi_combo=ttk.Combobox(settings,textvariable=self.dpi,values=['300','400'],state='readonly',width=5)
        self.dpi_combo.pack(side='left',padx=(4,10))
        self.dpi.trace_add('write',lambda *_:self.settings_changed())
        self.direction=tk.StringVar(value='원래 순서')
        self.direction_combo=ttk.Combobox(settings,textvariable=self.direction,values=['원래 순서','전체 페이지 역순'],state='readonly',width=18)
        self.direction_combo.pack(side='left')
        self.direction.trace_add('write',lambda *_:self.mapping_changed())
        self.page_order=tk.StringVar(value='1')
        ttk.Label(settings,text='페이지순번').pack(side='left',padx=(8,0))
        self.page_order_spin=ttk.Spinbox(settings,from_=1,to=9999,textvariable=self.page_order,width=5);self.page_order_spin.pack(side='left',padx=4)
        self.page_order.trace_add('write',lambda *_:self.settings_changed())
        button(settings,'zero','절단선 0%로',self.zero).pack(side='right')
        self.init_editor(right)
        self.canvas=tk.Canvas(right,bg='#E9EDF2',width=780,height=650,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True,padx=(8,0))
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<B1-Motion>',self.drag)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        scroll=ttk.Scrollbar(right,orient='horizontal',command=self.canvas.xview);scroll.pack(fill='x')
        self.canvas.configure(xscrollcommand=scroll.set)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-1 if e.delta>0 else 1,'units'))
        self.canvas.bind('<Configure>',lambda e:self.show_page())
        pdf_hint=ttk.Label(right,text='원본을 클릭해 절단선을 정합니다. 선 위를 제거하며, 본문 개인정보는 자동 판독하지 않습니다.',wraplength=780)
        pdf_hint.pack(fill='x',padx=8,pady=4)
        output=ttk.LabelFrame(right,text='4  결과 확인 · 저장',padding=8);output.pack(fill='x',padx=(8,0),pady=(0,6))
        self.output_mode=tk.StringVar(value='individual')
        choices=ttk.Frame(output);choices.pack(fill='x')
        ttk.Radiobutton(choices,text='학생코드별 개별 PDF',variable=self.output_mode,value='individual').pack(side='left')
        ttk.Radiobutton(choices,text='하나의 묶음 PDF',variable=self.output_mode,value='bundle').pack(side='left',padx=6)
        output_actions=ttk.Frame(output);output_actions.pack(fill='x',pady=(4,0))
        button(output_actions,'run','PDF 생성',self.run).pack(side='right')
        button(output_actions,'preview','결과 미리보기',self.preview).pack(side='right',padx=6)
        self.canvas.pack_forget();pdf_hint.pack_forget();output.pack_forget()
        pdf_hint.pack(side='bottom',fill='x',padx=8,pady=4)
        output.pack(side='bottom',fill='x',padx=(8,0),pady=(0,6))
        self.canvas.pack(fill='both',expand=True,padx=(8,0))
        self.ready_text=tk.StringVar()
        self.ready_label=ttk.Label(root,textvariable=self.ready_text,style='Status.TLabel',padding=(12,3),wraplength=1100)
        self.ready_label.pack(fill='x')
        self.status=tk.StringVar(value='명단과 PDF를 불러와 작업을 시작하십시오.')
        self.status_label=ttk.Label(root,textvariable=self.status,style='Muted.TLabel',padding=(12,2,12,8),wraplength=1100)
        self.status_label.pack(fill='x')
        self.status_label.pack_configure(before=self.body,side='bottom')
        self.ready_label.pack_configure(before=self.body,side='bottom')
        root.bind('<Configure>',self.resize_labels,add='+')
        self.controls['run'].configure(style='Accent.TButton')
        self.controls['preview'].configure(style='Secondary.TButton')
        self.controls['save_table'].configure(style='Secondary.TButton')
        self.update_counts()
        self.file_drop=FileDrop(root,self.handle_drop)
        self.visual_guide=ButtonGuide(root,lambda:main_button(self))

    def configure_style(self):
        # Presentation only: keep the approved layout, commands and data flow.
        bg,text,muted,blue='#F8F9FA','#202124','#5F6368','#1A73E8'
        self.root.configure(background=bg)
        style=ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.',font=('맑은 고딕',9),background=bg,foreground=text)
        style.configure('TFrame',background=bg)
        style.configure('TLabel',background=bg,foreground=text)
        style.configure('Muted.TLabel',foreground=muted)
        style.configure('Status.TLabel',foreground='#174EA6',font=('맑은 고딕',9,'bold'))
        style.configure('TLabelframe',background=bg,bordercolor='#DADCE0',relief='solid')
        style.configure('TLabelframe.Label',font=('맑은 고딕',9,'bold'),foreground=text)
        style.configure('TButton',padding=(7,4),background='#FFFFFF',bordercolor='#DADCE0',focuscolor=blue)
        style.map('TButton',background=[('disabled','#F1F3F4'),('active','#E8F0FE')],foreground=[('disabled','#9AA0A6')])
        style.configure('Accent.TButton',foreground='#FFFFFF',background=blue,bordercolor=blue,padding=(10,5))
        style.map('Accent.TButton',background=[('disabled','#E8EAED'),('pressed','#174EA6'),('active','#1558B0')],foreground=[('disabled','#80868B')])
        style.configure('Secondary.TButton',foreground='#174EA6',padding=(10,5))
        style.configure('TCombobox',padding=2)
        style.configure('TSpinbox',padding=2)

    def notice(self,method,*args,**kwargs):
        kwargs.setdefault('parent',self.root)
        return getattr(messagebox,method)(*args,**kwargs)

    def resize_labels(self,event):
        if event.widget is not self.root:return
        width=max(300,event.width-30)
        for label in (self.file_label,self.ready_label,self.status_label):label.configure(wraplength=width)
        self.count_label.configure(wraplength=max(280,self.available.winfo_width()-12))
    def settings_changed(self):
        self.reviewed.clear()
        self.invalidate_previews()
        self.update_controls()
    def place_dialog(self,top):
        top.update_idletasks()
        width,height=top.winfo_width(),top.winfo_height()
        x=self.root.winfo_rootx()+(self.root.winfo_width()-width)//2
        y=self.root.winfo_rooty()+(self.root.winfo_height()-height)//2
        x=max(0,min(x,max(0,top.winfo_screenwidth()-width-16)))
        y=max(0,min(y,max(0,top.winfo_screenheight()-height-56)))
        top.geometry(f'+{x}+{y}')
    def update_controls(self):
        if not hasattr(self,'ready_text'):return
        candidates=list(self.available.curselection())
        chosen=bool(self.order.curselection())
        have_pdf=bool(self.pdf)
        coded_candidates=bool(candidates) and not self.show_excluded_candidates.get() and all(self.visible_records[i][0] for i in candidates)
        ready=bool(self.roster and have_pdf and self.selected and not self.code_dirty and len(self.selected)==getattr(self,'count',0))
        sampled=0 in self.reviewed and (getattr(self,'count',0)==1 or any(n>0 for n in self.reviewed))
        enabled={'reset':True,'load_roster':True,'load_pdf':True,'generate':any(not r['code'] and i not in self.candidate_excluded for i,r in enumerate(self.students)) and not self.show_excluded_candidates.get(),
                 'table':bool(self.students),'save_table':bool(self.students),'include':coded_candidates,
                 'candidate_exclude':bool(candidates) and not self.show_excluded_candidates.get(),
                 'candidate_restore':bool(candidates) and self.show_excluded_candidates.get(),
                 'candidate_up':bool(candidates) and not self.show_excluded_candidates.get(),
                 'candidate_down':bool(candidates) and not self.show_excluded_candidates.get(),
                 'exclude':chosen,'up':chosen,'down':chosen,'show_page':have_pdf,'zero':have_pdf,
                 'preview':have_pdf,'run':ready and sampled}
        for key,button in self.controls.items():button.configure(state='normal' if enabled.get(key,True) and not self.busy else 'disabled')
        self.spin.configure(state='normal' if have_pdf and not self.busy else 'disabled')
        self.class_combo.configure(state='readonly' if self.records and not self.busy else 'disabled')
        self.candidate_view.configure(state='normal' if self.records and not self.busy else 'disabled')
        for combo in (self.dpi_combo,self.direction_combo):combo.configure(state='readonly' if have_pdf and not self.busy else 'disabled')
        self.file_status.set(f"명단: {Path(self.roster).name if self.roster else '미선택'}  |  PDF: {Path(self.pdf).name if self.pdf else '미선택'}")
        if self.busy:reason='PDF 생성 중입니다. 입력과 순서는 완료 후 변경할 수 있습니다.'
        elif not self.roster:reason='명단 XLSX를 불러오십시오. 기존 코드가 없어도 시작할 수 있습니다.'
        elif self.code_dirty:reason='새 코드가 미저장 상태입니다. 대조표를 새 XLSX로 저장하십시오.'
        elif not have_pdf:reason='원본 PDF를 불러오십시오.'
        elif not self.selected:reason='명단 후보를 선택한 뒤 「대상에 추가」를 누르십시오.'
        elif len(self.selected)!=getattr(self,'count',0):reason=f"PDF {self.count}페이지와 처리 대상 {len(self.selected)}명이 다릅니다. 대상과 페이지 순서를 확인하십시오."
        elif not sampled:reason='「결과 미리보기」에서 '+('첫 페이지를 확인하십시오.' if self.count==1 else '첫 페이지와 다른 표본 페이지를 확인하십시오.')
        else:reason='생성 준비 완료 · 선택한 저장 방식과 페이지-학생 순서를 최종 확인한 뒤 PDF를 생성하십시오.'
        self.ready_text.set(reason)
    def choose_roster_options(self,path):
        names=roster_sheets(path)
        top=tk.Toplevel(self.root);top.title('명단 불러오기 설정');top.transient(self.root)
        top.resizable(False,False)
        frame=ttk.Frame(top,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='시트와 코드 열을 확인하십시오. 코드 없는 명단도 불러올 수 있습니다.').grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,10))
        ttk.Label(frame,text='명단 시트').grid(row=1,column=0,sticky='w',padx=(0,10))
        sheet=tk.StringVar(value=names[0])
        select=ttk.Combobox(frame,textvariable=sheet,values=names,state='readonly',width=32);select.grid(row=1,column=1,sticky='ew')
        ttk.Label(frame,text='코드 열 이름').grid(row=2,column=0,sticky='w',pady=8)
        column=ttk.Entry(frame,width=35);column.grid(row=2,column=1,sticky='ew',pady=8)
        ttk.Label(frame,text='빈칸이면 학생코드·비식별코드 등을 자동 검색합니다.').grid(row=3,column=0,columnspan=2,sticky='w')
        choice=[]
        def accept():choice.append((sheet.get(),column.get() or None));top.destroy()
        actions=ttk.Frame(frame);actions.grid(row=4,column=0,columnspan=2,sticky='e',pady=(12,0))
        ttk.Button(actions,text='취소',command=top.destroy).pack(side='right')
        ttk.Button(actions,text='명단 불러오기',command=accept).pack(side='right',padx=8)
        top.bind('<Return>',lambda e:accept());top.bind('<Escape>',lambda e:top.destroy())
        self.place_dialog(top)
        top.grab_set();select.focus_set();self.root.wait_window(top)
        return choice[0] if choice else None
    def guarded(self, operation):
        try: return operation()
        except Exception as e: self.notice('showerror','확인 필요', str(e))
    def handle_drop(self, paths):
        if self.busy or self.drop_loading:
            self.status.set('처리 중에는 파일을 불러올 수 없습니다. 완료 후 다시 놓으십시오.')
            return
        files = [Path(p) for p in paths]
        extensions = [p.suffix.lower() for p in files]
        if (not files or len(files) > 2 or len(set(extensions)) != len(files)
                or any(ext not in ('.pdf', '.xlsx') for ext in extensions)
                or any(not p.is_file() for p in files)):
            self.notice('showerror','파일 놓기', 'PDF 또는 XLSX 파일을 각 종류 1개씩 놓으십시오. 폴더와 다른 형식은 지원하지 않습니다.')
            return
        self.drop_loading = True
        try:
            # Load the roster first; cancellation stops the remaining dropped files.
            for path in sorted(files, key=lambda p:p.suffix.lower() != '.xlsx'):
                load = self.load_xlsx if path.suffix.lower() == '.xlsx' else self.load_pdf
                if load(str(path)) is not True:
                    break
        finally:
            self.drop_loading = False
    def load_xlsx(self, path=None):
        if self.busy:return
        if self.code_dirty and not self.notice('askyesno','미저장 코드', '새로 발급한 코드가 저장되지 않았습니다. 저장하지 않고 다른 명단을 불러올까요?', parent=self.root):return
        path = path or filedialog.askopenfilename(filetypes=[('Excel', '*.xlsx')])
        if not path:return
        try:options=self.choose_roster_options(path)
        except Exception as e:self.notice('showerror','명단 확인',str(e),parent=self.root);return
        if options is None:return
        sheet,column=options
        def load():
            students = read_students(path, sheet, column or None)
            self.students, self.roster, self.selected = students, path, [];self.roster_options=options
            self.excluded_ids.clear()
            self.candidate_order=list(range(len(students)));self.candidate_excluded.clear();self.show_excluded_candidates.set(False)
            self.code_dirty = False
            self.roster_hash = digest(path)
            self.class_filter.set('전체 학급')
            self.rebuild_students()
            return True
        return self.guarded(load)
    def rebuild_students(self):
        self.records = [(r['code'], f"{r['class']} | {r['number']} | {r['name']} | {r['code'] or '(미발급)'}", r['class']) for r in self.students]
        if set(self.candidate_order)!=set(range(len(self.records))):self.candidate_order=list(range(len(self.records)))
        self.candidate_excluded.intersection_update(range(len(self.records)))
        self.class_combo.configure(values=['전체 학급'] + list(dict.fromkeys(r[2] for r in self.records)))
        self.filter_candidates()
        self.refresh()
        count = sum(not r['code'] for r in self.students)
        self.code_status.set(f'명단 {len(self.students)}명 · 미발급 {count}명\n'+('코드 저장 필요 · 「대조표 새 파일 저장」을 누르십시오.' if self.code_dirty else '기존/저장된 코드 사용'))
        self.update_controls()
    def generate_codes(self):
        if self.busy:return
        def generate():
            if not self.students:raise ValueError('학생 명단 XLSX를 먼저 불러오십시오.')
            chosen = list(self.available.curselection())
            indices = [self.visible_student_indices[i] for i in chosen] if chosen else [i for i in range(len(self.students)) if i not in self.candidate_excluded]
            indices=[i for i in indices if i not in self.candidate_excluded]
            count = sum(not self.students[i]['code'] for i in indices)
            if not count:
                self.notice('showinfo','코드 발급', '선택 범위의 기존 코드는 유지합니다. 미발급 코드가 없습니다.');return
            scope = '선택한 행 중' if chosen else '제외하지 않은 후보 중'
            if not self.notice('askyesno','미발급 코드만 발급', f'{scope} 미발급 {count}명에게만 난수 코드를 발급합니다. 기존 코드는 변경하지 않습니다. 계속할까요?', parent=self.root):return
            self.students, issued = issue_missing(self.students, indices)
            self.code_dirty = True
            self.rebuild_students()
            self.status.set(f'새 코드 {issued}개 발급 완료. 대조표를 새 XLSX로 저장한 뒤 PDF를 실행하십시오.')
        self.guarded(generate)
    def show_code_table(self):
        if self.busy or not self.students:return
        top = tk.Toplevel(self.root);top.title('학생코드 대조표 — 로컬 확인용');top.geometry('700x420')
        columns = ('학급','번호','이름','비식별코드')
        table = ttk.Treeview(top, columns=columns, show='headings')
        for col in columns:
            table.heading(col,text=col);table.column(col,width=130 if col != '비식별코드' else 230)
        scroll = ttk.Scrollbar(top,orient='vertical',command=table.yview);table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');table.pack(fill='both',expand=True)
        for row in self.students:
            table.insert('', 'end', values=tuple(row[k] or '(미발급)' for k in ('class','number','name','code')))
        ttk.Label(top,text='실명 대조표는 로컬 보관용입니다. PDF 출력과 구분하여 저장하십시오.').pack()
        self.preview_windows.append(top)
        self.place_dialog(top)
    def save_codes(self):
        if self.busy:return
        def save():
            if not self.students:raise ValueError('학생 명단 XLSX를 먼저 불러오십시오.')
            path = filedialog.asksaveasfilename(title='실명 대조표 — PDF와 분리된 로컬 위치에 새 파일 저장',defaultextension='.xlsx',initialfile='학생코드_대조표.xlsx',filetypes=[('Excel','*.xlsx')],confirmoverwrite=False)
            if not path:return
            forbidden = set(self.output_folders)
            if self.pdf:forbidden.add(str(Path(self.pdf).resolve().parent))
            try:
                saved = save_code_table(self.students,path,forbidden)
            except FileExistsError:
                raise ValueError('동일 이름 파일이 있습니다. 덮어쓰지 않았습니다. 다른 이름으로 다시 저장하거나 취소하십시오.')
            self.roster = str(saved);self.roster_hash=digest(saved);self.code_dirty=False
            self.rebuild_students()
            self.status.set('대조표 새 XLSX 저장 완료. PDF 출력은 아직 실행하지 않았습니다.')
            self.notice('showinfo','대조표 저장 완료', f'새 대조표를 저장했습니다.\n{saved}\nPDF 출력 성공과는 별도입니다.',parent=self.root)
        self.guarded(save)
    def filter_candidates(self):
        if self.busy:return
        klass=self.class_filter.get()
        if set(self.candidate_order)!=set(range(len(self.records))):self.candidate_order=list(range(len(self.records)))
        excluded=self.show_excluded_candidates.get()
        self.visible_student_indices=[i for i in self.candidate_order
            if (i in self.candidate_excluded)==excluded and (klass=='전체 학급' or (self.records[i][2] or '(학급 없음)')==klass)]
        self.visible_records=[self.records[i] for i in self.visible_student_indices]
        self.render_candidates(False)
        self.update_counts()
    def exclude_candidates(self):
        if self.busy or self.show_excluded_candidates.get():return
        rows={self.visible_student_indices[i] for i in self.available.curselection()}
        if not rows:
            self.status.set('제외할 학생을 명단 후보에서 선택하십시오. 변경하지 않았습니다.');return
        self.candidate_excluded.update(rows)
        codes={self.records[i][0] for i in rows if self.records[i][0]}
        previous=list(self.selected)
        self.selected=[code for code in self.selected if code not in codes]
        self.filter_candidates()
        if self.selected!=previous:self.refresh()
        self.status.set(f'후보 {len(rows)}명 제외. 이미 포함된 처리 대상에서도 빠집니다. 「제외 후보만 보기」에서 복구할 수 있습니다. 원본·코드는 보존됩니다.')
    def restore_candidates(self):
        if self.busy or not self.show_excluded_candidates.get():return
        rows={self.visible_student_indices[i] for i in self.available.curselection()}
        if not rows:
            self.status.set('복구할 학생을 제외 후보에서 선택하십시오. 변경하지 않았습니다.');return
        self.candidate_excluded.difference_update(rows)
        self.filter_candidates()
        self.status.set(f'후보 {len(rows)}명 복구. 「제외 후보만 보기」를 해제하면 기존 후보 순서에 다시 표시됩니다. 처리 대상에는 자동 추가하지 않습니다.')
    def move_candidates(self,delta):
        if self.busy or self.show_excluded_candidates.get() or delta not in (-1,1):return
        chosen=set(self.available.curselection())
        if not chosen:
            self.status.set('이동할 학생을 명단 후보에서 선택하십시오. 변경하지 않았습니다.');return
        rows=list(self.visible_student_indices);before=list(rows)
        selected_rows={rows[i] for i in chosen}
        for i in sorted(chosen,reverse=delta>0):
            j=i+delta
            if 0<=j<len(rows) and j not in chosen:
                rows[i],rows[j]=rows[j],rows[i];chosen.remove(i);chosen.add(j)
        if rows!=before:
            visible=set(before);slots=[i for i,row in enumerate(self.candidate_order) if row in visible]
            for slot,row in zip(slots,rows):self.candidate_order[slot]=row
            self.filter_candidates()
            for i,row in enumerate(self.visible_student_indices):
                if row in selected_rows:self.available.selection_set(i)
            self.available.see(min(chosen));self.update_counts()
            self.status.set('후보 순서를 조정했습니다. 새로 추가할 때 이 순서를 사용합니다. 이미 추가된 처리 대상의 순서와 원본 명단은 바꾸지 않습니다.')
    def render_candidates(self, preserve_selection=True):
        chosen = self.available.curselection() if preserve_selection else ()
        self.available.delete(0, 'end')
        for row,(ident, display, _) in zip(self.visible_student_indices,self.visible_records):
            state = '후보 제외' if row in self.candidate_excluded else '처리 대상' if ident and ident in self.selected else ('이번 작업 제외' if ident in self.excluded_ids else '미포함')
            self.available.insert('end', f'[{state}] {display}')
        for i in chosen:
            if i < len(self.visible_records):self.available.selection_set(i)
    def load_pdf(self, path=None):
        if self.busy:return
        path = path or filedialog.askopenfilename(filetypes=[('PDF','*.pdf')])
        if not path:return
        def load():
            with fitz.open(path) as doc:
                if doc.needs_pass or not doc.is_pdf or not doc.page_count:raise ValueError('읽을 수 있는 비암호화 PDF가 필요합니다.')
                self.count = doc.page_count
            self.invalidate_previews()
            self.pdf = path; self.pdf_hash = digest(path); self.rules=PageRules(.18);self.cut=.18;self.mask_selection=None;self.drag_state=None
            info=detect_dpi(path);self.dpi.set(str(info['selected']));self.dpi_info.set(info['reason']+' · 출력 DPI를 높여도 원본 필기 정보가 추가되지는 않습니다.')
            self.page.set(1); self.spin.configure(to=self.count); self.refresh(); self.show_page()
            return True
        return self.guarded(load)
    def refresh(self):
        self.invalidate_previews()
        self.order.delete(0,'end')
        for i, ident in enumerate(self.selected):
            original = source_page(i,self.count,self.direction.get() == '전체 페이지 역순')+1 if i < getattr(self,'count',0) else '?'
            self.order.insert('end', f'{i+1}: {ident} ← 원본 {original}페이지')
        self.reviewed.clear()
        self.render_candidates()
        self.update_counts()
        self.status.set(f'PDF {getattr(self,"count",0)}페이지 / 선택 ID {len(self.selected)}개 / 절단 {self.cut:.2%}')
    def include(self):
        if self.busy:return
        if self.show_excluded_candidates.get():
            self.status.set('제외 후보를 먼저 복구한 뒤 처리 대상에 추가하십시오.');return
        if any(not self.visible_records[i][0] for i in self.available.curselection()):
            self.notice('showerror','코드 미발급', '미발급 행이 포함되어 있습니다. 빈 코드 발급 후 다시 선택하십시오.');return
        changed = False
        for i in self.available.curselection():
            ident = self.visible_records[i][0]
            if ident not in self.selected:
                self.selected.append(ident); changed = True
                self.excluded_ids.discard(ident)
        if changed:self.refresh()
        elif not self.available.curselection():self.status.set('추가할 학생을 위 후보 목록에서 선택하십시오. 변경하지 않았습니다.')
    def select_all(self, event):
        event.widget.selection_set(0, 'end')
        self.update_counts()
        return 'break'
    def update_counts(self):
        self.selection_status.set(f'PDF {getattr(self,"count",0)}페이지 / 후보 선택 {len(self.available.curselection())}명 / 대상 {len(self.selected)}명 중 선택 {len(self.order.curselection())}명 / 대상 제외 {len(self.excluded_ids)}명 / 후보 제외 {len(self.candidate_excluded)}명')
        self.update_controls()
    def confirm_candidate_ids(self):
        """Never silently infer mapping from highlight or replace a custom order."""
        if self.selected or not self.available.curselection():
            return True
        count = len(self.available.curselection())
        if not self.notice('askyesno','실제 처리 ID 목록이 비어 있습니다',
                f'파란색 {count}개는 명렬표 후보 선택입니다. 아직 실제 처리 목록에 포함되지 않았습니다.\n'
                '선택한 후보를 명렬표 순서대로 실제 처리 ID 목록에 포함할까요?\n'
                '포함 후 PDF 페이지 순서를 대조하고 첫 페이지와 표본 결과를 확인하십시오.'):
            return False
        self.include()
        return True
    def exclude(self):
        if self.busy:return
        chosen = self.order.curselection()
        if chosen:
            for i in reversed(chosen):self.excluded_ids.add(self.selected.pop(i))
            self.refresh()
            self.status.set('선택 학생을 이번 PDF 대상에서만 제외했습니다. 후보에서 다시 추가할 수 있습니다. 원본·코드는 보존됩니다.')
        else:self.status.set('제외할 학생을 아래 처리 대상 목록에서 선택하십시오. 변경하지 않았습니다.')
    def move(self, delta):
        if self.busy:return
        chosen=set(self.order.curselection())
        if not chosen:
            self.status.set('이동할 학생을 아래 처리 대상 목록에서 선택하십시오. 변경하지 않았습니다.');return
        if delta not in (-1, 1):return
        original = list(self.selected)
        for i in sorted(chosen, reverse=delta > 0):
            j=i+delta
            if 0<=j<len(self.selected) and j not in chosen:
                self.selected[i],self.selected[j]=self.selected[j],self.selected[i]
                chosen.remove(i); chosen.add(j)
        if self.selected != original:
            self.refresh()
            for i in sorted(chosen):self.order.selection_set(i)
            self.order.see(min(chosen))
            self.update_counts()
            self.status.set('선택 학생의 상대 순서를 유지해 이동했습니다. 바뀐 페이지-학생 대응과 표본을 다시 확인하십시오.')
    def zero(self):
        if self.busy:return
        p=self.current_original()
        if p in self.rules.cuts:self.rules.cuts[p]=0
        else:self.rules.cut=0;self.cut=0
        self.rules_changed()
    def legacy_show_page(self):
        if not self.pdf:return
        self.update_counts()
        try:
            n=self.page.get()-1
            with fitz.open(self.pdf) as doc:
                if not 0<=n<len(doc):
                    self.status.set(f'페이지는 1~{len(doc)} 범위에서 입력하십시오.');return
                original=source_page(n,len(doc),self.direction.get() == '전체 페이지 역순')
                pix=doc[original].get_pixmap(matrix=fitz.Matrix(1,1), annots=False)
                im=Image.open(io.BytesIO(pix.tobytes('png')))
            im.thumbnail((max(50,self.canvas.winfo_width()-20),max(50,self.canvas.winfo_height()-20)))
            w,h=im.size; x=(self.canvas.winfo_width()-w)/2; y=10
            self.photo=ImageTk.PhotoImage(im);self.canvas.delete('all'); self.canvas.create_image(x,y,anchor='nw',image=self.photo)
            self.preview_rect=(x,y,w,h)
            self.canvas.create_line(x,y+h*self.cut,x+w,y+h*self.cut,fill='red',width=3)
            self.page_info.set(f'처리 {n+1}/{self.count} · 원본 {original+1} · 절단 {self.cut:.2%}')
        except Exception as e:self.status.set(f'미리보기 오류: {type(e).__name__}')
    def legacy_click(self,event):
        if self.busy:return
        if self.preview_rect:
            x,y,w,h=self.preview_rect
            if x<=event.x<=x+w and y<=event.y<y+h:
                self.cut=(event.y-y)/h;self.reviewed.clear();self.invalidate_previews();self.show_page()
    def preview(self):
        if self.busy:return
        def render():
            if not self.pdf:raise ValueError('PDF를 선택하십시오.')
            if not self.confirm_candidate_ids():return
            n=self.page.get()-1
            if not 0<=n<self.count:raise ValueError('미리 볼 페이지 번호를 확인하십시오.')
            top=tk.Toplevel(self.root);top.title('무명호패 · 절단 결과 미리보기')
            top.geometry('900x870');top.minsize(650,580);top.transient(self.root)
            self.preview_windows.append(top)
            token=self.review_token()
            top.preview_position=tk.IntVar(value=n)
            top.preview_identity=tk.StringVar()
            top.preview_code=tk.StringVar()
            top.preview_progress=tk.StringVar()
            top.preview_hint=tk.StringVar()
            top.preview_image=None
            top.preview_identified=False
            identity=ttk.Frame(top,padding=(16,14));identity.pack(fill='x')
            person_label=ttk.Label(identity,textvariable=top.preview_identity,font=('맑은 고딕',12,'bold'),wraplength=850)
            person_label.pack(fill='x')
            code_label=ttk.Label(identity,textvariable=top.preview_code,style='Muted.TLabel',wraplength=850)
            code_label.pack(fill='x',pady=(6,0))
            footer=ttk.Frame(top,padding=10);footer.pack(side='bottom',fill='x')
            ttk.Label(footer,text='학생정보는 교사용 화면 전용 · 출력 PDF에는 코드만 표시',wraplength=600).pack(fill='x')
            hint_label=ttk.Label(footer,textvariable=top.preview_hint,style='Status.TLabel',wraplength=850)
            hint_label.pack(fill='x',pady=(3,7))
            navigation=ttk.Frame(footer);navigation.pack(fill='x')
            top.preview_prev=ttk.Button(navigation,text='◀ 이전',command=lambda:show_page(top.preview_position.get()-1))
            top.preview_prev.pack(side='left')
            top.preview_next=ttk.Button(navigation,text='다음 ▶',command=lambda:show_page(top.preview_position.get()+1))
            top.preview_next.pack(side='right')
            ttk.Label(navigation,textvariable=top.preview_progress).pack(side='left',expand=True)
            def confirm():
                if not self.busy and token==self.review_token() and top.preview_identified:
                    self.reviewed.add(top.preview_position.get())
                    self.update_controls();update_confirmation()
            top.preview_confirm=ttk.Button(footer,text='이 페이지 확인',command=confirm,style='Accent.TButton')
            top.preview_confirm.pack(fill='x',pady=(8,0))
            canvas=tk.Canvas(top,bg='#E9EDF2',highlightthickness=0);canvas.pack(fill='both',expand=True,padx=10,pady=(0,4))
            def draw(event=None):
                canvas.delete('all')
                if top.preview_image is not None:
                    im=top.preview_image.copy();im.thumbnail((max(30,canvas.winfo_width()-12),max(30,canvas.winfo_height()-12)))
                    top.photo=ImageTk.PhotoImage(im)
                    canvas.create_image(canvas.winfo_width()/2,canvas.winfo_height()/2,image=top.photo)
                if event is not None:
                    width=max(300,top.winfo_width()-32)
                    person_label.configure(wraplength=width);code_label.configure(wraplength=width);hint_label.configure(wraplength=width)
            canvas.bind('<Configure>',draw)
            def update_confirmation():
                current=top.preview_position.get()
                checked=current in self.reviewed
                ready=0 in self.reviewed and (self.count==1 or any(i>0 for i in self.reviewed))
                top.preview_confirm.configure(state='normal' if top.preview_identified else 'disabled',text='확인 완료 · 다시 눌러도 중복 계산하지 않습니다' if checked else '이 페이지의 절단·학생 대응 확인')
                if not top.preview_identified:hint='학생정보 또는 페이지 대응을 확인할 수 없습니다. 명단과 처리 대상을 먼저 확인하십시오.'
                elif ready:hint=f'표본 확인 {1 if self.count==1 else 2}/{1 if self.count==1 else 2} 완료 · 창을 닫고 PDF를 생성할 수 있습니다.'
                elif self.count==1:hint='1페이지 문서입니다. 이 페이지의 절단·학생 대응을 확인해야 출력할 수 있습니다.'
                else:
                    completed=int(0 in self.reviewed)+int(any(i>0 for i in self.reviewed))
                    remaining='첫 페이지' if 0 not in self.reviewed else '다른 표본 페이지'
                    hint=f'표본 확인 {completed}/2 완료 · 다음: {remaining} 확인. 페이지 이동만으로는 확인되지 않습니다.'
                top.preview_hint.set(hint)
            def show_page(position):
                if self.busy or token!=self.review_token():
                    top.destroy();return
                if not 0<=position<self.count:return
                top.preview_position.set(position);top.preview_image=None;top.preview_identified=False;canvas.delete('all')
                ident=self.selected[position] if position<len(self.selected) else 'PREVIEW'
                matches=[r for r in self.students if r['code'] and r['code']==ident]
                student=matches[0] if len(matches)==1 else None
                top.preview_identity.set(f"{student['class']}  {student['number']}번  {student['name']}" if student else '학생정보 확인 불가 · 학생 미지정/대응 확인 필요')
                top.preview_code.set('비식별코드: '+ident)
                original=source_page(position,self.count,self.direction.get()=='전체 페이지 역순')
                top.preview_progress.set(f'{position+1} / {self.count}페이지 · 원본 {original+1}페이지')
                top.preview_prev.configure(state='normal' if position>0 else 'disabled')
                top.preview_next.configure(state='normal' if position+1<self.count else 'disabled')
                try:
                    with fitz.open(self.pdf) as doc:
                        page_cut,masks=self.rules.for_page(original)
                        metadata=page_identity(ident,student['class'],int(self.page_order.get())) if student else None
                        data=make_pdf(doc[original],ident,page_cut,int(self.dpi.get()),masks,metadata)
                    with fitz.open(stream=data,filetype='pdf') as doc:
                        pix=doc[0].get_pixmap();top.preview_image=Image.open(io.BytesIO(pix.tobytes('png'))).copy()
                    top.preview_identified=student is not None
                    self.page.set(position+1);self.show_page();draw()
                except Exception as exc:
                    self.notice('showerror','미리보기 중단',str(exc),parent=top)
                update_confirmation()
            self.place_dialog(top)
            show_page(n)
        self.guarded(render)
    def run(self):
        if self.busy:return
        def start():
            if not self.pdf or not self.roster:raise ValueError('PDF와 XLSX를 모두 선택하십시오.')
            if self.code_dirty:raise ValueError('발급한 코드가 미저장 상태입니다. 대조표를 새 XLSX로 저장한 뒤 실행하십시오.')
            if digest(self.pdf)!=self.pdf_hash or digest(self.roster)!=self.roster_hash:
                raise ValueError('선택 후 입력 파일이 변경되었습니다. 다시 선택하고 검수하십시오.')
            if not self.confirm_candidate_ids():return
            if not self.selected:
                raise ValueError('실제 처리 ID가 0개입니다. 명렬표 후보를 선택하고 「선택 → 포함」을 누른 뒤 아래 처리 순서를 확인하십시오.')
            validate_ids(self.selected,self.count)
            if 0 not in self.reviewed or (self.count>1 and not any(n>0 for n in self.reviewed)):
                raise ValueError('현재 절단·마스킹 규칙으로 첫 페이지와 다른 표본 페이지의 결과를 확인하십시오.')
            folder=filedialog.askdirectory(title='입력과 분리된 로컬 출력 폴더 (동기화 폴더 금지)')
            if not folder:return
            bundle = self.output_mode.get() == 'bundle'
            paths, collisions=plan_names(['비식별_묶음'] if bundle else self.selected,folder)
            if collisions and not self.notice('askyesno','파일명 충돌', f'{len(collisions)}개 파일명에 기존 파일이 있습니다. 다음 빈 순번 접미사로 저장합니까? 기존 파일은 보존합니다.') :return
            description = f'{self.count}페이지를 묶은 PDF 1개 ({paths[0].name})' if bundle else f'학생코드별 PDF {self.count}개'
            if not self.notice('askyesno','최종 확인',f'{description}를 생성합니다. 선택 ID 순서와 PDF 순서를 대조했고 개인정보 영역 및 본문을 확인했습니까? 출력은 로컬 비동기화 폴더입니까?'):return
            self.output_folders.add(str(Path(folder).resolve()))
            args=(self.pdf,list(self.selected),folder,self.cut,int(self.dpi.get()),bool(collisions),self.roster)
            reverse=self.direction.get() == '전체 페이지 역순'
            rule_snapshot=self.rules.snapshot()
            page_metadata=[page_identity(code,next(s['class'] for s in self.students if s['code']==code),int(self.page_order.get())) for code in self.selected]
            self.set_busy(True)
            def worker():
                try:
                    exporter = export_bundle if bundle else export_batch
                    result,log,_=exporter(*args,reverse=reverse,rules=rule_snapshot,page_metadata=page_metadata)
                    ok=sum(r[3]=='ok' for r in result)
                    detail='\n'.join(f'{r[0]}페이지 {r[1]} {r[3]}' for r in result if r[3]!='ok')
                    files = len({r[2] for r in result if r[3] == 'ok'})
                    msg=f'페이지 성공 {ok} / 실패 {len(result)-ok} / 누락 {self.count-len(result)}\n생성 PDF {files}개\n기록: {log}\n{detail}'
                    self.root.after(0,lambda m=msg:self.notice('showinfo','처리 결과',m))
                except Exception as e:self.root.after(0,lambda m=str(e):self.notice('showerror','처리 오류',m))
                finally:self.root.after(0,lambda:self.set_busy(False))
            threading.Thread(target=worker,daemon=False).start()
        self.guarded(start)

    def close_app(self):
        if self.busy or any(review.busy for review in getattr(self,'page_reviews',[])):
            self.notice('showinfo','저장 중','저장이 끝난 뒤 프로그램을 닫으십시오.');return
        for dialog in getattr(self,'batch_dialogs',[]):
            if dialog.top.winfo_exists() and not dialog.confirm_close():return
        self.root.destroy()

    def review_token(self):
        return (self.generation,self.pdf,tuple(self.selected),self.cut,self.dpi.get(),self.direction.get(),self.rules.token(),self.page_order.get())

    def invalidate_previews(self):
        self.generation += 1
        for window in self.preview_windows:
            if window.winfo_exists():window.destroy()
        self.preview_windows.clear()

    def mapping_changed(self):
        self.refresh()
        self.show_page()

    def set_busy(self, value):
        self.busy=value
        if value:self.invalidate_previews()
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child,(ttk.Button,ttk.Checkbutton,ttk.Radiobutton,ttk.Spinbox,ttk.Combobox,tk.Listbox)):
                    child.configure(state='disabled' if value else ('readonly' if isinstance(child,ttk.Combobox) else 'normal'))
                visit(child)
        visit(self.root)
        self.update_controls()

    def reset(self):
        if self.busy:
            self.notice('showinfo','처리 중', '처리가 끝난 뒤 새 작업을 시작하십시오.');return
        warning='현재 선택과 검수 상태를 비울까요?\n원본과 저장된 결과는 보존합니다.'
        if self.code_dirty:warning+='\n주의: 아직 저장하지 않은 신규 코드는 초기화됩니다.'
        if not self.notice('askyesno','새 작업 / 초기화',warning,parent=self.root):return
        self.invalidate_previews()
        self.rules=PageRules(.18);self.cut=.18;self.mask_selection=None;self.drag_state=None
        self.exception_combo.configure(values=[]);self.exception_choice.set('')
        self.pdf=self.roster=None
        self.pdf_hash=self.roster_hash=None
        self.records=[];self.visible_records=[];self.selected=[];self.count=0;self.reviewed.clear()
        self.students=[];self.visible_student_indices=[];self.code_dirty=False
        self.candidate_order=[];self.candidate_excluded.clear();self.show_excluded_candidates.set(False)
        self.excluded_ids.clear()
        self.code_status.set('기존 코드 우선 사용 · 미발급 행만 선택 발급 · 실명 대조표는 PDF 폴더와 분리')
        self.class_filter.set('전체 학급');self.class_combo.configure(values=['전체 학급'])
        self.preview_rect=None;self.photo=None;self.cut=0.18
        self.page_info.set('PDF를 불러오면 원본이 표시됩니다.')
        self.available.delete(0,'end');self.order.delete(0,'end');self.canvas.delete('all')
        self.page.set(1);self.spin.configure(to=1);self.dpi.set('300');self.direction.set('원래 순서')
        self.output_mode.set('individual');self.page_order.set('1')
        self.update_counts()
        self.status.set('새 작업: 로컬 XLSX와 PDF를 선택하십시오.')

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
