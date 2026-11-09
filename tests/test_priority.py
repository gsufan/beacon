"""Questions go first on the model server: documentation waits while one is answered, one project at a time."""

import threading
import time

from core.engine.priority import ModelPriority


def test_documentation_waits_while_a_question_is_answered():
    p = ModelPriority()
    released = threading.Event()
    done = []

    def question():
        with p.query():
            released.wait(2)

    t = threading.Thread(target=question)
    t.start()
    time.sleep(0.05)
    waiter = threading.Thread(target=lambda: done.append(p.wait_for_queries(timeout=2)))
    waiter.start()
    time.sleep(0.1)
    assert not done  # still waiting
    released.set()
    waiter.join(2)
    t.join(2)
    assert done == [True] and p.active_queries == 0


def test_wait_gives_up_after_timeout():
    p = ModelPriority()
    with p.query():
        assert p.wait_for_queries(timeout=0.05) is False


def test_one_project_documented_at_a_time():
    p = ModelPriority()
    inside, overlap = [0], [False]

    def document():
        with p.documentation():
            inside[0] += 1
            overlap[0] = overlap[0] or inside[0] > 1
            time.sleep(0.05)
            inside[0] -= 1

    threads = [threading.Thread(target=document) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)
    assert not overlap[0]


def test_the_engine_marks_the_question_as_active(monkeypatch):
    from core.engine import rag_engine
    seen = []

    class Engine(rag_engine.RAGEngine):
        def __init__(self):
            pass

        def _ask(self, *args):
            seen.append(rag_engine.priority.active_queries)
            return "ok"

    assert Engine().ask("¿qué hace esto?") == "ok"
    assert seen == [1] and rag_engine.priority.active_queries == 0
