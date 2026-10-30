cmake_minimum_required(VERSION 3.10)
project(audio-zone-manager C)

set(CMAKE_C_STANDARD 11)

find_path(CJSON_INCLUDE_DIR cjson/cJSON.h)
find_library(CJSON_LIBRARY cjson)

add_executable(audio-zone-manager main.c)
target_include_directories(audio-zone-manager PRIVATE ${CJSON_INCLUDE_DIR})
target_link_libraries(audio-zone-manager PRIVATE ${CJSON_LIBRARY})

install(TARGETS audio-zone-manager RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
