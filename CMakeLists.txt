cmake_minimum_required(VERSION 3.22)
project(piano LANGUAGES C CXX)   # C (and OBJC/OBJCXX on macOS) for the plugin build
if(APPLE)
  enable_language(OBJC OBJCXX)
endif()

add_subdirectory(synthkit)
include(CTest)

synthkit_add_synth(piano SOURCES src/Synth.cpp)

# The partial bank is written to vectorise: let the browser build use WebAssembly SIMD
# (every current browser has it).
if(SYNTHKIT_WASM)
  target_compile_options(piano_synth PRIVATE -msimd128)
endif()
