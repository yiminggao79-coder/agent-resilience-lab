"""Independent observer checks using synthetic native-API replies only."""
import ctypes
import errno
import os
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_lab import process_group as groups


class ProcessGroupReviewTests(unittest.TestCase):
    def tearDown(self):
        ctypes.set_errno(0)

    def native(self):
        backend=groups.DarwinProcesses.__new__(groups.DarwinProcesses)
        calls=[]

        def process(pid,flavor,arg,target,size):
            self.assertEqual((flavor,arg,size),(3,1,136))
            self.assertEqual(ctypes.get_errno(),0)
            calls.append(pid)
            info=ctypes.cast(target,ctypes.POINTER(groups._BSDInfo)).contents
            info.pid=pid; info.ppid=os.getpid(); info.pgid=pid; info.status=5
            info.start_sec=100; info.start_usec=20
            return size

        backend._lib=SimpleNamespace(proc_pidinfo=process)
        return backend,calls

    def test_capacity_retry_stops_before_query_when_reap_ownership_is_lost(self):
        for lost in ('marked','returncode'):
            child=SimpleNamespace(pid=12345,returncode=None); calls=[]

            class Backend:
                def wait_policy_ok(self): return True
                def process(self,pid): return groups.ProcessInfo(pid,os.getpid(),pid,5,100,20)
                def members(self,pid,capacity):
                    calls.append(capacity)
                    if len(calls)==1:
                        if lost=='marked': observer.mark_reap_started()
                        else: child.returncode=0
                        return (pid,23456)
                    return (pid,)

            observer=groups.OwnedGroup(child,backend=Backend(),initial_capacity=2,max_capacity=4)
            with self.subTest(lost=lost):
                with self.assertRaisesRegex(groups.GroupObservationError,'^group_anchor_lost$'):
                    observer.members()
                self.assertEqual(calls,[2],'group enumeration continued after ownership release')

    def test_native_abi_errno_zero_and_saturation_never_become_empty_proof(self):
        backend,_=self.native()
        ctypes.set_errno(errno.EPERM)
        self.assertEqual(backend.process(12345),groups.ProcessInfo(12345,os.getpid(),12345,5,100,20))
        for size,error in ((0,0),(0,errno.ESRCH),(135,0),(136,errno.EPERM)):
            def failed(*args):
                self.assertEqual(ctypes.get_errno(),0)
                ctypes.set_errno(error)
                return size
            with self.subTest(size=size,error=error),patch.object(backend._lib,'proc_pidinfo',failed):
                with self.assertRaises(groups.GroupObservationError): backend.process(12345)

        backend.wait_policy_ok=lambda:True
        for mode in ('zero','zero_errno','full','negative','over_capacity'):
            capacities=[]

            def members(pid,pids,byte_size):
                self.assertEqual(pid,12345)
                self.assertEqual(byte_size,len(pids)*ctypes.sizeof(ctypes.c_int))
                self.assertEqual(ctypes.get_errno(),0)
                capacities.append(len(pids))
                for index in range(len(pids)): pids[index]=12345+index
                if mode=='zero_errno': ctypes.set_errno(errno.EPERM)
                return {'zero':0,'zero_errno':0,'full':len(pids),'negative':-1,
                        'over_capacity':len(pids)+1}[mode]

            backend._lib.proc_listpgrppids=members
            observer=groups.OwnedGroup(SimpleNamespace(pid=12345,returncode=None),backend=backend,
                                       initial_capacity=2,max_capacity=4)
            with self.subTest(mode=mode),self.assertRaises(groups.GroupObservationError) as caught:
                observer.anchor_only_zombie()
            if mode=='full':
                self.assertEqual(caught.exception.code,'group_capacity_limit')
                self.assertEqual(capacities,[2,4])
            else: self.assertEqual(capacities,[2])

    def test_native_and_python_wait_policy_must_both_preserve_exclusive_wait(self):
        cases=[('default',None,0,0,0,signal.SIG_DFL,True),
               ('native_ignored',1,0,0,0,signal.SIG_DFL,False),
               ('native_nocldwait',None,0x20,0,0,signal.SIG_DFL,False),
               ('native_error',None,0,-1,errno.EPERM,signal.SIG_DFL,False),
               ('python_ignored',None,0,0,0,signal.SIG_IGN,False),
               ('python_handler',None,0,0,0,lambda *_:None,False)]
        for label,handler,flags,result,error,python_handler,allowed in cases:
            backend,calls=self.native()

            def sigaction(which,new,target):
                self.assertEqual(which,signal.SIGCHLD); self.assertIsNone(new)
                self.assertEqual(ctypes.get_errno(),0)
                action=ctypes.cast(target,ctypes.POINTER(groups._Sigaction)).contents
                action.handler=handler; action.flags=flags
                ctypes.set_errno(error)
                return result

            backend._libc=SimpleNamespace(sigaction=sigaction)
            with self.subTest(policy=label),patch.object(groups.signal,'getsignal',return_value=python_handler):
                if allowed:
                    groups.OwnedGroup(SimpleNamespace(pid=12345,returncode=None),backend=backend)
                    self.assertEqual(calls,[12345])
                else:
                    with self.assertRaisesRegex(groups.GroupObservationError,'^group_wait_not_exclusive$'):
                        groups.OwnedGroup(SimpleNamespace(pid=12345,returncode=None),backend=backend)
                    self.assertEqual(calls,[])
