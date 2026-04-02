"Public API for interacting with the mypy rule."

load(
    "//mypy/private:mypy.bzl",
    _load_stubs = "load_stubs",
    _mypy = "mypy",
    _mypy_cli = "mypy_cli",
)

load_stubs = _load_stubs

# re-export mypy aspect factory
mypy = _mypy

# export custom mypy_cli producer
mypy_cli = _mypy_cli
