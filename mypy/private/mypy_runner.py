import argparse
import atexit
import contextlib
import json
import multiprocessing
import multiprocessing.context
import pathlib
import os
import shutil
import sys
import tempfile
from typing import Any, Generator, Optional

import mypy.api
import mypy.util


def _merge_upstream_caches(cache_dir: str, upstream_caches: list[str]) -> None:
    current = pathlib.Path(cache_dir)
    current.mkdir(parents=True, exist_ok=True)

    for upstream_dir in upstream_caches:
        upstream = pathlib.Path(upstream_dir)

        # TODO(mark): maybe there's a more efficient way to synchronize the cache dirs?
        for dirpath_str, _, filenames in os.walk(upstream.as_posix()):
            dirpath = pathlib.Path(dirpath_str)
            relative_dir = dirpath.relative_to(upstream)
            for file in filenames:
                upstream_path = dirpath / file
                target_path = current / relative_dir / file
                if not target_path.parent.exists():
                    target_path.parent.mkdir(parents=True)
                if not target_path.exists():
                    shutil.copy(upstream_path, target_path)

    # missing_stubs is mutable, so remove it
    missing_stubs = current / "missing_stubs"
    if missing_stubs.exists():
        missing_stubs.unlink()


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


def _execute(
    output: Optional[str],
    cache_dir: Optional[str],
    upstream_caches: list[str],
    mypy_ini: Optional[str],
    mypy_path: Optional[str],
    srcs: list[str],
) -> tuple[int, str]:
    """Run mypy and return (exit_code, combined_output). Does not call hard_exit."""
    if mypy_path is not None:
        os.environ["MYPYPATH"] = mypy_path
    elif "MYPYPATH" in os.environ:
        del os.environ["MYPYPATH"]

    if len(srcs) > 0:
        with managed_cache_dir(cache_dir, upstream_caches) as cache_dir:
            report, errors, status = run_mypy(mypy_ini, cache_dir, srcs)
    else:
        report, errors, status = "", "", 0

    # Only emit anything when mypy reports an error: otherwise Bazel prints a
    # "INFO: From mypy //target:" header followed by "Success: no issues found
    # in N source files" for every action, which drowns out real signal in
    # //... builds. collect_mypy treats an empty file as a passing target.
    combined = errors + report if status else ""
    if output:
        with open(output, "w+") as file:
            file.write(combined)

    return status, combined


def run(
    output: Optional[str],
    cache_dir: Optional[str],
    upstream_caches: list[str],
    mypy_ini: Optional[str],
    mypy_path: Optional[str],
    srcs: list[str],
) -> None:
    status, _ = _execute(output, cache_dir, upstream_caches, mypy_ini, mypy_path, srcs)
    # use mypy's hard_exit to exit without freeing objects, it can be meaningfully
    # faster than an orderly shutdown
    mypy.util.hard_exit(status)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(fromfile_prefix_chars="@")
    parser.add_argument("--output", required=False)
    parser.add_argument("-c", "--cache-dir", required=False)
    parser.add_argument("--upstream-cache", required=False, action="append")
    parser.add_argument("--mypy-ini", required=False)
    parser.add_argument("--mypy-path", required=False)
    parser.add_argument("src", nargs="*")
    return parser


def _expand_flagfile(arguments: list[str]) -> list[str]:
    if len(arguments) == 1 and arguments[0].startswith("@"):
        return pathlib.Path(arguments[0][1:]).read_text().splitlines()
    return arguments


def _execute_inline(args: argparse.Namespace, sandbox_dir: str) -> tuple[int, str]:
    """Run mypy in the worker process itself (no fork)."""
    cwd = os.getcwd()
    try:
        if sandbox_dir:
            os.chdir(sandbox_dir)
        try:
            return _execute(
                args.output,
                args.cache_dir,
                args.upstream_cache or [],
                args.mypy_ini,
                args.mypy_path,
                args.src,
            )
        except BaseException as e:  # noqa: BLE001
            return 1, f"{type(e).__name__}: {e}"
    finally:
        os.chdir(cwd)


def _mp_context() -> Optional[multiprocessing.context.ForkContext]:
    """Return a fork-based multiprocessing context, or None when fork isn't available.

    Forking is required: spawn / forkserver re-exec Python and rebuild the
    parent's typeshed + mypy module state on every request, defeating the
    persistent-worker speedup. multiprocessing.Process under the fork context
    runs the child to completion and then calls os._exit, which skips the
    parent's atexit cache-cleanup in the child.
    """
    if not hasattr(os, "fork"):
        return None
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return None


def _child_entry(
    args: argparse.Namespace,
    sandbox_dir: str,
    queue: "multiprocessing.Queue[tuple[int, str]]",
) -> None:
    try:
        if sandbox_dir:
            os.chdir(sandbox_dir)
        result = _execute(
            args.output,
            args.cache_dir,
            args.upstream_cache or [],
            args.mypy_ini,
            args.mypy_path,
            args.src,
        )
    except BaseException as e:  # noqa: BLE001
        result = (1, f"{type(e).__name__}: {e}")
    queue.put(result)


def _execute_in_child(args: argparse.Namespace, sandbox_dir: str) -> tuple[int, str]:
    """Run mypy in a forked child so parent RSS stays small."""
    ctx = _mp_context()
    if ctx is None:
        return _execute_inline(args, sandbox_dir)

    queue: "multiprocessing.Queue[tuple[int, str]]" = ctx.Queue()
    proc = ctx.Process(target=_child_entry, args=(args, sandbox_dir, queue))
    proc.start()
    try:
        result = queue.get()
    finally:
        proc.join()
    return result


_PERSISTENT_CACHE_DIR: Optional[str] = None
_SIGNAL_HANDLERS_INSTALLED = False
_MIN_PERSISTENT_CACHE_FREE_BYTES = 1 << 30  # 1 GiB


def _cleanup_persistent_cache() -> None:
    """Wipe this worker's tmpfs cache on exit (atexit / signal handler)."""
    if _PERSISTENT_CACHE_DIR is None:
        return
    shutil.rmtree(_PERSISTENT_CACHE_DIR, ignore_errors=True)


def _install_signal_handlers() -> None:
    """Convert SIGTERM / SIGINT into sys.exit so atexit handlers run."""
    global _SIGNAL_HANDLERS_INSTALLED
    if _SIGNAL_HANDLERS_INSTALLED:
        return
    import signal as _signal

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ARG001
        sys.exit(128 + signum)

    for sig in (_signal.SIGTERM, _signal.SIGINT, _signal.SIGHUP):
        try:
            _signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            pass
    _SIGNAL_HANDLERS_INSTALLED = True


def _persistent_cache_dir() -> Optional[str]:
    """Per-worker mypy cache dir on tmpfs; None if too small for an incremental cache."""
    global _PERSISTENT_CACHE_DIR
    if _PERSISTENT_CACHE_DIR is not None:
        return _PERSISTENT_CACHE_DIR
    base = os.environ.get("MYPY_WORKER_CACHE_BASE", "/dev/shm")
    try:
        st = os.statvfs(base)
    except OSError:
        return None
    if st.f_bavail * st.f_frsize < _MIN_PERSISTENT_CACHE_FREE_BYTES:
        return None
    cache_dir = f"{base}/mypy-worker-{os.getpid()}"
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return None
    _PERSISTENT_CACHE_DIR = cache_dir
    atexit.register(_cleanup_persistent_cache)
    _install_signal_handlers()
    return cache_dir


def _worker_loop() -> None:
    parser = _make_parser()
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        request = json.loads(line)
        request_id = request.get("requestId", 0)
        sandbox_dir = request.get("sandboxDir", "")
        args = parser.parse_args(_expand_flagfile(request.get("arguments", [])))

        # Override the per-action tempdir cache_dir with our persistent one.
        cache_override = _persistent_cache_dir()
        if cache_override is not None:
            args.cache_dir = cache_override

        status, output_text = _execute_in_child(args, sandbox_dir)

        sys.stdout.write(
            json.dumps({"exitCode": status, "output": output_text, "requestId": request_id}) + "\n"
        )
        sys.stdout.flush()


def main() -> None:
    if "--persistent_worker" in sys.argv:
        _worker_loop()
        return

    parser = _make_parser()
    args = parser.parse_args()

    run(args.output, args.cache_dir, args.upstream_cache or [], args.mypy_ini, args.mypy_path, args.src)


if __name__ == "__main__":
    main()
