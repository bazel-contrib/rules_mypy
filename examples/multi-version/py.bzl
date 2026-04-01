"Custom py_library rule that also runs mypy."

load("@pip_A_types//:types.bzl", "types")
load("@rules_mypy//mypy:mypy.bzl", "mypy")

mypy_aspect = mypy(
    opt_in_tags = ["typecheck_A"],
    types = types,
)
