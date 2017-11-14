from setuptools import setup, find_packages

setup(
    name='datasette',
    description='An instant JSON API for your SQLite databases',
    author='Simon Willison',
    version='0.11',
    license='Apache License, Version 2.0',
    url='https://github.com/simonw/datasette',
    packages=find_packages(),
    package_data={'datasette': ['templates/*.html']},
    include_package_data=True,
    install_requires=[
        'click==6.7',
        'click-default-group==1.2',
        'sanic==0.6.0',
        'sanic-jinja2==0.5.5',
        'hupper==1.0',
    ],
    entry_points='''
        [console_scripts]
        datasette=datasette.cli:cli
    ''',
    setup_requires=['pytest-runner'],
    tests_require=[
        'pytest==3.2.3',
        'aiohttp==2.3.2',
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
