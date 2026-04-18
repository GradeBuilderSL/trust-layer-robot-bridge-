cmake_minimum_required(VERSION 3.16)
project(e1_dds_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED E1_SDK_ROOT)
  if(DEFINED ENV{E1_SDK_ROOT})
    set(E1_SDK_ROOT "$ENV{E1_SDK_ROOT}")
  else()
    set(E1_SDK_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../SDK/noetix_sdk_e1")
  endif()
endif()

if(NOT EXISTS "${E1_SDK_ROOT}/include/common.h")
  message(FATAL_ERROR "E1 SDK not found at ${E1_SDK_ROOT}. Set E1_SDK_ROOT.")
endif()

find_package(Threads REQUIRED)

if(CMAKE_SYSTEM_PROCESSOR MATCHES "arm.*|arm|aarch64")
  set(E1_SDK_LIB_ARCH "aarch64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "(x86_64)|(AMD64)|(^i686)")
  set(E1_SDK_LIB_ARCH "x86_64")
else()
  message(FATAL_ERROR "Unsupported architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

set(E1_SDK_LIB_DIR "${E1_SDK_ROOT}/lib/${E1_SDK_LIB_ARCH}")

add_executable(e1_dds_bridge e1_dds_bridge.cpp)
target_include_directories(
  e1_dds_bridge
  PRIVATE
    "${E1_SDK_ROOT}/include"
    "${E1_SDK_ROOT}/include/idl"
)

target_link_directories(e1_dds_bridge PRIVATE "${E1_SDK_LIB_DIR}")
target_link_libraries(
  e1_dds_bridge
  PRIVATE
    Threads::Threads
    "${E1_SDK_LIB_DIR}/libdriver.a"
    "${E1_SDK_LIB_DIR}/libddsc.so"
    "${E1_SDK_LIB_DIR}/libddscxx.so"
)

if(UNIX)
  set_target_properties(
    e1_dds_bridge
    PROPERTIES
      BUILD_RPATH "${E1_SDK_LIB_DIR}"
      INSTALL_RPATH "${E1_SDK_LIB_DIR}"
  )
endif()
