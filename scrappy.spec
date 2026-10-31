# PyInstaller desktop bundle
from PyInstaller.utils.hooks import collect_all

datas = [('config.json', '.'), ('FIRECRAWL.md', '.')]
a = Analysis(['launcher.py'], datas=datas, hiddenimports=['run', 'keyring.backends.Windows'],
             excludes=['playwright', 'patchright'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name='Scrappy', console=False)
