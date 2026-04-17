"Repository rule to generate `py_type_library` from input typings/stubs requirements."

_PY_TYPE_LIBRARY_TEMPLATE = """
py_type_library(
    name = "{requirement}",
    typing = requirement("{requirement}"),
    visibility = ["//visibility:public"],
)
"""

def _render_build(rctx, types):
    content = ""
    content += """load("{pip_requirements}", "requirement")\n""".format(
        pip_requirements = rctx.attr.pip_requirements,
    )
    content += """load("@rules_mypy//mypy:py_type_library.bzl", "py_type_library")\n"""
    content += 'load({bzl_library_bzl}, "bzl_library")\n'.format(
        bzl_library_bzl = repr(str(Label("@bazel_skylib//:bzl_library.bzl"))),
    )
    for requirement in types:
        content += _PY_TYPE_LIBRARY_TEMPLATE.format(
            requirement = requirement,
            raw = requirement.removeprefix("types-").removesuffix("-stubs"),
        ) + "\n"
    content += '''bzl_library(
    name = "types",
    srcs = ["types.bzl"],
    deps = [":requirements"],
    visibility = ["//visibility:public"],
)

bzl_library(
    name = "requirements",
    srcs = [{requirements_bzl}],
    deps = [{pip_bzl}],
    visibility = ["//visibility:private"],
)
'''.format(
        requirements_bzl = repr(str(rctx.attr.pip_requirements)),
        pip_bzl = repr(str(Label("@rules_python//python:pip_bzl"))),
    )
    return content

def _render_types_bzl(rctx, types):
    content = ""
    content += """load("{pip_requirements}", "requirement")\n""".format(
        pip_requirements = rctx.attr.pip_requirements,
    )
    content += "types = {\n"
    for requirement in types:
        content += """    requirement("{raw}"): "@@{name}//:{requirement}",\n""".format(
            raw = requirement.removeprefix("types-").removesuffix("-stubs"),
            name = str(rctx.attr.name),
            requirement = requirement,
        )
    content += "}\n"
    return content

def _generate_impl(rctx):
    contents = rctx.read(rctx.attr.requirements_txt)

    types = []

    # this is a very, very naive parser
    for line in contents.splitlines():
        if line.startswith("#") or line == "":
            continue

        if ";" in line:
            line, _ = line.split(";")

        if "~=" in line:
            req, _ = line.split("~=")
        elif "==" in line:
            req, _ = line.split("==")
        elif "<=" in line:
            req, _ = line.split("<=")
        else:
            continue

        req = req.strip()
        if req in rctx.attr.exclude_requirements:
            continue

        if req.endswith("-stubs") or req.startswith("types-"):
            types.append(req)

    rctx.file("BUILD.bazel", content = _render_build(rctx, types))
    rctx.file("types.bzl", content = _render_types_bzl(rctx, types))

generate = repository_rule(
    implementation = _generate_impl,
    attrs = {
        "pip_requirements": attr.label(),
        "requirements_txt": attr.label(allow_single_file = True),
        "exclude_requirements": attr.string_list(default = []),
    },
)
