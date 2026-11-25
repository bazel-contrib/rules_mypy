"Custom py_library rule that also runs mypy."

load("@pip_types//:types.bzl", "types")
load("@rules_mypy//mypy:mypy.bzl", "mypy")

mypy_aspect = mypy(types = types | {
    Label("//exec_keys:pkg"): Label("@pip_types//:types-cachetools"),
})
