from glob import glob
from setuptools import find_packages, setup


package_name = 'motion_coordination'


setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/deploy', glob('deploy/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='joonho_test',
    maintainer_email='joonho_test@example.com',
    description='DDS group motion coordination and local execution adapter.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'motion_coordination_node = motion_coordination.coordination_node:main',
        ],
    },
)
