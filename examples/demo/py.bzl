"Custom py_library rule that also runs mypy."

load("@pip//:requirements.bzl", "all_requirements", "requirement")
load("@rules_mypy//mypy:mypy.bzl", "load_stubs", "mypy")

stubs = load_stubs(
    requirements = all_requirements,
    overrides = {
        # See manual_stubs/implicit/
        requirement("six"): "@@//manual_stubs/implicit:foo-stubs",
    },
)

mypy_aspect = mypy(stubs = stubs)
