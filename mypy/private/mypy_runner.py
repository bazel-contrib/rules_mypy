import argparse
import contextlib
import pathlib
import os
import shutil
import sys
import tempfile
from typing import Any, Generator, Optional

import mypy.api
import mypy.util


def _hardlink_tree(src: str, dst: str) -> None:
    """Iteratively merge src into dst via hardlinks.

    Hardlinks share inodes so each downstream cache adds essentially zero
    unique disk blocks rather than re-copying the transitive merge content.

    Deliberately use os.scandir for best performance.
    Falls back to shutil.copy if os.link fails (e.g. cross-filesystem).
    """
    stack = [(src, dst)]
    while stack:
        cur_src, cur_dst = stack.pop()
        try:
            os.mkdir(cur_dst)
        except FileExistsError:
            pass

        with os.scandir(cur_src) as it:
            for entry in it:
                sp = entry.path
                dp = cur_dst + "/" + entry.name
                if entry.is_dir(follow_symlinks=False):
                    stack.append((sp, dp))
                    continue

                try:
                    os.link(sp, dp)
                except FileExistsError:
                    pass
                except OSError:
                    shutil.copy(sp, dp)


def _merge_upstream_caches(cache_dir: str, upstream_caches: list[str]) -> None:
    current = pathlib.Path(cache_dir)
    current.mkdir(parents=True, exist_ok=True)

    for upstream_dir in upstream_caches:
        _hardlink_tree(upstream_dir, cache_dir)

    # missing_stubs is mutable; the merge may have hardlinked it from upstream.
    # Unlink so mypy can rewrite it without touching the upstream inode.
    (current / "missing_stubs").unlink(missing_ok=True)


@contextlib.contextmanager
def managed_cache_dir(
    cache_dir: Optional[str], upstream_caches: list[str]
) -> Generator[str, Any, Any]:
    """
    Returns a managed cache directory.

    When cache_dir exists, returns a merged view of cache_dir with upstream_caches.
    Otherwise, returns a temporary directory that will be cleaned up when the resource
    is released.
    """
    if cache_dir:
        _merge_upstream_caches(cache_dir, list(upstream_caches))
        yield cache_dir
    else:
        tmpdir = tempfile.TemporaryDirectory()
        yield tmpdir.name
        tmpdir.cleanup()


def run_mypy(
    mypy_ini: Optional[str], cache_dir: str, srcs: list[str]
) -> tuple[str, str, int]:
    maybe_config = ["--config-file", mypy_ini] if mypy_ini else []
    report, errors, status = mypy.api.run(
        maybe_config
        + [
            # do not check mtime in cache
            "--skip-cache-mtime-checks",
            # mypy defaults to incremental, but force it on anyway
            "--incremental",
            # use a known cache-dir
            f"--cache-dir={cache_dir}",
            # use current dir + MYPYPATH to resolve deps
            "--explicit-package-bases",
            # speedup
            "--fast-module-lookup",
        ]
        + srcs
    )
    if status:
        sys.stderr.write(errors)
        sys.stderr.write(report)

    return report, errors, status


def run(
    output: Optional[str],
    cache_dir: Optional[str],
    upstream_caches: list[str],
    mypy_ini: Optional[str],
    srcs: list[str],
) -> None:
    if len(srcs) > 0:
        with managed_cache_dir(cache_dir, upstream_caches) as cache_dir:
            report, errors, status = run_mypy(mypy_ini, cache_dir, srcs)
    else:
        report, errors, status = "", "", 0

    if output:
        with open(output, "w+") as file:
            file.write(errors)
            file.write(report)

    # use mypy's hard_exit to exit without freeing objects, it can be meaningfully
    # faster than an orderly shutdown
    mypy.util.hard_exit(status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=False)
    parser.add_argument("-c", "--cache-dir", required=False)
    parser.add_argument("--upstream-cache", required=False, action="append")
    parser.add_argument("--mypy-ini", required=False)
    parser.add_argument("src", nargs="*")
    args = parser.parse_args()

    output: Optional[str] = args.output
    cache_dir: Optional[str] = args.cache_dir
    upstream_cache: list[str] = args.upstream_cache or []
    mypy_ini: Optional[str] = args.mypy_ini
    srcs: list[str] = args.src

    run(output, cache_dir, upstream_cache, mypy_ini, srcs)


if __name__ == "__main__":
    main()
