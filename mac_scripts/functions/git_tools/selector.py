from __future__ import annotations

from dataclasses import dataclass, replace
import os
import signal
import sys
import termios
import threading
from types import FrameType
from typing import Callable, Iterable, Literal, TextIO

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.output import DummyOutput, Output, create_output

SelectorKind = Literal["continue", "submit", "cancel"]

@dataclass(frozen=True, slots=True)
class SelectorItem:
    label: str
    value: str

@dataclass(frozen=True, slots=True)
class SelectorState:
    items: tuple[SelectorItem, ...]
    cursor: int
    selected: frozenset[int]

@dataclass(frozen=True, slots=True)
class SelectorResult:
    kind: SelectorKind
    state: SelectorState
    selected: tuple[str, ...]

def create_selector_state(items: Iterable[SelectorItem], *, initial: Iterable[str] = ()) -> SelectorState:
    copied = tuple(items)
    initial_values = frozenset(initial)
    selected = frozenset(
        index for index, item in enumerate(copied) if item.value in initial_values
    )
    return SelectorState(copied, 0, selected)

def reduce_selector(state: SelectorState, key: str, *, multiple: bool) -> SelectorResult:
    cursor = state.cursor
    selected = set(state.selected)

    if key == "up":
        cursor = max(0, cursor - 1)
    elif key == "down":
        cursor = min(max(0, len(state.items) - 1), cursor + 1)
    elif key == "toggle" and multiple and state.items:
        if cursor in selected:
            selected.remove(cursor)
        else:
            selected.add(cursor)
    elif key == "select_all" and multiple:
        selected.update(range(len(state.items)))
    elif key == "clear" and multiple:
        selected.clear()

    next_state = replace(state, cursor=cursor, selected=frozenset(selected))
    if key == "cancel":
        return SelectorResult("cancel", next_state, ())
    if key == "submit":
        indexes = sorted(selected) if multiple else ([cursor] if state.items else [])
        values = tuple(next_state.items[index].value for index in indexes)
        return SelectorResult("submit", next_state, values)
    return SelectorResult("continue", next_state, ())

def _prompt_input(source: Input | TextIO | None) -> tuple[Input, bool]:
    if isinstance(source, Input):
        return source, False
    return create_input(stdin=source), True

def _prompt_output(destination: Output | TextIO | None) -> Output:
    stream = (
        getattr(destination, "stdout", None)
        if isinstance(destination, Output)
        else (sys.stdout if destination is None else destination)
    )
    try:
        is_tty = bool(stream is not None and stream.isatty())
    except (AttributeError, OSError):
        is_tty = False
    if not is_tty:
        return DummyOutput()
    if isinstance(destination, Output):
        return destination
    return create_output(stdout=destination)

def run_selector(
    items: Iterable[SelectorItem],
    *,
    initial: Iterable[str] = (),
    multiple: bool,
    input: Input | TextIO | None = None,
    output: Output | TextIO | None = None,
    render: Callable[[SelectorState], object],
) -> SelectorResult:
    state = create_selector_state(items, initial=initial)
    bindings = KeyBindings()
    prompt_input, owns_input = _prompt_input(input)
    prompt_output = _prompt_output(output)
    tty_fd: int | None = None
    original_attributes: list[object] | None = None
    try:
        tty_fd = prompt_input.fileno()
        original_attributes = termios.tcgetattr(tty_fd)
    except (AttributeError, OSError, termios.error):
        tty_fd = None

    def render_or_exit(app: Application[SelectorResult]) -> None:
        try:
            render(state)
        except BaseException as error:
            app.exit(exception=error)

    def apply(key: str, event: KeyPressEvent) -> None:
        nonlocal state
        result = reduce_selector(state, key, multiple=multiple)
        state = result.state
        if result.kind == "continue":
            render_or_exit(event.app)
        else:
            event.app.exit(result=result)

    def move_up(event: KeyPressEvent) -> None:
        apply("up", event)

    def move_down(event: KeyPressEvent) -> None:
        apply("down", event)

    def toggle(event: KeyPressEvent) -> None:
        apply("toggle", event)

    def select_all(event: KeyPressEvent) -> None:
        apply("select_all", event)

    def clear(event: KeyPressEvent) -> None:
        apply("clear", event)

    def submit(event: KeyPressEvent) -> None:
        apply("submit", event)

    def cancel(event: KeyPressEvent) -> None:
        apply("cancel", event)

    def suspend(event: KeyPressEvent) -> None:
        event.app.suspend_to_background(suspend_group=False)

    handlers = (
        (("up", "k"), move_up), (("down", "j"), move_down), ((" ",), toggle),
        (("a",), select_all), (("c",), clear), (("enter",), submit),
        (("q", "c-c", "c-d"), cancel), (("c-z",), suspend),
    )
    for keys, handler in handlers:
        for key in keys:
            bindings.add(key)(handler)

    received: list[int] = []

    def after_render(app: Application[SelectorResult]) -> None:
        if app.is_running and not app.is_done:
            render_or_exit(app)
        if received and app.is_running and not app.is_done:
            app.exit(result=SelectorResult("cancel", state, ()))

    application: Application[SelectorResult] = Application(
        layout=Layout(Window(FormattedTextControl(text=""))), key_bindings=bindings,
        full_screen=True, erase_when_done=False, after_render=after_render,
        input=prompt_input, output=prompt_output,
    )

    prior_handlers: dict[int, object] = {}
    managed_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    manages_signals = threading.current_thread() is threading.main_thread()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)
        if application.is_running:
            application.exit(result=SelectorResult("cancel", state, ()))

    try:
        if manages_signals:
            for signum in managed_signals:
                prior_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, on_signal)
        try:
            result = application.run(handle_sigint=False, set_exception_handler=False)
        except (EOFError, KeyboardInterrupt):
            result = SelectorResult("cancel", state, ())
    finally:
        try:
            if manages_signals:
                for signum, handler in prior_handlers.items():
                    signal.signal(signum, handler)
        finally:
            try:
                if tty_fd is not None and original_attributes is not None:
                    termios.tcflush(tty_fd, termios.TCIFLUSH)
                    termios.tcsetattr(tty_fd, termios.TCSANOW, original_attributes)
            finally:
                if owns_input:
                    prompt_input.close()

    terminating = next((s for s in received if s in (signal.SIGTERM, signal.SIGHUP)), None)
    if terminating is not None:
        signal.signal(terminating, signal.SIG_DFL)
        os.kill(os.getpid(), terminating)
        raise AssertionError("default signal action returned unexpectedly")
    return result
