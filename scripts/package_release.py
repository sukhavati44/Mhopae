"""Allowlisted release package. No input, local session, or work logs are shipped."""
from pathlib import Path
import hashlib, importlib.metadata as md, json, os, platform, shutil, sys, zipfile
ROOT=Path(__file__).resolve().parents[1]
program=ROOT/'dist/MHopae';release=ROOT/'release';release.mkdir(exist_ok=True)
assert (program/'MHopae.exe').is_file() and (program/'_internal').is_dir()
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
shutil.copyfile(ROOT/'사용설명서.txt',program/'사용설명서.txt')
shutil.copytree(ROOT/'source',program/'source',ignore=shutil.ignore_patterns('__pycache__','*.pyc','vendor'),dirs_exist_ok=True)
shutil.copyfile(ROOT/'requirements.txt',program/'requirements.txt')
shutil.copyfile(ROOT/'MHopae.spec',program/'MHopae.spec')
licenses=program/'licenses';licenses.mkdir(exist_ok=True)
versions={name:md.version(name) for name in ['PyMuPDF','Pillow','openpyxl','et_xmlfile','PyInstaller']}
for name in versions:
    distribution=md.distribution(name)
    for entry in distribution.files or []:
        if any(token in entry.name.lower() for token in ('license','copying','notice')):
            original=Path(distribution.locate_file(entry))
            if original.is_file():
                target=licenses/name/str(entry).replace('..','_')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.is_file():shutil.copyfile(python_license,licenses/'CPython-LICENSE.txt')
for file in (program/'_internal').rglob('*'):
    if file.is_file() and file.name.lower() in ('license.terms','license.txt','license'):
        target=licenses/'runtime'/file.relative_to(program/'_internal');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
notice='Bundled runtime: CPython '+platform.python_version()+', Tcl/Tk, '+', '.join(n+' '+v for n,v in versions.items())+'.\nOriginal component notices are preserved in licenses/ and _internal/.\nApplication source for this exact build is in source/ and the linked repository commit.\nUpstream component license terms remain applicable.\n'
(program/'THIRD_PARTY_NOTICES.txt').write_text(notice,encoding='utf-8')
source_hashes={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sorted((ROOT/'source').glob('*.py'))}
manifest={'version':'0.1.0','commit':os.environ.get('GITHUB_SHA','local'),'python':platform.python_version(),'packages':versions,'source_sha256':source_hashes,'exe_sha256':sha(program/'MHopae.exe')}
(program/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
archive=release/'MHopae-0.1.0-windows-x64.zip'
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for file in sorted(program.rglob('*')):
        if file.is_file():
            assert file.suffix.lower() not in ('.pdf','.xlsx','.mhopae')
            assert 'local_work' not in file.parts
            z.write(file,'MHopae/'+file.relative_to(program).as_posix())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert 'MHopae/MHopae.exe' in z.namelist() and 'MHopae/source/visual_guide.py' in z.namelist()
manifest['archive_sha256']=sha(archive);manifest['archive_bytes']=archive.stat().st_size
(release/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text(manifest['archive_sha256']+'  '+archive.name+'\n',encoding='utf-8')
print(json.dumps({'version':manifest['version'],'commit':manifest['commit'],'archive_bytes':manifest['archive_bytes'],'archive_sha256':manifest['archive_sha256']}))
