"Extension for generating a types repository containing py_type_librarys for requirements."

load("//mypy/private:types.bzl", "generate")

requirements = tag_class(
    attrs = {
        "name": attr.string(),
        "pip_requirements": attr.label(),
        "requirements_txt": attr.label(allow_single_file = True),
        "exclude_requirements": attr.string_list(default = [], mandatory = False),
        "requirements_darwin": attr.label(
            allow_single_file = True,
            doc = "MacOS only version of requirements_txt.",
        ),
        "requirements_linux": attr.label(
            allow_single_file = True,
            doc = "Linux only version of requirements_txt.",
        ),
        "requirements_windows": attr.label(
            allow_single_file = True,
            doc = "Windows only version of requirements_txt.",
        ),
    },
)

def _extension(module_ctx):
    for mod in module_ctx.modules:
        for tag in mod.tags.requirements:
            generate(
                name = tag.name,
                pip_requirements = tag.pip_requirements,
                requirements_txt = tag.requirements_txt,
                requirements_darwin = tag.requirements_darwin,
                requirements_linux = tag.requirements_linux,
                requirements_windows = tag.requirements_windows,
                exclude_requirements = tag.exclude_requirements,
            )

types = module_extension(
    implementation = _extension,
    tag_classes = {
        "requirements": requirements,
    },
)
