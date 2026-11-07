import subprocess
import tempfile
import unittest
from pathlib import Path
from SmokeBot.auto_update import get_git_update_status, apply_git_update

def git(path,*args):
    return subprocess.run(['git',*args],cwd=path,check=True,capture_output=True,text=True).stdout.strip()

class GitHistoryTests(unittest.TestCase):
    def test_equal_ahead_behind_diverged_and_noop_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);remote=root/'remote.git';work=root/'work';peer=root/'peer'
            git(root,'init','--bare',str(remote));git(root,'clone',str(remote),str(work))
            for path in (work,):
                git(path,'config','user.email','synthetic@example.invalid');git(path,'config','user.name','Synthetic')
            git(work,'checkout','-b','main');(work/'base').write_text('base');git(work,'add','.');git(work,'commit','-m','base');git(work,'push','-u','origin','main')
            git(root,'clone','-b','main',str(remote),str(peer));git(peer,'config','user.email','synthetic@example.invalid');git(peer,'config','user.name','Synthetic')
            equal=get_git_update_status(str(work));self.assertTrue(equal['up_to_date'])
            (work/'local').write_text('local');git(work,'add','.');git(work,'commit','-m','local')
            ahead=get_git_update_status(str(work));self.assertTrue(ahead['up_to_date'],'A local-only commit is not a remote update');self.assertEqual(ahead['relationship'],'ahead')
            self.assertFalse(apply_git_update(str(work))['changed'])
            (peer/'remote').write_text('remote');git(peer,'add','.');git(peer,'commit','-m','remote');git(peer,'push','origin','main')
            diverged=get_git_update_status(str(work));self.assertEqual(diverged['relationship'],'diverged');self.assertFalse(diverged['can_fast_forward'])
            git(work,'reset','--hard','HEAD~1')
            behind=get_git_update_status(str(work));self.assertEqual(behind['relationship'],'behind');self.assertTrue(behind['can_fast_forward'])
            update=apply_git_update(str(work));self.assertTrue(update['ok']);self.assertTrue(update['changed'])
            self.assertTrue(get_git_update_status(str(work))['up_to_date'])
