cmake_minimum_required(VERSION 3.27)
project(quantcore LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(
  quantcore
  src/module.cpp
  src/bs2002.cpp
  src/laplace.cpp
  src/ssvi.cpp
  src/fdm_cn_log.cpp
)
target_include_directories(quantcore PRIVATE include)
