from setuptools import setup, find_packages

setup(
    name='experisana',
    version='0.1',
    packages=find_packages(),
    install_requires=[
        'fire',
        'asana',
        'python-dotenv',
        'requests',
        'backoff',
        'PyYAML'
    ],
    entry_points={
        'console_scripts': [
            'experisana=experisana.cli:main',
        ],
    },
)