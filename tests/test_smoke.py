"""Public smoke tests for basic module wiring.

Keep these minimal and free of any real-world data.
"""

from __future__ import annotations


def test_imports() -> None:
    import beanbeaver
    import beanbeaver.cli.main
    import beanbeaver.importers
    import beanbeaver.receipt
    import beanbeaver.runtime

    assert beanbeaver is not None
    assert beanbeaver.cli.main is not None
    assert beanbeaver.importers is not None
    assert beanbeaver.receipt is not None
    assert beanbeaver.runtime is not None
