"""Run the vendored async tests on asyncio with pytest alone.

Upstream tau marks async tests with ``@pytest.mark.anyio`` and relies on the
anyio pytest plugin. The vendored harness must stay installable with pydantic
only, so the marker is honoured here instead: no plugin, no extra dependency.
"""

import asyncio
import inspect

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "anyio: run this async test on asyncio")


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True
