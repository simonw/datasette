from setuptools import setup, find_packages
import os


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
    with open(path) as fp:
        exec(fp.read(), g)
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
        "asgiref>=3.2.10,<3.5.0",
        "click>=7.1.1,<8.1.0",
        "click-default-group~=1.2.2",
        "Jinja2>=2.10.3,<3.1.0",
        "hupper~=1.9",
        "httpx>=0.20",
        "pint~=0.9",
        "pluggy>=1.0,<1.1",
        "uvicorn~=0.11",
        "aiofiles>=0.4,<0.9",
        "janus>=0.6.2,<1.1",
        "asgi-csrf>=0.9",
        "PyYAML>=5.3,<7.0",
        "mergedeep>=1.1.1,<1.4.0",
        "itsdangerous>=1.1,<3.0",
        "python-baseconv==1.2.2",
        # The following are known to work with 3.6
        'asgiref<=3.4.1;python_version<="3.6"',
        'click<=8.0.3;python_version<="3.6"',
        'click-default-group<=1.2.2;python_version<="3.6"',
        'Jinja2<=3.0.3;python_version<="3.6"',
        'hupper<=1.10.3;python_version<="3.6"',
        'httpx<=0.21.3;python_version<="3.6"',
        'pint<=0.17;python_version<="3.6"',
        'pluggy<=1.0.0;python_version<="3.6"',
        'uvicorn<=0.16.0;python_version<="3.6"',
        'aiofiles<=0.8.0;python_version<="3.6"',
        'janus<=0.7.0;python_version<="3.6"',
        'asgi-csrf<=0.9;python_version<="3.6"',
        'PyYAML<=6.0;python_version<="3.6"',
        'mergedeep<=1.3.4;python_version<="3.6"',
        'itsdangerous<=2.0.1;python_version<="3.6"',
        'python-baseconv<=1.2.2;python_version<="3.6"',
    ],
    entry_points="""
        [console_scripts]
        datasette=datasette.cli:cli
    """,
    setup_requires=["pytest-runner"],
    extras_require={
        "docs": ["sphinx_rtd_theme", "sphinx-autobuild", "codespell"],
        "test": [
            "pytest==6.2.5",
            "pytest-xdist==2.5.0",
            "pytest-asyncio==0.16.0",
            "beautifulsoup4==4.10.0",
            "black==21.12b0",
            "pytest-timeout==2.0.2",
            "trustme==0.9.0",
            "cogapp==3.3.0",
        ],
        "rich": ["rich"],
    },
    tests_require=["datasette[test]"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Framework :: Datasette",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Database",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.6",
    ],
)
