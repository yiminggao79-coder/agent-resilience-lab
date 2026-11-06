"""Host-only actor proof tracking; project processes inherit none of this state."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import uuid

if __package__:
    from . import native_jobs
else:
    import native_jobs

SUPERVISOR_ENV = 'AGENT_LAB_NATIVE_JOB'
_CURRENT = ContextVar('native_job_actor', default=None)


class JobTracker:
    def __init__(self, reference, worker_token, role):
        self.reference = reference
        self.worker_token = worker_token
        self.role = role
        self.supervisor_possible = False
        self.native_pending = False
        self.native_processes = set()

    def supervisor_begin(self):
        if self.role != 'worker' or self.supervisor_possible:
            raise native_jobs.JobStateError('native_job_state_unavailable')
        self.supervisor_possible = True

    def supervisor_failed(self):
        self.supervisor_possible = False

    def native_begin(self):
        if self.role != 'supervisor' or self.native_pending:
            raise native_jobs.JobStateError('native_job_state_unavailable')
        # Before Popen: a signal in the spawn/registration gap cannot attest idle.
        self.native_pending = True

    def native_started(self, pid):
        self.native_processes.add(pid)
        self.native_pending = False

    def native_failed(self):
        self.native_pending = False

    def native_joined(self, pid):
        self.native_processes.discard(pid)


def current_tracker():
    return _CURRENT.get()


def supervisor_payload():
    tracker = current_tracker()
    if tracker is None:
        return None
    return json.dumps({'reference': tracker.reference, 'worker_token': tracker.worker_token},
                      ensure_ascii=True, allow_nan=False, separators=(',', ':'))


@contextmanager
def worker_job(reference, root):
    if reference is None:
        yield None
        return
    reference = native_jobs.validate_reference(reference, root=root)
    worker_token = uuid.uuid4().hex
    # A second worker cannot claim zero-launch after the original producer died.
    native_jobs.register_worker(reference, worker_token)
    tracker = JobTracker(reference, worker_token, 'worker')
    token = _CURRENT.set(tracker)
    try:
        yield tracker
    finally:
        try:
            if not tracker.supervisor_possible:
                native_jobs.confirm_job(reference, 'no_supervisor_launch', worker_token=worker_token)
        finally:
            _CURRENT.reset(token)


@contextmanager
def supervisor_job(root):
    value = os.environ.pop(SUPERVISOR_ENV, None)
    if value is None:
        yield None
        return
    try:
        if len(value.encode('utf-8')) > 8192:
            raise ValueError()
        payload = json.loads(value)
        if not isinstance(payload, dict) or set(payload) != {'reference', 'worker_token'}:
            raise ValueError()
        reference = native_jobs.validate_reference(payload['reference'], root=Path(root))
        worker_token = payload['worker_token']
        # Same-token retry validates ownership; it does not register a new actor.
        native_jobs.register_worker(reference, worker_token)
    except (ValueError, TypeError, KeyError):
        raise native_jobs.JobStateError('native_job_state_unavailable') from None
    tracker = JobTracker(reference, worker_token, 'supervisor')
    token = _CURRENT.set(tracker)
    try:
        yield tracker
    finally:
        try:
            if not tracker.native_pending and not tracker.native_processes:
                native_jobs.confirm_job(reference, 'all_native_joined', worker_token=worker_token)
        finally:
            _CURRENT.reset(token)
