from re import VERBOSE
from setuptools import setup, find_packages
import os
import sys


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
    version=get_version(),
    description="An open source multi-tool for exploring and publishing data",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Simon Willison",
    license="Apache License, Version 2.0",
    url="https://datasette.io/",
    project_urls={
        "Documentation": "https://docs.datasette.io/en/stable/",
        "Changelog": "https://docs.datasette.io/en/stable/changelog.html",
        "Live demo": "https://latest.datasette.io/",
        "Source code": "https://github.com/simonw/datasette",
        "Issues": "https://github.com/simonw/datasette/issues",
        "CI": "https://github.com/simonw/datasette/actions?query=workflow%3ATest",
    },
    packages=find_packages(exclude=("tests",)),
    package_data={"datasette": ["templates/*.html"]},
    include_package_data=True,
    python_requires=">=3.6",
    install_requires=[
        "asgiref>=3.2.10,<3.4.0",
        "click~=7.1.1",
        "click-default-group~=1.2.2",
        "Jinja2>=2.10.3,<2.12.0",
        "hupper~=1.9",
        "httpx>=0.15",
        "pint~=0.9",
        "pluggy~=0.13.0",
        "uvicorn~=0.11",
        "aiofiles>=0.4,<0.7",
        "janus>=0.4,<0.7",
        "asgi-csrf>=0.6",
        "PyYAML~=5.3",
        "mergedeep>=1.1.1,<1.4.0",
        "itsdangerous~=1.1",
        "python-baseconv==1.2.2",
    ],
    entry_points="""
        [console_scripts]
        datasette=datasette.cli:cli
    """,
    setup_requires=["pytest-runner"],
    extras_require={
        "docs": ["sphinx_rtd_theme", "sphinx-autobuild"],
        "test": [
            "pytest>=5.2.2,<6.3.0",
            "pytest-asyncio>=0.10,<0.15",
            "beautifulsoup4>=4.8.1,<4.10.0",
            "black==20.8b1",
            "pytest-timeout>=1.4.2,<1.5",
            "trustme>=0.7,<0.8",
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
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.6",
    ],
)
