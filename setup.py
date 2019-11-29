from setuptools import setup, find_packages
import os
import sys

import versioneer


def get_long_description():
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"),
        encoding="utf8",
    ) as fp:
        return fp.read()


def get_version():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "datasette", "version.py"
    )
    g = {}
    exec(open(path).read(), g)
    return g["__version__"]


setup(
    name="datasette",
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    description="A tool for exploring and publishing data",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Simon Willison",
    license="Apache License, Version 2.0",
    url="https://github.com/simonw/datasette",
    packages=find_packages(exclude="tests"),
    package_data={"datasette": ["templates/*.html"]},
    include_package_data=True,
    install_requires=[
        "click~=7.0",
        "click-default-group~=1.2.2",
        "Jinja2~=2.10.3",
        "hupper~=1.9",
        "pint~=0.9",
        "pluggy~=0.13.0",
        "uvicorn~=0.10.4",
        "aiofiles~=0.4.0",
        "marshmallow~=3.2.0",
        "marshmallow-union~=0.1.12",
    ],
    entry_points="""
        [console_scripts]
        datasette=datasette.cli:cli
    """,
    setup_requires=["pytest-runner"],
    extras_require={
        "docs": ["sphinx_rtd_theme", "sphinx-autobuild"],
        "test": [
            "pytest~=5.2.2",
            "pytest-asyncio~=0.10.0",
            "aiohttp~=3.6.2",
            "beautifulsoup4~=4.8.1",
            "asgiref~=3.2.3",
            "black~=19.10b0",
        ],
    },
    tests_require=["datasette[test]"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Database",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.6",
    ],
)
