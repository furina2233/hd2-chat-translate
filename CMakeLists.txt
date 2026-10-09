cmake_minimum_required(VERSION 3.20)

if(NOT CMAKE_HOST_WIN32)
    message(FATAL_ERROR "本项目的 CMake 原生目标仅支持 Windows x64 MinGW-w64 GCC。")
endif()

project(hd2_chat_translate_native LANGUAGES C)

if(NOT WIN32)
    message(FATAL_ERROR "本项目的 CMake 原生目标仅支持 Windows x64 MinGW-w64 GCC。")
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "本项目要求 64 位 Windows 工具链；当前 C 编译器不是 x64。")
endif()

if(NOT MINGW OR NOT CMAKE_C_COMPILER_ID STREQUAL "GNU")
    message(FATAL_ERROR "本项目仅支持 Windows x64 MinGW-w64 GCC；MSVC、Clang 和其他编译器不受支持。")
endif()

execute_process(
    COMMAND "${CMAKE_C_COMPILER}" -dumpmachine
    RESULT_VARIABLE HD2CT_COMPILER_TARGET_RESULT
    OUTPUT_VARIABLE HD2CT_COMPILER_TARGET
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_VARIABLE HD2CT_COMPILER_TARGET_ERROR
)
if(NOT HD2CT_COMPILER_TARGET_RESULT EQUAL 0 OR
   NOT HD2CT_COMPILER_TARGET MATCHES "^(x86_64|amd64)-w64-mingw32")
    message(FATAL_ERROR "C 编译器必须以 x86_64-w64-mingw32 为目标；检测结果：${HD2CT_COMPILER_TARGET} ${HD2CT_COMPILER_TARGET_ERROR}")
endif()

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)

set(HD2CT_TOOLS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tools")
set(HD2CT_NATIVE_HELPER "${HD2CT_TOOLS_DIR}/cmake_native.py")
set(HD2CT_NATIVE_BUILDER "${HD2CT_TOOLS_DIR}/build_native_http.py")
set(HD2CT_LANGUAGE_GENERATOR "${HD2CT_TOOLS_DIR}/target_languages.py")
set(HD2CT_LANGUAGE_CATALOGUE "${CMAKE_CURRENT_SOURCE_DIR}/resources/target_languages.json")
set(HD2CT_NATIVE_OUTPUT_DIR "${CMAKE_CURRENT_BINARY_DIR}/native")
set(HD2CT_GENERATED_HEADER "${HD2CT_NATIVE_OUTPUT_DIR}/target_languages.generated.h")
set(HD2CT_NATIVE_META "${HD2CT_NATIVE_OUTPUT_DIR}/hd2ct_http.meta.json")

file(MAKE_DIRECTORY "${HD2CT_NATIVE_OUTPUT_DIR}")

# 配置阶段先生成头文件，供 CLion 在首次构建前索引。
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${HD2CT_NATIVE_HELPER}"
            generate-header --catalogue "${HD2CT_LANGUAGE_CATALOGUE}"
            --output-dir "${HD2CT_NATIVE_OUTPUT_DIR}"
    RESULT_VARIABLE HD2CT_HEADER_RESULT
    OUTPUT_VARIABLE HD2CT_HEADER_OUTPUT
    ERROR_VARIABLE HD2CT_HEADER_ERROR
)
if(NOT HD2CT_HEADER_RESULT EQUAL 0)
    message(FATAL_ERROR "生成目标语言 C 头文件失败：${HD2CT_HEADER_OUTPUT}${HD2CT_HEADER_ERROR}")
endif()

# 从既有 Python 构建器读取源码清单，避免 CMake 与命令行构建清单漂移。
set(HD2CT_SOURCE_MANIFEST "${CMAKE_CURRENT_BINARY_DIR}/hd2ct_native_sources.cmake")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${HD2CT_NATIVE_HELPER}"
            write-source-manifest --output "${HD2CT_SOURCE_MANIFEST}"
    RESULT_VARIABLE HD2CT_MANIFEST_RESULT
    OUTPUT_VARIABLE HD2CT_MANIFEST_OUTPUT
    ERROR_VARIABLE HD2CT_MANIFEST_ERROR
)
if(NOT HD2CT_MANIFEST_RESULT EQUAL 0)
    message(FATAL_ERROR "生成原生源码清单失败：${HD2CT_MANIFEST_OUTPUT}${HD2CT_MANIFEST_ERROR}")
endif()
include("${HD2CT_SOURCE_MANIFEST}")

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${HD2CT_NATIVE_HELPER}"
    "${HD2CT_NATIVE_BUILDER}"
    "${HD2CT_LANGUAGE_GENERATOR}"
    "${HD2CT_LANGUAGE_CATALOGUE}"
)

add_custom_command(
    OUTPUT "${HD2CT_GENERATED_HEADER}"
    COMMAND "${Python3_EXECUTABLE}" "${HD2CT_NATIVE_HELPER}"
            generate-header --catalogue "${HD2CT_LANGUAGE_CATALOGUE}"
            --output-dir "${HD2CT_NATIVE_OUTPUT_DIR}"
    DEPENDS
        "${HD2CT_NATIVE_HELPER}"
        "${HD2CT_LANGUAGE_GENERATOR}"
        "${HD2CT_LANGUAGE_CATALOGUE}"
    COMMENT "生成原生客户端目标语言头文件"
    VERBATIM
)

add_library(hd2ct_http_binary SHARED
    ${HD2CT_NATIVE_SOURCES}
    ${HD2CT_NATIVE_HEADERS}
    "${HD2CT_GENERATED_HEADER}"
)

set_target_properties(hd2ct_http_binary PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED YES
    C_EXTENSIONS NO
    PREFIX ""
    SUFFIX ".dll"
    OUTPUT_NAME "hd2ct_http"
    RUNTIME_OUTPUT_DIRECTORY "${HD2CT_NATIVE_OUTPUT_DIR}"
    ARCHIVE_OUTPUT_DIRECTORY "${HD2CT_NATIVE_OUTPUT_DIR}"
)

target_include_directories(hd2ct_http_binary PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/native"
    "${HD2CT_NATIVE_OUTPUT_DIR}"
)

target_compile_definitions(hd2ct_http_binary PRIVATE
    _WIN32_WINNT=0x0601
    WINVER=0x0601
    CJSON_NESTING_LIMIT=32
    CJSON_HIDE_SYMBOLS
)

# 目标专属选项放在配置默认选项之后；Debug 也固定优化并禁止调试信息。
target_compile_options(hd2ct_http_binary PRIVATE
    -Os
    -finput-charset=UTF-8
    -fexec-charset=UTF-8
    "$<$<CONFIG:Debug>:-g0>"
)

target_link_options(hd2ct_http_binary PRIVATE
    -s
    -static-libgcc
    -Wl,--exclude-all-symbols
)

target_link_libraries(hd2ct_http_binary PRIVATE
    winhttp
    advapi32
    bcrypt
)

get_filename_component(HD2CT_GCC_DIR "${CMAKE_C_COMPILER}" DIRECTORY)
find_program(HD2CT_OBJDUMP
    NAMES objdump x86_64-w64-mingw32-objdump
    HINTS "${HD2CT_GCC_DIR}"
)
if(NOT HD2CT_OBJDUMP)
    message(FATAL_ERROR "找不到与 MinGW GCC 配套的 objdump；请使用同一工具链安装或将其加入 PATH。")
endif()

# 每次构建先清除旧 meta，失败时打包器不会接受上一次 DLL 的 metadata。
add_custom_target(hd2ct_http_prepare
    COMMAND "${Python3_EXECUTABLE}" "${HD2CT_NATIVE_HELPER}"
            remove-meta --metadata "${HD2CT_NATIVE_META}"
    COMMENT "清除旧的原生 DLL metadata"
    VERBATIM
)
add_dependencies(hd2ct_http_binary hd2ct_http_prepare)

# 此目标每次构建都重验 DLL 并原子更新 metadata，不会调用 GCC。
add_custom_target(hd2ct_http_metadata
    BYPRODUCTS "${HD2CT_NATIVE_META}"
    COMMAND "${Python3_EXECUTABLE}" "${HD2CT_NATIVE_HELPER}"
            write-meta
            --dll "$<TARGET_FILE:hd2ct_http_binary>"
            --metadata "${HD2CT_NATIVE_META}"
            --compiler "${CMAKE_C_COMPILER}"
            --objdump "${HD2CT_OBJDUMP}"
    COMMENT "核验原生 DLL 并生成 metadata"
    VERBATIM
)
add_dependencies(hd2ct_http_metadata hd2ct_http_binary)

# CLion 默认及显式选择此目标时都会构建 DLL 和配套 metadata。
add_custom_target(hd2ct_http ALL)
add_dependencies(hd2ct_http hd2ct_http_metadata)
