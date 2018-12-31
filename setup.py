from setuptools import setup, find_packages
import os

import versioneer


def get_long_description():
    with open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'README.md'
    ), encoding='utf8') as fp:
        return fp.read()


def get_version():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'datasette', 'version.py'
    )
    g = {}
    exec(open(path).read(), g)
    return g['__version__']


setup(
    name='datasette',
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    description='An instant JSON API for your SQLite databases',
    long_description=get_long_description(),
    long_description_content_type='text/markdown',
    author='Simon Willison',
    license='Apache License, Version 2.0',
    url='https://github.com/simonw/datasette',
    packages=find_packages(),
    package_data={'datasette': ['templates/*.html']},
    include_package_data=True,
    install_requires=[
        'click==6.7',
        'click-default-group==1.2',
        'sanic==18.12.0',
        'Jinja2==2.10',
        'hupper==1.0',
        'pint==0.8.1',
        'pluggy>=0.7.1',
    ],
    entry_points='''
        [console_scripts]
        datasette=datasette.cli:cli
    ''',
    setup_requires=['pytest-runner'],
    extras_require={
        'test': [
            'pytest==4.0.2',
            'aiohttp==3.3.2',
            'beautifulsoup4==4.6.1',
        ]
    },
    tests_require=[
        'datasette[test]',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Intended Audience :: End Users/Desktop',
        'Topic :: Database',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.5',
    ],
)
