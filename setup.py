from setuptools import setup, find_packages
from datasette import __version__

setup(
    name='datasette',
    description='An instant JSON API for your SQLite databases',
    author='Simon Willison',
    version=__version__,
    license='Apache License, Version 2.0',
    url='https://github.com/simonw/datasette',
    packages=find_packages(),
    package_data={'datasette': ['templates/*.html']},
    include_package_data=True,
    install_requires=[
        'click==6.7',
        'click-default-group==1.2',
        'Sanic==0.7.0',
        'Jinja2==2.10',
        'hupper==1.0',
    ],
    entry_points='''
        [console_scripts]
        datasette=datasette.cli:cli
    ''',
    setup_requires=['pytest-runner'],
    tests_require=[
        'pytest==3.2.1',
        'aiohttp==2.3.2',
        'beautifulsoup4==4.6.0',
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Intended Audience :: End Users/Desktop',
        'Topic :: Database',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.6',
    ],
)
