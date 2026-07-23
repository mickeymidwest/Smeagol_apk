cmake_minimum_required(VERSION 3.22.1)

project("gremlin_llama" LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED true)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED true)

# Lean, statically-linked build -- GGML_BACKEND_DL stays off (its default),
# so the CPU backend is built directly into libgremlin_llama.so with
# nothing to load from a separate path at runtime. A ~1B-parameter Q4
# model doesn't need GPU-delegate complexity to be usable on a phone CPU.
# Skip everything upstream only needs for its own CLI tools/tests/server --
# add_subdirectory() here is not a standalone build (LLAMA_STANDALONE is
# OFF), so LLAMA_BUILD_COMMON must be forced on explicitly or the common
# helper library (tokenization, chat templates, sampling) never builds.
set(LLAMA_BUILD_COMMON   ON  CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS    OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS    OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER   OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP      OFF CACHE BOOL "" FORCE)

# android/llama.cpp is a git submodule (pinned to release b10091) -- see
# android/README.md for why it's vendored this way instead of fetched via
# Gradle: llama.cpp has no published Maven artifact, only its own CMake
# project, so building it from source via add_subdirectory() is the
# actual supported integration path (same one used by llama.cpp's own
# examples/llama.android reference app, which this was adapted from).
set(LLAMA_SRC ${CMAKE_CURRENT_LIST_DIR}/../../../../llama.cpp)
add_subdirectory(${LLAMA_SRC} build-llama)

add_library(gremlin_llama SHARED
        gremlin_llama.cpp)

target_include_directories(gremlin_llama PRIVATE
        ${LLAMA_SRC}
        ${LLAMA_SRC}/common
        ${LLAMA_SRC}/include
        ${LLAMA_SRC}/ggml/include)

target_link_libraries(gremlin_llama
        llama
        llama-common
        android
        log)
