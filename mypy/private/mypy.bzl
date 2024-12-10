"""
mypy aspect.

The mypy aspect runs mypy, succeeding if mypy is error free and failing if mypy produces errors. The
result of the aspect is a mypy cache directory, located at [name].mypy_cache. When provided input cache
directories (the results of other mypy builds), the underlying action first attempts to merge the cache
directories.
"""

load("@python_versions//3.12:defs.bzl", py312_binary = "py_binary")
load("@rules_mypy_pip//:requirements.bzl", "requirement")
load("@rules_python//python:py_info.bzl", RulesPythonPyInfo = "PyInfo")
load(":py_type_library.bzl", "PyTypeLibraryInfo")

MypyCacheInfo = provider(
    doc = "Output details of the mypy build rule.",
    fields = {
        "directory": "Location of the mypy cache produced by this target.",
    },
)

def _extract_import_dir(import_):
    # _main/path/to/package -> path/to/package
    return import_.split("/", 1)[-1]

def _imports(target):
    if RulesPythonPyInfo in target:
        return target[RulesPythonPyInfo].imports.to_list()
    elif PyInfo in target:
        return target[PyInfo].imports.to_list()
    else:
        return []

def _extract_imports(target):
    return [_extract_import_dir(i) for i in _imports(target)]

def _opt_out(opt_out_tags, rule_tags):
    "Returns true iff at least one opt_out_tag appears in rule_tags."
    if len(opt_out_tags) == 0:
        return False

    for tag in opt_out_tags:
        if tag in rule_tags:
            return True

    return False

def _opt_in(opt_in_tags, rule_tags):
    "Returns true iff opt_in_tags is empty or at least one of opt_in_tags appears in rule_tags."
    if len(opt_in_tags) == 0:
        return True

    for tag in opt_in_tags:
        if tag in rule_tags:
            return True

    return False

def _mypy_impl(target, ctx):
    # skip non-root targets
    if target.label.workspace_root != "":
        return []

    if RulesPythonPyInfo not in target and PyInfo not in target:
        return []

    # disable if a target is tagged with at least one suppression tag
    if _opt_out(ctx.attr._suppression_tags, ctx.rule.attr.tags):
        return []

    # disable if there are opt-in tags and one is not present
    if not _opt_in(ctx.attr._opt_in_tags, ctx.rule.attr.tags):
        return []

    # ignore rules that don't carry source files like py_proto_library
    if not hasattr(ctx.rule.files, "srcs"):
        return []

    # we need to help mypy map the location of external deps by setting
    # MYPYPATH to include the site-packages directories.
    external_deps = []

    # we need to help mypy map the location of first party deps with custom
    # 'imports' by setting MYPYPATH.
    imports_dirs = {}

    # generated dirs
    generated_dirs = {}

    upstream_caches = []

    types = []

    depsets = []

    type_mapping = dict(zip([k.label for k in ctx.attr._types_keys], ctx.attr._types_values))
    additional_types = [
        type_mapping[dep.label]
        for dep in ctx.rule.attr.deps
        if dep.label in type_mapping
    ]

    for import_ in _extract_imports(target):
        imports_dirs[import_] = 1

    for dep in (ctx.rule.attr.deps + additional_types):
        depsets.append(dep.default_runfiles.files)

        if PyTypeLibraryInfo in dep:
            types.append(dep[PyTypeLibraryInfo].directory.path + "/site-packages")
        elif dep.label.workspace_root.startswith("external/"):
            # TODO: do we need this, still?
            external_deps.append(dep.label.workspace_root + "/site-packages")

            external_deps.extend([
                "external/{}".format(x)
                for x in _imports(dep)
                if "mypy_extensions" not in x and
                   "typing_extensions" not in x
            ])
        elif dep.label.workspace_name == "":
            for import_ in _extract_imports(dep):
                imports_dirs[import_] = 1

        if MypyCacheInfo in dep:
            upstream_caches.append(dep[MypyCacheInfo].directory)

        for file in dep.default_runfiles.files.to_list():
            if file.root.path:
                generated_dirs[file.root.path] = 1

        # TODO: can we use `ctx.bin_dir.path` here to cover generated files
        # and as a way to skip iterating over depset contents to find generated
        # file roots?

    unique_imports_dirs = imports_dirs.keys()
    unique_generated_dirs = generated_dirs.keys()
    generated_imports_dirs = []
    for generated_dir in unique_generated_dirs:
        for import_ in unique_imports_dirs:
            generated_imports_dirs.append("{}/{}".format(generated_dir, import_))

    # types need to appear first in the mypy path since the module directories
    # are the same and mypy resolves the first ones, first.
    mypy_path = ":".join(types + external_deps + unique_imports_dirs + unique_generated_dirs + generated_imports_dirs)

    output_file = ctx.actions.declare_file(ctx.rule.attr.name + ".mypy_stdout")

    args = ctx.actions.args()
    args.add("--output", output_file)

    if ctx.attr.cache:
        cache_directory = ctx.actions.declare_directory(ctx.rule.attr.name + ".mypy_cache")
        args.add("--cache-dir", cache_directory.path)

        outputs = [output_file, cache_directory]
        result_info = [
            MypyCacheInfo(directory = cache_directory),
            OutputGroupInfo(mypy = depset(outputs)),
        ]
    else:
        outputs = [output_file]
        result_info = [OutputGroupInfo(mypy = depset(outputs))]

    args.add_all([c.path for c in upstream_caches], before_each = "--upstream-cache")
    args.add_all(ctx.rule.files.srcs)

    if hasattr(ctx.attr, "_mypy_ini"):
        args.add("--mypy-ini", ctx.file._mypy_ini.path)
        config_files = [ctx.file._mypy_ini]
    else:
        config_files = []

    extra_env = {}
    if ctx.attr.color:
        # force color on
        extra_env["MYPY_FORCE_COLOR"] = "1"

        # force color on only works if TERM is set to something that supports color
        extra_env["TERM"] = "xterm-256color"

    py_type_files = [x for x in ctx.rule.files.data if x.basename == "py.typed" or x.extension == "pyi"]
    ctx.actions.run(
        mnemonic = "mypy",
        progress_message = "mypy %{label}",
        inputs = depset(
            direct = ctx.rule.files.srcs + py_type_files + upstream_caches + config_files,
            transitive = depsets,
        ),
        outputs = outputs,
        executable = ctx.executable._mypy_cli,
        arguments = [args],
        env = {"MYPYPATH": mypy_path} | ctx.configuration.default_shell_env | extra_env,
    )

    return result_info

def mypy(
        mypy_cli = None,
        mypy_ini = None,
        types = None,
        cache = True,
        color = True,
        suppression_tags = None,
        opt_in_tags = None):
    """
    Create a mypy target inferring upstream caches from deps.

    Args:
        mypy_cli:   (optional) a replacement mypy_cli to use (recommended to produce
                    with mypy_cli macro)
        mypy_ini:   (optional) mypy_ini file to use
        types:      (optional) a dict of dependency label to types dependency label
                    example:
                    ```
                    {
                        requirement("cachetools"): requirement("types-cachetools"),
                    }
                    ```
                    Use the types extension to create this map for a requirements.in
                    or requirements.txt file.
        cache:      (optional, default True) propagate the mypy cache
        color:      (optional, default True) use color in mypy output
        suppression_tags: (optional, default ["no-mypy"]) tags that suppress running
                    mypy on a particular target.
        opt_in_tags: (optional, default []) tags that must be present for mypy to run
                    on a particular target. When specified, this ruleset will _only_
                    run on targets with this tag.

    Returns:
        a mypy aspect.
    """
    types = types or {}

    additional_attrs = {}

    return aspect(
        implementation = _mypy_impl,
        attr_aspects = ["deps"],
        attrs = {
            "_mypy_cli": attr.label(
                default = mypy_cli or "@rules_mypy//mypy/private:mypy",
                cfg = "exec",
                executable = True,
            ),
            "_mypy_ini": attr.label(
                # we provide a default here because Bazel won't allow Label attrs
                # that are public, or private attrs that have default values of None
                default = mypy_ini or "@rules_mypy//mypy/private:default_mypy.ini",
                allow_single_file = True,
                mandatory = False,
            ),
            # pass the dict[Label, Label] in parts because Bazel doesn't have
            # this kind of attr to pass naturally
            "_types_keys": attr.label_list(default = types.keys()),
            "_types_values": attr.label_list(default = types.values()),
            "_suppression_tags": attr.string_list(default = suppression_tags or ["no-mypy"]),
            "_opt_in_tags": attr.string_list(default = opt_in_tags or []),
            "cache": attr.bool(default = cache),
            "color": attr.bool(default = color),
        } | additional_attrs,
    )

def mypy_cli(name, deps = None, mypy_requirement = None, py_binary = py312_binary, tags = None):
    """
    Produce a custom mypy executable for use with the mypy build rule.

    Args:
        name: name of the binary target this macro produces
        deps: (optional) additional dependencies to include (e.g. mypy plugins)
              (note: must match the Python version of py_binary)
        mypy_requirement: (optional) a replacement mypy requirement
              (note: must match the Python version of py_binary)
        py_binary: (optional) the py_binary rule to use when constructing this target
              (defaults to a rules_mypy specified version, currently Python 3.12)
        tags: (optional) tags to include in the binary target
    """

    deps = deps or []
    mypy_requirement = mypy_requirement or requirement("mypy")

    py_binary(
        name = name,
        srcs = ["@rules_mypy//mypy/private:mypy.py"],
        main = "@rules_mypy//mypy/private:mypy.py",
        visibility = ["//visibility:public"],
        deps = [mypy_requirement] + deps,
        tags = tags,
    )
