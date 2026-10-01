cmake_minimum_required(VERSION 3.14)
project(eventbus CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
option(BUS_WITH_METRICS "count published events" ON)

add_library(eventbus src/bus.cpp src/handlers/log_handler.cpp src/handlers/counter_handler.cpp)
target_include_directories(eventbus PUBLIC include)
if(BUS_WITH_METRICS)
  target_compile_definitions(eventbus PUBLIC BUS_WITH_METRICS=1)
endif()

add_executable(busd app/main.cpp)
target_link_libraries(busd eventbus)

enable_testing()
add_executable(bus_test tests/bus_test.cpp)
target_link_libraries(bus_test eventbus)
add_test(NAME bus COMMAND bus_test)
