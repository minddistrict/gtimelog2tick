from setuptools import setup

setup(
    name="gtimelog2tick",
    version='0.2',
    description="Create entries in tickspot's tick from Gtimelog journal.",
    license='GPL',
    py_modules=['gtimelog2tick'],
    install_requires=[
        'requests',
        'keyring',
    ],
    entry_points={
        'console_scripts': [
            'gtimelog2tick=gtimelog2tick:main',
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires='>= 3.11',
)
