# 무명호패 0.1.0

Windows에서 로컬 PDF의 개인정보 영역을 잘라내거나 가리고, 학생코드·학급·페이지순번으로 답안 PDF를 정리하는 오프라인 도구입니다.

## 다운로드·실행

[0.1.0 Windows 배포본](https://github.com/sukhavati44/Mhopae/releases/tag/v0.1.0)에서 `MHopae-0.1.0-windows-x64.zip`을 받으십시오. ZIP 전체를 풀고 `MHopae/MHopae.exe`를 실행합니다. `_internal` 폴더를 포함한 프로그램 폴더 전체를 함께 보관·복사해야 합니다. Python 설치나 PowerShell 스크립트 실행은 필요하지 않습니다.

## 주요 기능

- PDF·XLSX 선택 및 창으로 끌어 놓기, 학급별 명단 후보 필터·제외·복구·순서 조정
- 기존 학생코드를 유지하고 빈 코드 발급, 한글·하이픈 등 Windows 파일명에서 허용되는 문자 지원
- 절단·마스크의 공통 규칙과 페이지별 예외, 결과 미리보기, 300/400 DPI 출력
- 학생코드별 개별 또는 묶음 출력, 같은 이름은 다음 빈 순번으로 저장
- 대량 스캔의 면별·학생별 대응, 구간별 정순/역순, 미정·중복을 남긴 채 가능한 페이지 처리
- 학생코드와 학급·페이지순번을 분리한 두 박스 및 PDF 내부 페이지 정보
- 학급별·학생별·페이지순번별·개별 재구성, 기존 비식별 파일의 독립 학생별 병합
- 로컬 작업 저장·이어하기, 입력 지문 확인, 선택 페이지만 보완 출력
- 메인 절단·마스크 규칙 비교 적용, 상태가 명확한 기존 버튼 하나의 은은한 시각 안내

본문 개인정보를 자동 판독하거나 OCR하지 않습니다. 사용자가 제거 범위와 학생 대응·페이지순서를 직접 확인해야 합니다. 재구성·병합은 이미 비식별화된 입력 전용이며 추가 개인정보 제거를 하지 않습니다. 작업 JSON에는 로컬 경로·코드 참조가 있으므로 외부 공유하지 마십시오.

자세한 조작은 [사용설명서](사용설명서.txt), 변경 사항은 [0.1.0 릴리스 기록](docs/releases/v0.1.0.md), 시험 범위는 [검증 기록](docs/검증기록.md)을 참고하십시오.

## 소스 실행·빌드

Windows x64 / Python 3.12와 Tcl/Tk를 사용합니다.

```powershell
python -m pip install -r requirements.txt
python source/app.py
python scripts/release_smoke.py
python -m PyInstaller --noconfirm --distpath dist --workpath build MHopae.spec
python scripts/package_release.py
```

배포 ZIP은 런타임·안내서·해당 빌드의 소스·구성요소 라이선스와 SHA-256 명세를 포함합니다. GitHub Actions가 정확한 커밋으로 빌드하며 `build-manifest.json`에 소스 해시·Python/라이브러리 버전·실행파일 해시를 기록합니다. 빌드 환경 차이로 로컬 EXE와 CI EXE의 바이트 해시는 다를 수 있습니다.

앱 실행·PDF 처리에는 인터넷이나 외부 API가 필요하지 않습니다. 개발용 의존성 설치와 GitHub 릴리스 게시에는 네트워크를 사용합니다. 라이브러리별 조건은 배포본의 `licenses`와 `THIRD_PARTY_NOTICES.txt`에 보존합니다.
