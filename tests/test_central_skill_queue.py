from src.apps.platform.central_queue import IsolatedSkillWorker, QueueJob


class FakeQueue:
    def __init__(self): self.items = []; self.acked = []; self.requeued = []
    def enqueue(self, job): self.items.append(job); return job.job_id
    def claim(self, worker_id, timeout=5): return self.items.pop(0) if self.items else None
    def ack(self, worker_id, job_id): self.acked.append((worker_id, job_id))
    def requeue(self, worker_id, job): self.requeued.append((worker_id, job.job_id)); self.items.append(job)


def test_isolated_worker_acknowledges_success():
    queue = FakeQueue(); queue.enqueue(QueueJob("j1", {"skill": "story-scout"}))
    seen = []
    worker = IsolatedSkillWorker(queue, worker_id="w1", handler=lambda job: seen.append(job.job_id))
    assert worker.run_once() is True
    assert seen == ["j1"] and queue.acked == [("w1", "j1")]


def test_isolated_worker_requeues_failure():
    queue = FakeQueue(); queue.enqueue(QueueJob("j1", {}))
    worker = IsolatedSkillWorker(queue, worker_id="w1", handler=lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    try: worker.run_once()
    except RuntimeError: pass
    else: raise AssertionError("handler failure must propagate")
    assert queue.requeued == [("w1", "j1")]
