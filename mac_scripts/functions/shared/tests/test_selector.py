import io
import os
import pty
import threading
import time

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from shared.selector import (
    SelectorItem,
    create_selector_state,
    reduce_selector,
    run_selector,
)


ITEMS = (
    SelectorItem("git@github.com:org/a.git", "git@github.com:org/a.git"),
    SelectorItem("git@gitlab.com:acme/b.git", "git@gitlab.com:acme/b.git"),
    SelectorItem("git@github.com:org/c.git", "git@github.com:org/c.git"),
)


def test_toggle_and_submit_returns_display_order() -> None:
    state = create_selector_state(ITEMS)
    state = reduce_selector(state, "toggle", multiple=True).state
    state = reduce_selector(state, "down", multiple=True).state
    state = reduce_selector(state, "down", multiple=True).state
    state = reduce_selector(state, "toggle", multiple=True).state

    result = reduce_selector(state, "submit", multiple=True)

    assert result.kind == "submit"
    assert result.selected == (ITEMS[0].value, ITEMS[2].value)


def test_initial_selection_is_matched_by_value_and_submitted_in_display_order() -> None:
    state = create_selector_state(
        ITEMS,
        initial=(ITEMS[2].value, ITEMS[0].value, "not-present"),
    )

    result = reduce_selector(state, "submit", multiple=True)

    assert state.selected == frozenset({0, 2})
    assert result.selected == (ITEMS[0].value, ITEMS[2].value)


def test_navigation_stops_at_first_and_last_item() -> None:
    state = create_selector_state(ITEMS)

    state = reduce_selector(state, "up", multiple=True).state
    assert state.cursor == 0
    for _ in range(5):
        state = reduce_selector(state, "down", multiple=True).state
    assert state.cursor == 2


def test_select_all_and_clear_only_change_multiple_selection() -> None:
    state = reduce_selector(
        create_selector_state(ITEMS), "select_all", multiple=True
    ).state
    assert state.selected == frozenset({0, 1, 2})

    state = reduce_selector(state, "clear", multiple=True).state
    assert state.selected == frozenset()

    single = create_selector_state(ITEMS, initial=(ITEMS[1].value,))
    assert reduce_selector(single, "select_all", multiple=False).state == single
    assert reduce_selector(single, "clear", multiple=False).state == single


def test_single_submit_returns_current_item_without_toggling() -> None:
    state = create_selector_state(ITEMS, initial=(ITEMS[2].value,))
    state = reduce_selector(state, "down", multiple=False).state
    state = reduce_selector(state, "toggle", multiple=False).state

    result = reduce_selector(state, "submit", multiple=False)

    assert result.selected == (ITEMS[1].value,)


def test_cancel_returns_empty_selection_and_preserves_state() -> None:
    state = create_selector_state(ITEMS, initial=(ITEMS[0].value,))

    result = reduce_selector(state, "cancel", multiple=True)

    assert result.kind == "cancel"
    assert result.selected == ()
    assert result.state == state


def test_unknown_key_continues_without_mutating_state() -> None:
    state = create_selector_state(ITEMS, initial=(ITEMS[0].value,))

    result = reduce_selector(state, "future", multiple=True)

    assert result.kind == "continue"
    assert result.state == state
    assert result.state is not state


def test_run_selector_applies_prompt_toolkit_bindings_and_renders_transitions() -> None:
    rendered = []
    with create_pipe_input() as pipe_input:
        pipe_input.send_text("\x1b[B \x1b[A \r")
        result = run_selector(
            ITEMS,
            initial=(),
            multiple=True,
            input=pipe_input,
            output=DummyOutput(),
            render=rendered.append,
        )

    assert result.kind == "submit"
    assert result.selected == (ITEMS[0].value, ITEMS[1].value)
    assert rendered[0] == create_selector_state(ITEMS)
    assert rendered[-1].selected == frozenset({0, 1})


def test_run_selector_supports_vim_select_all_clear_and_cancel_bindings() -> None:
    rendered = []
    with create_pipe_input() as pipe_input:
        pipe_input.send_text("ja ckq")
        result = run_selector(
            ITEMS,
            initial=(ITEMS[0].value,),
            multiple=True,
            input=pipe_input,
            output=DummyOutput(),
            render=rendered.append,
        )

    assert result.kind == "cancel"
    assert result.selected == ()
    assert result.state.cursor == 0
    assert result.state.selected == frozenset()


def test_run_selector_preserves_a_split_arrow_sequence() -> None:
    selector_started = threading.Event()
    with create_pipe_input() as pipe_input:
        def send_split_arrow() -> None:
            assert selector_started.wait(timeout=1)
            pipe_input.send_text("\x1b[")
            time.sleep(0.05)
            pipe_input.send_text("B\r")

        producer = threading.Thread(target=send_split_arrow)
        producer.start()
        result = run_selector(
            ITEMS,
            initial=(),
            multiple=False,
            input=pipe_input,
            output=DummyOutput(),
            render=lambda _state: selector_started.set(),
        )
        producer.join(timeout=1)

    assert not producer.is_alive()
    assert result.kind == "submit"
    assert result.selected == (ITEMS[1].value,)


def test_run_selector_does_not_paint_prompt_toolkit_to_redirected_output() -> None:
    master_fd, slave_fd = pty.openpty()
    redirected = io.StringIO()
    sent_keys = False
    tty_input = open(
        os.dup(slave_fd),
        "r",
        encoding="utf-8",
        buffering=1,
        closefd=True,
    )

    def render(state) -> None:
        nonlocal sent_keys
        selected = ",".join(str(index) for index in sorted(state.selected))
        redirected.write(f"FRAME:{state.cursor}:{selected}\n")
        if not sent_keys:
            sent_keys = True
            os.write(master_fd, b" \r")

    try:
        result = run_selector(
            ITEMS,
            initial=(),
            multiple=True,
            input=tty_input,
            output=redirected,
            render=render,
        )
    finally:
        tty_input.close()
        os.close(master_fd)
        os.close(slave_fd)

    assert result.kind == "submit"
    assert result.selected == (ITEMS[0].value,)
    assert redirected.getvalue() == "FRAME:0:\nFRAME:0:0\n"
