"""A rule that builds its deps on //exec_keys:other_platform."""

def _other_platform_transition_impl(_settings, _attr):
    return {"//command_line_option:platforms": str(Label(":other_platform"))}

_other_platform_transition = transition(
    implementation = _other_platform_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:platforms"],
)

def _other_platform_impl(ctx):
    return [DefaultInfo(files = depset(transitive = [d[DefaultInfo].files for d in ctx.attr.deps]))]

other_platform = rule(
    implementation = _other_platform_impl,
    attrs = {
        "deps": attr.label_list(cfg = _other_platform_transition),
    },
)
