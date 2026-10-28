def test_git_tools_selector_reexports_shared_engine() -> None:
    from shared.selector import SelectorItem as shared_item

    from git_tools.selector import SelectorItem as git_item

    assert git_item is shared_item
