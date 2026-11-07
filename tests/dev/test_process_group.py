"""Read-only owned-group observation contracts; synthetic kernel replies."""
from dataclasses import replace
import os
from types import SimpleNamespace
import unittest

try:
    from agent_lab.process_group import OwnedGroup,ProcessInfo,GroupObservationError
except ImportError:
    OwnedGroup=ProcessInfo=GroupObservationError=None


class ProcessGroupTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(OwnedGroup,'owned-group observer not implemented')
        self.child=SimpleNamespace(pid=12345,returncode=None)
        self.info=ProcessInfo(12345,os.getpid(),12345,2,100,20)
        outer=self
        class Backend:
            default=True
            page=(12345,)
            calls=[]
            def wait_policy_ok(self):return self.default
            def process(self,pid):return outer.info
            def members(self,pid,capacity):
                self.calls.append(capacity);return self.page[:capacity]
        self.backend=Backend()
    def group(self,**kw):return OwnedGroup(self.child,backend=self.backend,**kw)
    def test_running_then_owned_zombie_singleton_is_narrow_predicate(self):
        group=self.group();self.assertFalse(group.anchor_only_zombie())
        self.info=replace(self.info,status=5)
        self.assertTrue(group.anchor_only_zombie())
        self.assertIsNone(self.child.returncode)
        self.assertEqual(group.signal_target(),self.child.pid)
    def test_other_members_even_zombies_are_not_accepted_as_finished(self):
        group=self.group();self.info=replace(self.info,status=5)
        self.backend.page=(12345,23456)
        self.assertFalse(group.anchor_only_zombie())
        self.backend.page=(12345,)
        self.assertTrue(group.anchor_only_zombie())
    def test_no_empty_error_duplicate_or_foreign_list_becomes_quiet(self):
        group=self.group();self.info=replace(self.info,status=5)
        for page in ((),(23456,),(12345,12345),(True,12345),(0,12345)):
            self.backend.page=page
            with self.subTest(page=page),self.assertRaises(GroupObservationError):group.anchor_only_zombie()
    def test_capacity_saturation_grows_and_stops_without_false_complete(self):
        group=self.group(initial_capacity=2,max_capacity=4)
        self.info=replace(self.info,status=5);self.backend.page=(12345,23456)
        self.assertFalse(group.anchor_only_zombie());self.assertEqual(self.backend.calls,[2,4])
        self.backend.page=(12345,23456,23457,23458)
        with self.assertRaisesRegex(GroupObservationError,'^group_capacity_limit$'):group.anchor_only_zombie()
    def test_changed_identity_parent_group_or_wait_state_forbids_target(self):
        for changes in ({'start_usec':21},{'ppid':1},{'pgid':23456},{'pid':23456}):
            self.info=ProcessInfo(12345,os.getpid(),12345,2,100,20);group=self.group()
            self.info=replace(self.info,**changes)
            with self.subTest(changes=changes),self.assertRaises(GroupObservationError):group.signal_target()
        self.info=ProcessInfo(12345,os.getpid(),12345,2,100,20);group=self.group()
        self.child.returncode=0
        with self.assertRaises(GroupObservationError):group.signal_target()
    def test_nondefault_wait_policy_and_initial_nonowned_group_are_refused(self):
        self.backend.default=False
        with self.assertRaises(GroupObservationError):self.group()
        self.backend.default=True;self.info=replace(self.info,pgid=1)
        with self.assertRaises(GroupObservationError):self.group()
    def test_no_group_access_after_reap_starts_even_if_wait_fails(self):
        group=self.group();group.mark_reap_started()
        for action in (group.anchor,group.members,group.anchor_only_zombie,group.signal_target):
            with self.subTest(action=action.__name__),self.assertRaises(GroupObservationError):action()
    def test_anchor_revalidated_after_members_query(self):
        group=self.group();self.info=replace(self.info,status=5)
        def changed(pid,capacity):
            self.info=replace(self.info,start_usec=999);return (12345,)
        self.backend.members=changed
        with self.assertRaises(GroupObservationError):group.anchor_only_zombie()


if __name__=='__main__':unittest.main()
