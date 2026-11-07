"""Actual compiled workspace images/Seatbelt; fixed model protocol, no LLM score."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tool_control import ToolCancelled
from agent_lab.tools import ToolError
from tests.native_fixture import compile_c
from tests.support import ScriptedModel,tool,final

HELLO='#include <stdio.h>\nint main(void){puts("owned native program");return 0;}\n'


class WorkspaceExecNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-workspace-native-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()

    def test_actual_compiled_image_requires_opt_in_approval_and_survives_readonly_root(self):
        program,metadata=compile_c(self.root,'program',HELLO)
        with self.assertRaises(ToolError):CodingTools(self.root).prepare('run_command',{'argv':['./program'],'cwd':'.'})
        tools=CodingTools(self.root,allow_workspace_executables=True,read_only_paths=['.'])
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,tools,ScriptedModel([tool('run_command',{'argv':['./program'],'cwd':'.'},'native'),final('done')]))
            task=engine.create('Run owned compiled fixture',Limits(tool_timeout=6))
            self.assertEqual(engine.run(task)['phase'],'awaiting_approval')
            request=engine.approval_request(task);engine.decide_approval(task,request['request_id'],request['digest'],True)
            state=engine.run(task);self.assertEqual(state['phase'],'completed',state.get('reason'))
            result=json.loads(next(m['content'] for m in state['messages'] if m['role']=='tool'))
            self.assertEqual((result['exit_code'],result['stdout']),(0,'owned native program\n'))
            self.assertEqual(native_jobs.inspect_workspace(self.root,self.base/'state.sqlite')['proof_kind'],'all_native_joined')
        self.assertFalse(metadata['agent_compilation'])

    def test_native_program_private_readonly_network_hidden_and_nofork_boundaries(self):
        private=self.base/'private.txt';private.write_text('owned private sentinel')
        (self.root/'protected.txt').write_text('original')
        hidden,_=compile_c(self.root,'.hidden-program','#include <stdio.h>\nint main(void){puts("hidden executed");return 91;}\n')
        body=r'''#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
int main(int argc,char **argv){
 FILE *p=fopen(argv[1],"r");int priv=p?0:errno;if(p)fclose(p);
 p=fopen("protected.txt","w");int ro=p?0:errno;if(p)fclose(p);
 int s=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in address={0};
 address.sin_family=AF_INET;address.sin_port=htons(9);address.sin_addr.s_addr=htonl(0x7f000001);
 int n=connect(s,(struct sockaddr *)&address,sizeof(address));int net=n==0?0:errno;close(s);
 errno=0;pid_t a=fork();if(a==0)_exit(0);int fe=a<0?errno:0;if(a>0)waitpid(a,0,0);
 errno=0;pid_t b=vfork();if(b==0)_exit(0);int ve=b<0?errno:0;if(b>0)waitpid(b,0,0);
 execl("./.hidden-program","./.hidden-program",(char *)0);int hidden=errno;
 printf("{\"private\":%d,\"readonly\":%d,\"network\":%d,\"fork\":%d,\"vfork_api\":%d,\"hidden_exec\":%d,\"api_env\":%d}\n",
        priv,ro,net,fe,ve,hidden,getenv("DEEPSEEK_API_KEY")!=0);return 0;
}
'''
        program,_=compile_c(self.root,'boundaries',body)
        tools=CodingTools(self.root,allow_workspace_executables=True,read_only_paths=['protected.txt'])
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-only'}):
            result=tools.execute(tools.prepare('run_command',{'argv':['./boundaries',str(private)],'cwd':'.'}),6)
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(json.loads(result['stdout']),{'private':1,'readonly':1,'network':1,'fork':1,'vfork_api':1,'hidden_exec':1,'api_env':0})
        self.assertEqual((self.root/'protected.txt').read_text(),'original')

    def test_secondary_visible_programs_are_scope_granted_not_individually_pinned(self):
        compile_c(self.root,'child',HELLO)
        compile_c(self.root,'launcher','#include <unistd.h>\nint main(void){execl("./child","./child",(char*)0);return 92;}\n')
        tools=CodingTools(self.root,allow_workspace_executables=True)
        intent=tools.prepare('run_command',{'argv':['./launcher'],'cwd':'.'})
        self.assertEqual(intent['workspace_executable']['path'],'launcher')
        result=tools.execute(intent,6)
        self.assertEqual((result['exit_code'],result['stdout']),(0,'owned native program\n'))

    def test_confined_unittest_can_spawn_workspace_compiled_program(self):
        compile_c(self.root,'program',HELLO)
        (self.root/'test_program.py').write_text('''import subprocess,unittest
class Check(unittest.TestCase):
 def test_program(self):
  result=subprocess.run(['./program'],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
  self.assertEqual(result.stdout,'owned native program\\n')
''')
        tools=CodingTools(self.root,allow_workspace_executables=True,command_backend='confined-fork')
        result=tools.execute(tools.prepare('run_tests',{}),6)
        self.assertEqual(result['exit_code'],0,result)

    def test_cancel_drains_actual_native_fork_descendant_before_return(self):
        code=r'''#include <stdio.h>
#include <unistd.h>
int main(void){pid_t child=fork();if(child<0)return 9;
 if(child>0){FILE *f=fopen("ready","w");fputs("owned",f);fclose(f);}
 sleep(2);FILE *f=fopen(child>0?"late-parent":"late-child","w");if(f){fputs("unexpected",f);fclose(f);}return 0;}
'''
        compile_c(self.root,'waiter',code)
        tools=CodingTools(self.root,allow_workspace_executables=True,command_backend='confined-fork')
        intent=tools.prepare('run_command',{'argv':['./waiter'],'cwd':'.'})
        with tools.cancellation_scope(lambda:(self.root/'ready').exists()):
            with self.assertRaises(ToolCancelled) as caught:tools.execute(intent,6)
        self.assertTrue(caught.exception.local_quiescent)
        self.assertFalse(list(self.root.glob('late-*')))


if __name__=='__main__':unittest.main()
