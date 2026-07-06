from glob import glob
from setuptools import find_packages, setup

package_name = 'motion_web_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='joonho_test',
    maintainer_email='joonho_test@example.com',
    description='FastAPI bridge between ROS2 motion state and the web UI.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'motion_web_bridge = motion_web_bridge.bridge_node:main',
            'motion_mapping_manager = motion_web_bridge.motion_mapping_manager:main',
            'motion_run_manager = motion_web_bridge.motion_run_manager:main',
        ],
    },
)
