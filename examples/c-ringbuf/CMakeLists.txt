cmake_minimum_required(VERSION 3.13)
project(ringbuf C)

option(RB_THREADSAFE "guard buffers with a mutex" OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

add_library(ringbuf src/ringbuf.c src/log.c)
target_include_directories(ringbuf PUBLIC include)
if(RB_THREADSAFE)
  target_compile_definitions(ringbuf PUBLIC RB_THREADSAFE=1)
  find_package(Threads REQUIRED)
  target_link_libraries(ringbuf PUBLIC Threads::Threads)
endif()

add_executable(rbtool tools/rbtool.c)
target_link_libraries(rbtool ringbuf)

enable_testing()
add_executable(test_ringbuf tests/test_ringbuf.c)
target_link_libraries(test_ringbuf ringbuf)
add_test(NAME ringbuf COMMAND test_ringbuf)
