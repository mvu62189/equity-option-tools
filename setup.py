from __future__ import annotations

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import find_packages, setup

ext_modules = [
    Pybind11Extension(
        "quantcore",
        [
            "cpp/src/module.cpp",
            "cpp/src/bs2002.cpp",
            "cpp/src/laplace.cpp",
            "cpp/src/ssvi.cpp",
            "cpp/src/fdm_cn_log.cpp",
        ],
        include_dirs=["cpp/include"],
        cxx_std=20,
    )
]

setup(
    packages=find_packages(where="python"),
    package_dir={"": "python"},
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
