import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

class UpdateStagingTests(unittest.TestCase):
    def run_failed_download(self,mode):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bot=root/'bot';bot.mkdir();fake=root/'bin';fake.mkdir()
            originals={name:'# original '+name+'\n' for name in ('main.py','storage.py','auto_update.py','chat_utils.py')}
            for name,value in originals.items():(bot/name).write_text(value)
            script=(Path(__file__).resolve().parents[1]/'SmokeBot/update_bot.sh').read_text()
            script=re.sub(r'^BOT_DIR=.*$', 'BOT_DIR="'+str(bot)+'"',script,flags=re.M)
            target=root/'update.sh';target.write_text(script)
            curl=f'''#!{sys.executable}
import pathlib,sys
out=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
if {mode!r}=='download' and out.name=='storage.py':sys.exit(22)
out.write_text('invalid python !!!' if {mode!r}=='syntax' else '# staged synthetic\\n')
'''
            (fake/'curl').write_text(curl)
            for name in ('pkill','nohup'):(fake/name).write_text('#!/bin/sh\necho attempted >> "'+str(root/'process-actions')+'"\nexit 0\n')
            if mode=='replace':
                (fake/'mv').write_text(f"#!{sys.executable}\nimport subprocess,sys\nif '/download/storage.py' in sys.argv[-2]:sys.exit(1)\nsys.exit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)\n")
            for p in fake.iterdir():p.chmod(0o755)
            result=subprocess.run(['bash',str(target)],env={**os.environ,'PATH':str(fake)+os.pathsep+os.environ['PATH']},capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            for name,value in originals.items():self.assertEqual((bot/name).read_text(),value,result.stdout+'\n'+result.stderr)
            self.assertEqual((root/'process-actions').exists(),mode=='startup','no process action before valid staging/replacement')
            self.assertFalse((bot/'pid.txt').exists())
    def test_download_failure_preserves_files_and_process(self):self.run_failed_download('download')
    def test_syntax_failure_preserves_files_and_process(self):self.run_failed_download('syntax')

    def test_replacement_failure_restores_all_original_files(self):self.run_failed_download('replace')
    def test_startup_failure_restores_all_original_files(self):self.run_failed_download('startup')
