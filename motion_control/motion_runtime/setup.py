from setuptools import find_packages, setup


package_name = 'motion_runtime'


setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='joonho_test',
    maintainer_email='joonho_test@example.com',
    description='Motion mapping, validation, and runtime execution orchestration.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'motion_mapping_manager = motion_runtime.motion_mapping_manager:main',
            'motion_run_manager = motion_runtime.motion_run_manager:main',
        ],
    },
)
