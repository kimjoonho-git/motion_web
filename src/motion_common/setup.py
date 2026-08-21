from setuptools import find_packages, setup

package_name = 'motion_common'

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
    description='공용 커널 · 경로 해석 · 모션 표 파서 · rclpy 비의존 순수 모듈',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [],
    },
)
