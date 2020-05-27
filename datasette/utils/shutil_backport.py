"""
Backported from Python 3.8.

This code is licensed under the Python License:
https://github.com/python/cpython/blob/v3.8.3/LICENSE
"""
import os
from shutil import copy, copy2, copystat, Error


def _copytree(
    entries,
    src,
    dst,
    symlinks,
    ignore,
    copy_function,
    ignore_dangling_symlinks,
    dirs_exist_ok=False,
):
    if ignore is not None:
        ignored_names = ignore(src, set(os.listdir(src)))
    else:
        ignored_names = set()

    os.makedirs(dst, exist_ok=dirs_exist_ok)
    errors = []
    use_srcentry = copy_function is copy2 or copy_function is copy

    for srcentry in entries:
        if srcentry.name in ignored_names:
            continue
        srcname = os.path.join(src, srcentry.name)
        dstname = os.path.join(dst, srcentry.name)
        srcobj = srcentry if use_srcentry else srcname
        try:
            if srcentry.is_symlink():
                linkto = os.readlink(srcname)
                if symlinks:
                    os.symlink(linkto, dstname)
                    copystat(srcobj, dstname, follow_symlinks=not symlinks)
                else:
                    if not os.path.exists(linkto) and ignore_dangling_symlinks:
                        continue
                    if srcentry.is_dir():
                        copytree(
                            srcobj,
                            dstname,
                            symlinks,
                            ignore,
                            copy_function,
                            dirs_exist_ok=dirs_exist_ok,
                        )
                    else:
                        copy_function(srcobj, dstname)
            elif srcentry.is_dir():
                copytree(
                    srcobj,
                    dstname,
                    symlinks,
                    ignore,
                    copy_function,
                    dirs_exist_ok=dirs_exist_ok,
                )
            else:
                copy_function(srcentry, dstname)
        except Error as err:
            errors.extend(err.args[0])
        except OSError as why:
            errors.append((srcname, dstname, str(why)))
    try:
        copystat(src, dst)
    except OSError as why:
        # Copying file access times may fail on Windows
        if getattr(why, "winerror", None) is None:
            errors.append((src, dst, str(why)))
    if errors:
        raise Error(errors)
    return dst


def copytree(
    src,
    dst,
    symlinks=False,
    ignore=None,
    copy_function=copy2,
    ignore_dangling_symlinks=False,
    dirs_exist_ok=False,
):
    with os.scandir(src) as entries:
        return _copytree(
            entries=entries,
            src=src,
            dst=dst,
            symlinks=symlinks,
            ignore=ignore,
            copy_function=copy_function,
            ignore_dangling_symlinks=ignore_dangling_symlinks,
            dirs_exist_ok=dirs_exist_ok,
        )
