# 무명호패 Beta 0.0.1

Windows용 오프라인 PDF 비식별화 도구입니다. XLSX 명단의 학생코드와 PDF 페이지를 연결하고, 사용자가 지정한 상단 영역을 잘라 코드만 표시한 PDF를 생성합니다.

## 주요 기능

- 학급별 후보 필터, 후보 제외·복구·순서 조정
- 기존 학생코드 유지 및 빈 코드 발급, 대조표 XLSX 저장
- PDF·XLSX 파일 끌어 놓기
- 절단 결과 미리보기와 페이지·코드 개수 검증
- 학생코드별 개별 PDF 또는 하나의 묶음 PDF
- 기존 출력 파일 덮어쓰기 방지

본문 개인정보를 자동으로 찾아 지우지는 않습니다. 교사가 절단 영역과 페이지·학생코드 순서를 확인해야 합니다. 이름·학급은 교사용 화면에 표시되고 출력 PDF에는 코드만 표시합니다. 대조표는 PDF와 분리하여 보관하십시오.

## 실행

Windows 배포본은 폴더 전체를 복사한 뒤 `MHopae.exe`를 실행합니다. `_internal` 폴더가 함께 있어야 합니다. 실행에 별도 Python 설치나 인터넷 연결은 필요하지 않습니다.

소스 실행 및 빌드에는 Windows, Python 3.12와 requirements.txt의 패키지가 필요합니다.

```powershell
python -m pip install -r requirements.txt
python source/app.py
python -m PyInstaller --noconfirm --distpath program --workpath build MHopae.spec
```

소스 개발 환경에서 의존성을 설치할 때에는 인터넷이 필요할 수 있습니다. 프로그램 자체 동작과 구분합니다.

## 구성

- `source/`: 프로그램 소스
- `MHopae.spec`: Windows onedir 경량 빌드 설정
- `사용설명서.txt`: 교사용 사용 안내
- `test_student_codes.py`: 가상자료 기능·원본 보존·소켓 차단 회귀시험
- `docs/검증기록.md`: 검증 범위와 한계

```powershell
python test_student_codes.py validation_fresh
```

검증 출력 경로는 아직 존재하지 않는 새 이름을 사용하십시오.

## 경량화 및 검증

미사용 AVIF/WebP/색관리 플러그인과 선택적 lxml을 제외하여 배포 폴더를 약88.6MiB에서72.7MiB로 줄였습니다. 핵심 PDF 엔진과 Python/Tk 런타임은 유지했습니다.

실제 경량 EXE에서 가상 XLSX 읽기·쓰기와 PDF 미리보기·개별 PDF3개 생성을 확인했습니다. 소스 시험에서는39개 회귀 항목 및25페이지 개별·묶음 생성, 충돌 방지, 소켓 차단을 확인했습니다. 다른 물리 PC, 실제 Windows DPI 변경, Ctrl+마우스 비연속 선택의 수동 검증은 수행하지 않았습니다.

실제 학생자료·신원 대응표·인증정보·개발 중간 빌드는 저장소에 포함하지 않습니다. 학생코드도 신원과 연결될 수 있으므로 입력·출력 자료는 사용자가 로컬에서 관리하십시오.

## 라이선스

프로젝트 소스의 배포 라이선스는 소유자가 별도로 결정합니다. 제3자 라이브러리에는 각 라이선스가 적용되므로 실행파일 재배포 시 고지와 의무를 확인해야 합니다.
