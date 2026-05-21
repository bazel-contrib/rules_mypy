import argparse
import contextlib
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile
from typing import Iterator, Optional

import mypy.api
import mypy.util


def _sorted_file_list(root: pathlib.Path) -> list[pathlib.PurePath]:
    """
    Return all files under root as relative paths, sorted deterministically by POSIX path.
    """
    root = root.resolve()
    result: list[pathlib.PurePath] = []

    for dirpath, _, filenames in os.walk(root):
        relative_dirpath = pathlib.PurePath(dirpath).relative_to(root)
        result.extend(relative_dirpath / name for name in filenames)

    result.sort(key=lambda p: p.as_posix())
    return result


def _deterministic_zip(
    src_dir: str,
    dst_zip: str,
) -> None:
    """
    Create a deterministic zip archive of src_dir at dst_zip.

    Deterministic aspects:
    - Only file entries (no explicit directory entries).
    - Paths stored as relative POSIX paths.
    - Entries sorted lexicographically by POSIX path.
    - Implicit default: file timestamp set to the ZIP epoch (1980-01-01 00:00:00).
    - Implicit default: file attributes set to 0o600 (?rw-------).
    """
    src_path = pathlib.Path(src_dir).resolve()
    dst_zip_path = pathlib.Path(dst_zip).resolve()

    with zipfile.ZipFile(dst_zip_path, mode="w") as zf:
        for relative_path in _sorted_file_list(src_path):
            full_path = src_path / relative_path

            info = zipfile.ZipInfo(filename=relative_path.as_posix())
            info.create_system = 3  # Unix

            zf.writestr(
                info,
                full_path.read_bytes(),
                compress_type=zipfile.ZIP_STORED,
                compresslevel=None,
            )


def _merge_upstream_caches(cache_dir: str, upstream_caches: list[str]) -> None:
    current = pathlib.Path(cache_dir)
    current.mkdir(parents=True, exist_ok=True)

    for upstream_zip in upstream_caches:
        # TODO(mark): maybe there's a more efficient way to synchronize the cache dirs?
        with zipfile.ZipFile(upstream_zip, "r") as zf:
            for info in zf.infolist():
                relative_path = pathlib.PurePath(info.filename)
                target_path = current / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)

                if not target_path.exists():
                    with zf.open(info, "r") as src, target_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

    # missing_stubs is mutable, so remove it
    missing_stubs = current / "missing_stubs"
    if missing_stubs.exists():
        missing_stubs.unlink()


@contextlib.contextmanager
def managed_cache_dir(
    output_cache: Optional[str], upstream_caches: list[str]
) -> Iterator[str]:
    """
    Returns a managed cache directory.

    Returns a temporary directory with a merged view of upstream_caches.
    When output_cache is not None, on context exit will create a single zip file there with contents of managed cache.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _merge_upstream_caches(tmpdir, upstream_caches)
        yield tmpdir
        if output_cache:
            _deterministic_zip(tmpdir, output_cache)


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
    output_cache: Optional[str],
    upstream_caches: list[str],
    mypy_ini: Optional[str],
    srcs: list[str],
) -> None:
    if srcs:
        with managed_cache_dir(output_cache, upstream_caches) as cache_dir:
            report, errors, status = run_mypy(mypy_ini, cache_dir, srcs)
    else:
        report, errors, status = "", "", 0

    if output:
        with open(output, "w") as file:
            file.write(errors)
            file.write(report)

    # use mypy's hard_exit to exit without freeing objects, it can be meaningfully
    # faster than an orderly shutdown
    mypy.util.hard_exit(status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=False)
    parser.add_argument("--output-cache", required=False)
    parser.add_argument("--upstream-cache", required=False, action="append")
    parser.add_argument("--mypy-ini", required=False)
    parser.add_argument("src", nargs="*")
    args = parser.parse_args()

    output: Optional[str] = args.output
    output_cache: Optional[str] = args.output_cache
    upstream_cache: list[str] = args.upstream_cache or []
    mypy_ini: Optional[str] = args.mypy_ini
    srcs: list[str] = args.src

    run(output, output_cache, upstream_cache, mypy_ini, srcs)


if __name__ == "__main__":
    main()
