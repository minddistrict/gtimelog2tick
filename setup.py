from setuptools import setup

with open('README.rst') as f:
    readme = f.read()
with open('CHANGES.rst') as f:
    changes = f.read()

setup(
    name="gtimelog2tick",
    version='0.2',
    description="Create entries in tickspot's tick from Gtimelog journal.",
    long_description='\n\n'.join([readme, changes]),
    license='GPL',
    author='Michael Howitz',
    author_email='icemac@gmx.net',
    keywords='gtimelog, tick, upload',
    url='https://github.com/minddistrict/gtimelog2tick',
    py_modules=['gtimelog2tick'],
    install_requires=[
        'requests',
    ],
    entry_points={
        'console_scripts': [
            'gtimelog2tick=gtimelog2tick:main',
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU General Public License (GPL)",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires='>= 3.11',
)
