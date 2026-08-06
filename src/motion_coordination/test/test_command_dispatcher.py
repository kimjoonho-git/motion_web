import threading
import time

from motion_coordination.command_dispatcher import CommandDispatcher


def _wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('dispatcher result timeout')


def test_dispatcher_preserves_fifo_order_for_normal_commands():
    handled = []
    dispatcher = CommandDispatcher(handled.append)
    dispatcher.submit('prepare')
    dispatcher.submit('initialize_at')
    dispatcher.submit('start_at')
    dispatcher.start()
    try:
        _wait_for(lambda: len(handled) == 3)
        assert handled == ['prepare', 'initialize_at', 'start_at']
    finally:
        dispatcher.close()


def test_active_stop_now_runs_while_normal_command_is_already_blocked():
    handled = []
    start_entered = threading.Event()
    release_start = threading.Event()

    def handler(command):
        if command == 'start_at':
            start_entered.set()
            release_start.wait(1.0)
        handled.append(command)

    dispatcher = CommandDispatcher(handler)
    dispatcher.start()
    try:
        dispatcher.submit('start_at')
        assert start_entered.wait(0.5)
        dispatcher.submit('stop_now', urgent_stop=True)
        _wait_for(lambda: handled == ['stop_now'])
        release_start.set()
        _wait_for(lambda: handled == ['stop_now', 'start_at'])
    finally:
        release_start.set()
        dispatcher.close()


def test_dispatcher_never_runs_two_handlers_concurrently():
    active = 0
    maximum = 0
    lock = threading.Lock()

    def handler(_command):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1

    dispatcher = CommandDispatcher(handler)
    dispatcher.start()
    try:
        for index in range(5):
            dispatcher.submit(index)
        _wait_for(lambda: dispatcher._queue.unfinished_tasks == 0)
        assert maximum == 1
    finally:
        dispatcher.close()
