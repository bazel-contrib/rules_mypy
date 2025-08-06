"Custom py_library rule that also runs mypy."

load("@pip//:requirements.bzl", "all_requirements")
load("@rules_mypy//mypy:mypy.bzl", "load_stubs", "mypy")

stubs = load_stubs(requirements = all_requirements)

mypy_aspect = mypy(stubs = stubs)
