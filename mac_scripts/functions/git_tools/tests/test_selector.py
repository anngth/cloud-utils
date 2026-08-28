def test_git_tools_selector_reexports_shared_engine() -> None:
    from git_tools.selector import SelectorItem as git_item
    from shared.selector import SelectorItem as shared_item

    assert git_item is shared_item
