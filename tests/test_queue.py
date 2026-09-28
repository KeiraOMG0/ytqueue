from ytqueue.models import Track
from ytqueue.queue import PlayQueue


def test_enqueue_and_pop_order():
    q = PlayQueue()
    q.enqueue(Track(url="a", title="A"))
    q.enqueue(Track(url="b", title="B"))
    assert q.pop_next().title == "A"
    assert q.pop_next().title == "B"
    assert q.pop_next() is None


def test_loop_requeues_current_on_finish():
    q = PlayQueue()
    q.loop_enabled = True
    q.set_current(Track(url="a", title="A"))
    q.finish_current()
    assert q.previous.title == "A"
    assert list(q.tracks)[0].title == "A"


def test_no_loop_does_not_requeue():
    q = PlayQueue()
    q.set_current(Track(url="a", title="A"))
    q.finish_current()
    assert len(q.tracks) == 0
    assert q.previous.title == "A"
